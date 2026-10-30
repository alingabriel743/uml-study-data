"""
Dependency management API — diagrams-v2.

Supports two modes (same pattern as diagram_generation.py):
  SYNC  (default, APP_SQS_ENABLED=false): runs DependencyService directly, returns full response.
  ASYNC (APP_SQS_ENABLED=true):           sends job to SQS, returns job_id + status="queued".

Endpoints:
  POST   /ai/ingest_dependencies/{project_id}
  GET    /ai/dependencies/{project_id}
  GET    /ai/dependencies/{project_id}/{dep_name}/context
  DELETE /ai/dependencies/{project_id}?dependency_id=
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path as FilePath
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path, Query

from app.models.dependency_models import (
    DependencyIngestionRequest,
    DependencyIngestionResponse,
    UPLOAD_METHODS,
    DEPENDENCY_TYPES,
)
from app.services.dependency_service import DependencyService
from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/ai")


# --------------------------------------------------------------------------- #
# SQS helpers (mirrors diagram_generation.py pattern exactly)                 #
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
    logger.info(f"ingest_dependency: job {job_id} enqueued")


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
# POST /ai/ingest_dependencies/{project_id}                                   #
# --------------------------------------------------------------------------- #

@router.post(
    "/ingest_dependencies/{project_id}",
    response_model=DependencyIngestionResponse,
    summary="Ingest dependencies for a project",
)
async def ingest_dependencies(
    project_id: str = Path(..., description="Target project ID"),
    request: DependencyIngestionRequest = None,
):
    """
    Download / clone each dependency, run Tree-sitter IR extraction, then run
    DependencyAnalyzerAgent → `dependency_context.json`.

    SYNC mode  (APP_SQS_ENABLED=false): blocks until all dependencies are processed.
    ASYNC mode (APP_SQS_ENABLED=true):  enqueues an `ingest_dependency` SQS job,
                                        returns status="queued" + job_id immediately.
    """
    if not request or not request.dependencies:
        raise HTTPException(
            status_code=400,
            detail="At least one dependency must be provided in the request body.",
        )

    for dep in request.dependencies:
        if dep.uploadMethod not in UPLOAD_METHODS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid uploadMethod '{dep.uploadMethod}'. Must be one of: {UPLOAD_METHODS}",
            )
        if dep.type not in DEPENDENCY_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid type '{dep.type}'. Must be one of: {DEPENDENCY_TYPES}",
            )

    logger.info(
        f"ingest_dependencies: project={project_id}, count={len(request.dependencies)}"
    )

    # ── SQS async mode ──
    # V1 wire format: one SQS message per dependency, with the dependency
    # data sent directly in msg.data (not wrapped in a list).
    # "id" is sent as "dependencyId" to match v1 consumer expectations.
    if settings.sqs_enabled:
        job_ids = []
        for dep in request.dependencies:
            job_id = str(uuid4())
            dep_dict = dep.model_dump()
            # V1 uses "dependencyId" not "id"
            if dep_dict.get("id"):
                dep_dict["dependencyId"] = dep_dict.pop("id")
            _write_job_queued(project_id, job_id, "ingest_dependency", dep_dict)
            _send_to_sqs(job_id, project_id, "ingest_dependency", dep_dict)
            job_ids.append(job_id)
        first_job = job_ids[0] if job_ids else "unknown"
        return DependencyIngestionResponse(
            status="queued",
            project_id=project_id,
            processed_dependencies=0,
            failed_dependencies=None,
            message=(
                f"{len(job_ids)} dependency ingestion job(s) queued. "
                f"Poll GET /ai/jobs/{project_id}/{first_job} (and subsequent job IDs) for results."
            ),
        )

    # ── Sync mode ──
    try:
        service = DependencyService(project_id)
        successful, failed = await service.process_dependencies(request.dependencies)

        status = "ok" if successful > 0 else "error"
        message = f"Successfully processed {successful} of {len(request.dependencies)} dependencies."
        if failed:
            message += f" Failed: {', '.join(failed)}."

        return DependencyIngestionResponse(
            status=status,
            project_id=project_id,
            processed_dependencies=successful,
            failed_dependencies=failed or None,
            message=message,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"ingest_dependencies failed for {project_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Dependency ingestion failed: {exc}",
        )


# --------------------------------------------------------------------------- #
# GET /ai/dependencies/{project_id}                                           #
# --------------------------------------------------------------------------- #

@router.get(
    "/dependencies/{project_id}",
    summary="List all dependencies for a project",
)
async def list_dependencies(
    project_id: str = Path(..., description="Project ID"),
):
    try:
        service = DependencyService(project_id)
        deps = service.list_dependencies()
        return {
            "status": "ok",
            "project_id": project_id,
            "dependencies": deps,
            "count": len(deps),
        }
    except Exception as exc:
        logger.error(f"list_dependencies failed for {project_id}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve dependencies: {exc}")


# --------------------------------------------------------------------------- #
# GET /ai/dependencies/{project_id}/{dep_name}/context                        #
# --------------------------------------------------------------------------- #

@router.get(
    "/dependencies/{project_id}/{dep_name}/context",
    summary="Get the agent-produced context for a dependency",
)
async def get_dependency_context(
    project_id: str = Path(..., description="Project ID"),
    dep_name: str = Path(..., description="Dependency name"),
):
    try:
        service = DependencyService(project_id)
        context = service.get_dependency_context(dep_name)
        if context is None:
            raise HTTPException(
                status_code=404,
                detail=f"No context found for dependency '{dep_name}' in project '{project_id}'.",
            )
        return {
            "status": "ok",
            "project_id": project_id,
            "dependency_name": dep_name,
            "context": context,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            f"get_dependency_context failed for {project_id}/{dep_name}: {exc}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to retrieve dependency context: {exc}")


# --------------------------------------------------------------------------- #
# DELETE /ai/dependencies/{project_id}?dependency_id=                         #
# --------------------------------------------------------------------------- #

@router.delete(
    "/dependencies/{project_id}",
    summary="Delete a dependency by ID",
)
async def delete_dependency(
    project_id: str = Path(..., description="Project ID"),
    dependency_id: str = Query(..., description="Dependency ID to delete"),
):
    try:
        service = DependencyService(project_id)
        result = await service.delete_dependency(dependency_id)
        return {
            "status": "ok",
            "project_id": project_id,
            "deleted_dependency": result["deleted_dependency"],
            "message": "Dependency deleted successfully.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(
            f"delete_dependency failed for {project_id}/{dependency_id}: {exc}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to delete dependency: {exc}")
