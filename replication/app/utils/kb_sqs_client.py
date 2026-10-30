"""
KB SQS client for diagrams-v2 — deployed environment mode.

Communicates with the KB microservice via SQS using AIRequestMessageDto format.
Used when APP_KB_SERVICE_ENABLED=true (takes precedence over local Weaviate).

Ported from v1 app/services/kb_sqs_client.py with v2 config conventions.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Optional

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_kb_client: Optional["KBSQSClient"] = None


class KBSQSClient:
    """
    Sends ingestion / deletion requests to the KB microservice via SQS.

    The KB service always uses real AWS (not LocalStack), so this client
    creates its own boto3 session regardless of APP_LOCAL_ENVIRONMENT.
    """

    def __init__(self) -> None:
        import boto3

        self._sqs = boto3.client("sqs", region_name=settings.aws_region)
        self._request_url: str | None = None
        self._response_url: str | None = None
        self._init_queues()

    def _init_queues(self) -> None:
        try:
            self._request_url = self._sqs.get_queue_url(
                QueueName=settings.kb_request_queue_name
            )["QueueUrl"]
            self._response_url = self._sqs.get_queue_url(
                QueueName=settings.kb_response_queue_name
            )["QueueUrl"]
            logger.info(
                f"KBSQSClient: queues ready — "
                f"req={settings.kb_request_queue_name}, "
                f"resp={settings.kb_response_queue_name}"
            )
        except Exception as exc:
            logger.error(f"KBSQSClient: failed to get queue URLs: {exc}")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def upload_file(
        self,
        project_id: str,
        file_location: str,
        file_name: str | None = None,
        location_type: str | None = None,
        extract_images: bool = False,
        wait_for_response: bool = True,
        timeout: float | None = None,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Send an upload_file operation to the KB service."""
        data: dict[str, Any] = {
            "file_location": file_location,
            "extract_images": extract_images,
        }
        if file_name:
            data["file_name"] = file_name
        if location_type:
            data["location_type"] = location_type

        self._send(
            operation="upload_file",
            project_id=project_id,
            data=data,
            user_id=user_id,
            organization_id=organization_id,
        )

        if wait_for_response:
            return await self._wait(
                project_id=project_id,
                operation="upload_file",
                timeout=timeout or settings.kb_response_timeout,
            )
        return None

    async def delete_file(
        self,
        project_id: str,
        file_path: str,
        file_name: str | None = None,
        file_id: str | None = None,
        wait_for_response: bool = False,
        timeout: float = 60.0,
    ) -> dict[str, Any] | None:
        """Send a delete_file operation to the KB service."""
        data: dict[str, Any] = {"file_path": file_path}
        if file_name:
            data["fileName"] = file_name
        if file_id:
            data["id"] = file_id

        self._send(operation="delete_file", project_id=project_id, data=data)

        if wait_for_response:
            return await self._wait(
                project_id=project_id,
                operation="delete_file",
                timeout=timeout,
            )
        return None

    async def ingest_dependency(
        self,
        project_id: str,
        dependency_data: dict[str, Any],
        wait_for_response: bool = True,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """Send an ingest_dependency operation to the KB service."""
        self._send(
            operation="ingest_dependency",
            project_id=project_id,
            data=dependency_data,
        )
        if wait_for_response:
            return await self._wait(
                project_id=project_id,
                operation="ingest_dependency",
                timeout=timeout or settings.kb_response_timeout,
            )
        return None

    async def delete_dependency(
        self,
        project_id: str,
        dependency_name: str,
        dependency_id: str,
        wait_for_response: bool = True,
        timeout: float = 60.0,
    ) -> dict[str, Any] | None:
        """Send a delete_dependency operation to the KB service."""
        data = {
            "dependency_name": dependency_name,
            "dependency_id": dependency_id,
        }
        self._send(operation="delete_dependency", project_id=project_id, data=data)
        if wait_for_response:
            return await self._wait(
                project_id=project_id,
                operation="delete_dependency",
                timeout=timeout,
            )
        return None

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _send(
        self,
        *,
        operation: str,
        project_id: str,
        data: dict[str, Any],
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        if not self._request_url:
            raise RuntimeError("KB request queue URL is not available")

        message: dict[str, Any] = {
            "projectId": project_id,
            "data": data,
            "operation": operation,
            "timestamp": int(datetime.now().timestamp() * 1000),
            "inferenceServer": "ON_PREM",
        }
        if user_id:
            message["userId"] = user_id
        if organization_id:
            message["organizationId"] = organization_id

        try:
            resp = self._sqs.send_message(
                QueueUrl=self._request_url,
                MessageBody=json.dumps(message),
            )
            logger.info(
                f"KBSQSClient: sent {operation} for project {project_id} "
                f"(MessageId={resp['MessageId']})"
            )
        except Exception as exc:
            logger.error(f"KBSQSClient: failed to send {operation}: {exc}")
            raise

    async def _wait(
        self,
        *,
        project_id: str,
        operation: str,
        timeout: float,
        poll_interval: float = 2.0,
    ) -> dict[str, Any] | None:
        if not self._response_url:
            logger.error("KBSQSClient: response queue URL not available")
            return None

        elapsed = 0.0
        while elapsed < timeout:
            try:
                resp = self._sqs.receive_message(
                    QueueUrl=self._response_url,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=min(int(poll_interval), 20),
                )
                for msg in resp.get("Messages", []):
                    try:
                        body = json.loads(msg["Body"])
                        pid = body.get("projectId") or body.get("project_id")
                        if pid == project_id and body.get("operation") == operation:
                            self._sqs.delete_message(
                                QueueUrl=self._response_url,
                                ReceiptHandle=msg["ReceiptHandle"],
                            )
                            logger.info(
                                f"KBSQSClient: received {operation} response for {project_id}"
                            )
                            return body
                    except json.JSONDecodeError:
                        continue
            except Exception as exc:
                logger.error(f"KBSQSClient: error polling response queue: {exc}")

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning(
            f"KBSQSClient: timeout waiting for {operation} response "
            f"(project={project_id}, timeout={timeout}s)"
        )
        return None


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


def get_kb_sqs_client() -> KBSQSClient:
    """Return the singleton KBSQSClient, creating it on first call."""
    global _kb_client
    if _kb_client is None:
        _kb_client = KBSQSClient()
    return _kb_client


def is_kb_enabled() -> bool:
    """Return True if the KB service is configured and enabled."""
    return settings.kb_service_enabled
