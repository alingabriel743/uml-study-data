"""
Document ingestion API — diagrams-v2.

Supports two modes (same pattern as diagram_generation.py):
  SYNC  (default, APP_SQS_ENABLED=false): runs DocumentIngestionService directly.
  ASYNC (APP_SQS_ENABLED=true):           sends job to SQS, returns job_id + status="queued".

Endpoints:
  POST   /ai/ingest_documents/{project_id}
  GET    /ai/documents/{project_id}
  DELETE /ai/documents/{project_id}/{file_name}
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path as FilePath
from typing import Any, List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path as PathParam, Query
from pydantic import BaseModel, Field

from app.services.document_ingestion_service import (
    DocumentIngestionService,
    SUPPORTED_EXTENSIONS,
)
from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/ai")


# --------------------------------------------------------------------------- #
# Request model                                                                #
# --------------------------------------------------------------------------- #

class DocumentIngestionRequest(BaseModel):
    """Request body for POST /ai/ingest_documents/{project_id}."""

    target_files: List[str] = Field(
        default=[],
        description="File paths already on disk to ingest",
    )
    file_location: Optional[str] = Field(
        None,
        description="S3 path or URL (required for KB service mode)",
    )
    file_name: Optional[str] = Field(None, description="Original filename")
    extract_images: bool = Field(False, description="Extract images from document")
    user_id: Optional[str] = Field(None, description="User ID (forwarded to KB service)")
    organization_id: Optional[str] = Field(None, description="Organisation ID")


# --------------------------------------------------------------------------- #
# SQS helpers                                                                  #
# --------------------------------------------------------------------------- #

def _send_to_sqs(job_id: str, project_id: str, operation: str, data: dict) -> None:
    from app.utils.sqs_client import get_sqs_client, get_queue_url
    from app.models.sqs_models import AIRequestMessageDto, AIOperationType

    client = get_sqs_client()
    queue_url = get_queue_url(settings.sqs_request_queue_name)

    msg = AIRequestMessageDto(
        projectId=project_id,
        operation=AIOperationType(operation),
        data=data,
        timestamp=int(time.time() * 1000),
        job_id=job_id,
        request_id=job_id,
    )

    dedup_id = hashlib.sha256(f"{job_id}-{int(time.time()*1000)}".encode()).hexdigest()

    from app.utils.sqs_client import send_sqs_message
    send_sqs_message(
        client,
        queue_url=queue_url,
        message_body=msg.model_dump_json(),
        message_group_id=job_id,
        message_deduplication_id=dedup_id,
    )
    logger.info(f"ingest_documents: job {job_id} enqueued")


def _write_job_queued(project_id: str, job_id: str, operation: str, data: dict) -> None:
    jobs_dir = FilePath("data") / project_id / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    with (jobs_dir / f"{job_id}.json").open("w") as f:
        json.dump({
            "job_id": job_id,
            "project_id": project_id,
            "operation": operation,
            "status": "queued",
            "data": None,
            "timestamp": int(time.time() * 1000),
        }, f, indent=2)


# --------------------------------------------------------------------------- #
# POST /ai/ingest_documents/{project_id}                                      #
# --------------------------------------------------------------------------- #

@router.post(
    "/ingest_documents/{project_id}",
    summary="Ingest documentation files for a project",
)
async def ingest_documents(
    project_id: str = PathParam(..., description="Target project ID"),
    request: DocumentIngestionRequest = None,
) -> dict[str, Any]:
    """
    Ingest documentation files (PDF, DOCX, Markdown, TXT) for a project.

    Mode is selected automatically:
    - `APP_KB_SERVICE_ENABLED=true` → KB microservice via SQS (deployed env)
    - `APP_WEAVIATE_ENABLED=true`   → local Docker Weaviate RAG
    - default                       → store in `docs/` folder, agents read via Glob/Read

    SYNC mode  (APP_SQS_ENABLED=false): blocks until ingestion completes.
    ASYNC mode (APP_SQS_ENABLED=true):  enqueues an `ingest_documents` SQS job,
                                        returns status="queued" + job_id immediately.
    """
    if not request:
        raise HTTPException(status_code=400, detail="Request body is required.")

    if not request.target_files and not request.file_location:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide at least one of 'target_files' (file paths on disk) "
                "or 'file_location' (S3/URL for KB service)."
            ),
        )

    logger.info(
        f"ingest_documents: project={project_id}, "
        f"files={len(request.target_files)}, file_location={request.file_location}"
    )

    # ── SQS async mode ──
    # V1 wire format: operation="upload_file", data uses camelCase field names
    # (fileLocation, fileName, extract_images, id) matching v1 sqs_consumer.py
    if settings.sqs_enabled:
        job_id = str(uuid4())
        data = {
            "fileLocation": request.file_location,
            "fileName": request.file_name,
            "extract_images": request.extract_images,
        }
        # Remove None values to keep payload clean
        data = {k: v for k, v in data.items() if v is not None}
        _write_job_queued(project_id, job_id, "upload_file", data)
        _send_to_sqs(job_id, project_id, "upload_file", data)
        return {
            "status": "queued",
            "project_id": project_id,
            "mode": "queued",
            "ingested_files": 0,
            "total_chunks": None,
            "kb_response": None,
            "local_copies": [],
            "message": f"Document ingestion job queued: {job_id}. Poll GET /ai/jobs/{project_id}/{job_id} for results.",
        }

    # ── Sync mode ──
    try:
        svc = DocumentIngestionService(project_id)
        result = await svc.ingest_files(
            target_files=request.target_files,
            file_location=request.file_location,
            file_name=request.file_name,
            extract_images=request.extract_images,
            user_id=request.user_id,
            organization_id=request.organization_id,
        )
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"ingest_documents failed for {project_id}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Document ingestion failed: {exc}")


# --------------------------------------------------------------------------- #
# GET /ai/documents/{project_id}                                              #
# --------------------------------------------------------------------------- #

@router.get(
    "/documents/{project_id}",
    summary="List ingested documents for a project",
)
async def list_documents(
    project_id: str = PathParam(..., description="Project ID"),
) -> dict[str, Any]:
    try:
        svc = DocumentIngestionService(project_id)
        docs = svc.list_documents()
        return {
            "status": "ok",
            "project_id": project_id,
            "documents": docs,
            "count": len(docs),
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        }
    except Exception as exc:
        logger.error(f"list_documents failed for {project_id}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {exc}")


# --------------------------------------------------------------------------- #
# DELETE /ai/documents/{project_id}/{file_name}                               #
# --------------------------------------------------------------------------- #

@router.delete(
    "/documents/{project_id}/{file_name}",
    summary="Delete a document from a project",
)
async def delete_document(
    project_id: str = PathParam(..., description="Project ID"),
    file_name: str = PathParam(..., description="Document filename to delete"),
    file_id: Optional[str] = Query(None, description="File ID (used by KB service)"),
) -> dict[str, Any]:
    try:
        svc = DocumentIngestionService(project_id)
        result = await svc.delete_file(
            file_name=file_name,
            file_path=f"docs/{file_name}",
            file_id=file_id,
        )
        return result
    except Exception as exc:
        logger.error(
            f"delete_document failed for {project_id}/{file_name}: {exc}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {exc}")
