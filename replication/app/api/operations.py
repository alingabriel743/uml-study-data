"""
Operations proxy endpoint for diagrams-v2.

SQS bridge for the frontend — same pattern as atonis-req-copilot.
Frontend sends an operation request; the API dispatches it to the SQS
request queue, polls the response queue for the matching result, and
returns it synchronously.

This removes the need for the frontend to poll GET /projects every 5s
and ensures the frontend gets the actual operation result back.
"""

import asyncio
import hashlib
import json
import time as _time
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/ai")


# In-memory cache for async operation results.
# Key: request_id, Value: {"result": dict, "timestamp": float}
_async_results: dict[str, dict] = {}
_ASYNC_RESULT_TTL = 600  # seconds — auto-evict after 10 minutes


class OperationRequest(BaseModel):
    """Request for SQS-proxied operations from the frontend."""
    operation: str
    projectId: str
    data: Optional[dict] = None
    async_mode: bool = False  # When True, return immediately with request_id


class OperationResponse(BaseModel):
    """Response from SQS-proxied operation."""
    status: str
    data: Optional[dict] = None
    error: Optional[str] = None
    operation: Optional[str] = None
    projectId: Optional[str] = None
    request_id: Optional[str] = None


@router.post("/operations", response_model=OperationResponse)
async def proxy_operation(request: OperationRequest):
    """
    SQS proxy endpoint for frontend clients.

    Accepts an operation request, sends it to the SQS request queue,
    polls the response queue, and returns the result.
    Uses a unique requestId (correlation ID) so that parallel operations
    for the same project never steal each other's responses.

    Same pattern as atonis-req-copilot POST /api/v1/operations.
    """
    from app.utils.sqs_client import get_sqs_client, get_queue_url, send_sqs_message

    request_id = str(uuid4())
    logger.info(f"Operations proxy: {request.operation} for project {request.projectId} [requestId={request_id}]")

    try:
        # Fresh SQS client per call — no caching, no contention with the
        # worker's long-polling client (matches req-copilot pattern).
        sqs_client = get_sqs_client()
        request_queue_url = get_queue_url(settings.sqs_request_queue_name)
        response_queue_url = get_queue_url(settings.sqs_response_queue_name)

        # Build and send message with correlation ID
        timestamp_ms = int(_time.time() * 1000)
        message = {
            "operation": request.operation,
            "projectId": request.projectId,
            "timestamp": timestamp_ms,
            "data": request.data or {},
            "request_id": request_id,
            "job_id": request_id,
        }
        group_id = f"{request.projectId}-{request.operation}"
        dedup_id = hashlib.sha256(
            f"{request.projectId}-{request.operation}-{timestamp_ms}-{request_id}".encode()
        ).hexdigest()

        # Send to SQS — synchronous call (fast, ~ms), NOT in to_thread.
        # Matches req-copilot pattern: only receive_message uses to_thread.
        send_sqs_message(
            sqs_client,
            queue_url=request_queue_url,
            message_body=json.dumps(message),
            message_group_id=group_id,
            message_deduplication_id=dedup_id,
        )
        logger.info(f"SQS message sent: {request.operation} [requestId={request_id}]")

        # Async mode: return immediately with the request_id for polling
        if request.async_mode:
            logger.info(f"Async mode: returning immediately [requestId={request_id}]")
            return OperationResponse(
                status="ACCEPTED",
                operation=request.operation,
                projectId=request.projectId,
                request_id=request_id,
            )

        # Synchronous mode: poll for response (up to 180 seconds).
        max_wait = 180
        start = _time.time()
        # FIFO queues everywhere: WaitTimeSeconds=5 long-poll returns
        # immediately when a message arrives (matches req-copilot).
        poll_wait = 5

        while _time.time() - start < max_wait:
            resp = await asyncio.to_thread(
                sqs_client.receive_message,
                QueueUrl=response_queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=poll_wait,
            )
            msgs = resp.get("Messages", [])
            for msg in msgs:
                receipt_handle = msg["ReceiptHandle"]
                body = json.loads(msg["Body"])

                # Match by correlation ID
                if body.get("request_id") == request_id:
                    # This response is ours — delete and return
                    await asyncio.to_thread(
                        sqs_client.delete_message,
                        QueueUrl=response_queue_url,
                        ReceiptHandle=receipt_handle,
                    )
                    logger.info(f"Operations proxy response received: {body.get('status')} [requestId={request_id}]")
                    return OperationResponse(
                        status=body.get("status", "OK"),
                        data=body.get("data"),
                        error=body.get("error"),
                        operation=body.get("operation"),
                        projectId=body.get("projectId"),
                    )

                # Not our response.
                msg_ts = body.get("timestamp", 0)
                is_stale = msg_ts < timestamp_ms
                has_no_request_id = not body.get("request_id")

                if is_stale or has_no_request_id:
                    # Old orphan or pre-requestId message — delete to unblock
                    try:
                        await asyncio.to_thread(
                            sqs_client.delete_message,
                            QueueUrl=response_queue_url,
                            ReceiptHandle=receipt_handle,
                        )
                        logger.debug(f"Deleted stale/orphan response (ts={msg_ts}, op={body.get('operation')})")
                    except Exception:
                        pass
                else:
                    # Fresh message belonging to another in-flight request —
                    # cache it for that poller, then release visibility.
                    other_id = body.get("request_id")
                    if other_id:
                        _async_results[other_id] = {
                            "result": OperationResponse(
                                status=body.get("status", "OK"),
                                data=body.get("data"),
                                error=body.get("error"),
                                operation=body.get("operation"),
                                projectId=body.get("projectId"),
                                request_id=other_id,
                            ),
                            "timestamp": _time.time(),
                        }
                    try:
                        await asyncio.to_thread(
                            sqs_client.change_message_visibility,
                            QueueUrl=response_queue_url,
                            ReceiptHandle=receipt_handle,
                            VisibilityTimeout=0,
                        )
                    except Exception:
                        pass

            await asyncio.sleep(1)

        # Timeout
        logger.warning(f"Operations proxy timed out for {request.operation} [requestId={request_id}]")
        return OperationResponse(
            status="ERROR",
            error=f"Operation timed out after {max_wait}s",
            operation=request.operation,
            projectId=request.projectId,
        )

    except Exception as e:
        logger.error(f"Operations proxy error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Operation failed: {str(e)}")


@router.get("/operations/{request_id}/status", response_model=OperationResponse)
async def poll_operation_status(request_id: str):
    """
    Poll for the result of an async operation.

    1. Check the in-memory cache first (previous poll may have stored it).
    2. If not cached, do a single short SQS long-poll (5s).
    3. Return the result when found, or {"status": "PENDING"} otherwise.
    """
    from app.utils.sqs_client import get_sqs_client, get_queue_url

    # --- Evict stale cache entries (lazy cleanup) ---
    now = _time.time()
    stale_keys = [k for k, v in _async_results.items() if now - v["timestamp"] > _ASYNC_RESULT_TTL]
    for k in stale_keys:
        _async_results.pop(k, None)

    # --- 1. Check cache ---
    cached = _async_results.pop(request_id, None)
    if cached:
        logger.info(f"Async poll: returning cached result [requestId={request_id}]")
        return cached["result"]

    # --- 2. Single short SQS poll ---
    try:
        sqs_client = get_sqs_client()
        response_queue_url = get_queue_url(settings.sqs_response_queue_name)

        resp = await asyncio.to_thread(
            sqs_client.receive_message,
            QueueUrl=response_queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=5,
        )

        for msg in resp.get("Messages", []):
            receipt_handle = msg["ReceiptHandle"]
            body = json.loads(msg["Body"])

            if body.get("request_id") == request_id:
                # Our response — delete from queue and return
                await asyncio.to_thread(
                    sqs_client.delete_message,
                    QueueUrl=response_queue_url,
                    ReceiptHandle=receipt_handle,
                )
                logger.info(f"Async poll: found SQS result [requestId={request_id}]")
                return OperationResponse(
                    status=body.get("status", "OK"),
                    data=body.get("data"),
                    error=body.get("error"),
                    operation=body.get("operation"),
                    projectId=body.get("projectId"),
                    request_id=request_id,
                )

            # Not ours — apply stale/release logic
            has_no_request_id = not body.get("request_id")

            if has_no_request_id:
                try:
                    await asyncio.to_thread(
                        sqs_client.delete_message,
                        QueueUrl=response_queue_url,
                        ReceiptHandle=receipt_handle,
                    )
                    logger.debug(f"Async poll: deleted orphan response (op={body.get('operation')})")
                except Exception:
                    pass
            else:
                # Belongs to another request — cache it for that poller
                other_id = body.get("request_id")
                _async_results[other_id] = {
                    "result": OperationResponse(
                        status=body.get("status", "OK"),
                        data=body.get("data"),
                        error=body.get("error"),
                        operation=body.get("operation"),
                        projectId=body.get("projectId"),
                        request_id=other_id,
                    ),
                    "timestamp": _time.time(),
                }
                try:
                    await asyncio.to_thread(
                        sqs_client.change_message_visibility,
                        QueueUrl=response_queue_url,
                        ReceiptHandle=receipt_handle,
                        VisibilityTimeout=0,
                    )
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Async poll SQS error: {e}", exc_info=True)

    # --- 3. Nothing found yet ---
    return OperationResponse(status="PENDING", request_id=request_id)
