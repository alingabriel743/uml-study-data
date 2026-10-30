"""
SQS Worker for diagrams-v2.

Polls the SQS request queue, runs the DiagramOrchestrator, writes
results to disk and puts a response message onto the response queue.

Architecture:
  API → SQS request queue → Worker → SQS response queue
                                   → data/{project_id}/jobs/{job_id}.json

The API returns a job_id immediately. The frontend polls
GET /ai/jobs/{job_id} which reads the persisted job file.
"""

import asyncio
import hashlib
import json
import logging
from app.utils.logger import get_logger
import time
from pathlib import Path
from typing import Dict, Any

from app.utils.config import settings
from app.utils.sqs_client import get_sqs_client, get_queue_url
from app.models.sqs_models import (
    DiagramRequestMessageDto,
    DiagramResponseMessageDto,
    DiagramOperationType,
    DiagramJobStatus,
)

logger = get_logger(__name__)

# Module-level client — created once at import (matches req-copilot pattern).
# The proxy creates its own fresh client via get_sqs_client(), so there is
# no connection-pool contention.
client = get_sqs_client()


def _dedup_id(job_id: str) -> str:
    ts = str(int(time.time() * 1000))
    return hashlib.sha256(f"{job_id}-{ts}".encode()).hexdigest()


def _send_response(response: DiagramResponseMessageDto, response_queue_url: str) -> None:
    """Push a response message to the SQS response queue (v1-compatible format)."""
    body = response.model_dump_json()
    size_bytes = len(body.encode("utf-8"))
    size_mb = size_bytes / 1024 / 1024
    logger.info(f" Sending SQS response:")
    logger.info(f"   ├─ Job ID:    {response.job_id}")
    logger.info(f"   ├─ Status:    {response.status}")
    logger.info(f"   ├─ Operation: {response.operation}")
    logger.info(f"   ├─ Size:      {size_mb:.2f} MB ({size_bytes:,} bytes)")
    logger.info(f"   └─ Queue:     {response_queue_url.split('/')[-1]}")
    from app.utils.sqs_client import send_sqs_message
    send_sqs_message(
        client,
        queue_url=response_queue_url,
        message_body=body,
        message_group_id=response.job_id or response.projectId or "unknown",
        message_deduplication_id=_dedup_id(response.job_id or "unknown"),
    )
    logger.info(f" Response sent to SQS for job {response.job_id}")


def _write_job_file(project_id: str, job_id: str, payload: dict) -> None:
    """Persist job result to disk so the API can serve it via GET /ai/jobs/{job_id}."""
    jobs_dir = Path("data") / project_id / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_file = jobs_dir / f"{job_id}.json"
    with job_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Job result written to {job_file}")


# ── Operation processors ───────────────────────────────────────────

async def _process_upload_repo(msg: DiagramRequestMessageDto) -> Dict[str, Any]:
    """Process upload_repo operation — GitHub clone or ZIP extract."""
    from app.api.repo_upload import _process_github_repo, _process_local_or_path
    from app.models.schemas import RepoInput

    data = msg.data or {}
    project_id = msg.projectId or getattr(msg, 'project_id', None) or "unknown"

    logger.info(f"")
    logger.info(f"{'─' * 60}")
    logger.info(f" UPLOAD_REPO: project={project_id}")
    logger.info(f"{'─' * 60}")

    try:
        repo_input = RepoInput(
            url=data.get("url"),
            location=data.get("location"),
            language=data.get("language", "sql"),
            token=data.get("token"),
        )

        if repo_input.url:
            result = await _process_github_repo(repo_input, project_id)
        else:
            result = await _process_local_or_path(repo_input, project_id)

        if result.status != "ok":
            logger.error(f"   upload_repo result: {result.status} — {result.detected_language}")
            return {"status": "error", "error": result.detected_language or "Upload failed"}

        logger.info(f"    Upload complete: {result.total_files} files, language={result.language}")

        # Auto-trigger IR generation immediately after successful upload
        detected_lang = result.language or result.main_language or "sql"
        from app.api.ir_generation import _send_ir_to_sqs
        from uuid import uuid4 as _uuid4
        ir_job_id = str(_uuid4())
        try:
            _send_ir_to_sqs(ir_job_id, project_id, detected_lang)
            logger.info(f"    Auto-triggered generate_ir job {ir_job_id} (language={detected_lang})")
        except Exception as ir_err:
            logger.warning(f"     Failed to auto-trigger IR: {ir_err}")

        # Return the full RepoResponse matching v1 structure
        result_dict = result.model_dump() if hasattr(result, "model_dump") else {}
        result_dict["ir_job_id"] = ir_job_id  # extra field for tracking
        return result_dict
    except Exception as e:
        logger.error(f"upload_repo failed: {e}")
        return {"status": "error", "error": str(e)}


async def _process_generate_ir(msg: DiagramRequestMessageDto) -> Dict[str, Any]:
    """Process generate_ir operation — Tree-sitter IR extraction."""
    import os
    from app.utils.ir_extraction.postprocess_ir import postprocess_ir
    from app.api.ir_generation import LANGUAGE_EXTRACTOR_MAP

    data = msg.data or {}
    project_id = msg.projectId or getattr(msg, 'project_id', None) or "unknown"
    language = data.get("language", "sql").lower()

    logger.info(f"")
    logger.info(f"{'─' * 60}")
    logger.info(f" GENERATE_IR: project={project_id} language={language}")
    logger.info(f"{'─' * 60}")

    try:
        repo_path = str(Path("data") / project_id)
        if not os.path.exists(repo_path):
            return {"status": "error", "error": f"Project {project_id} not found"}

        extractor_class = LANGUAGE_EXTRACTOR_MAP.get(language)
        if not extractor_class:
            return {"status": "error", "error": f"Language '{language}' not supported"}

        import asyncio as _asyncio

        extractor = extractor_class(repo_path)
        loop = _asyncio.get_event_loop()
        ir_data = await loop.run_in_executor(None, extractor.extract_ir)
        ir_path = str(Path(repo_path) / "ir.json")
        extractor.save_ir(ir_path)
        logger.info(f"   IR extracted to: {ir_path}")

        postprocessed_path = await loop.run_in_executor(None, postprocess_ir, ir_path)
        logger.info(f"   IR postprocessed: {postprocessed_path}")

        # Read stats from postprocessed file
        try:
            final_path = postprocessed_path if postprocessed_path else ir_path
            with open(final_path, "r") as f:
                import json as _json
                actual_ir = _json.load(f)
            stats = {
                "tables": len(actual_ir.get("tables", [])),
                "procedures": len(actual_ir.get("procedures", [])),
                "functions": len(actual_ir.get("functions", [])),
                "classes": len(actual_ir.get("classes", [])),
            }
        except Exception:
            stats = {}

        logger.info(f"   IR stats: {stats}")

        return {
            "status": "ok",
            "project_id": project_id,
            "ir_file": postprocessed_path if postprocessed_path else ir_path,
            "stats": stats,
        }
    except Exception as e:
        logger.error(f"generate_ir failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def _process_ingest_dependency(msg: DiagramRequestMessageDto) -> Dict[str, Any]:
    """
    Process ingest_dependency operation — download, IR extract, agent analysis.

    V1 wire format: msg.data contains a SINGLE dependency directly (not a list).
    Field names: dependencyId (mapped to id), plus all DependencyDto fields.
    Response: v1-compatible with dependency_id, source_path, operation fields.
    """
    from app.services.dependency_service import DependencyService
    from app.models.dependency_models import DependencyDto

    data = msg.data or {}
    project_id = msg.projectId or "unknown"

    # V1: dependency_id comes as "dependencyId" OR "id"
    dependency_id = data.get("dependencyId") or data.get("id")

    logger.info(f"")
    logger.info(f"{'─' * 60}")
    logger.info(f" INGEST_DEPENDENCY: project={project_id}, dep_id={dependency_id}")
    logger.info(f"{'─' * 60}")

    try:
        if not data:
            return {
                "status": "error",
                "project_id": project_id,
                "operation": "ingest_dependency",
                "dependency_id": dependency_id,
                "message": "No dependency data provided in request",
            }

        # V1 sends "dependencyId"; map to "id" for DependencyDto
        dep_data = data.copy()
        if "dependencyId" in dep_data and "id" not in dep_data:
            dep_data["id"] = dep_data["dependencyId"]

        try:
            dependency = DependencyDto.model_validate(dep_data)
        except Exception as exc:
            logger.error(f"Invalid dependency data: {exc}")
            return {
                "status": "error",
                "project_id": project_id,
                "operation": "ingest_dependency",
                "dependency_id": dependency_id,
                "message": f"Invalid dependency data: {exc}",
            }

        service = DependencyService(project_id)
        successful, failed = await service.process_dependencies([dependency])

        sanitized = service._sanitize(dependency.name)
        source_path = str(service.dependencies_path / sanitized)

        if successful > 0:
            logger.info(f"    Dependency '{dependency.name}' processed successfully")
            return {
                "status": "ok",
                "project_id": project_id,
                "operation": "ingest_dependency",
                "dependency_id": dependency_id,
                "processed_dependencies": successful,
                "total_dependencies": 1,
                "source_path": source_path,
                "message": f"Successfully processed dependency '{dependency.name}' (ID: {dependency_id})",
            }
        else:
            return {
                "status": "error",
                "project_id": project_id,
                "operation": "ingest_dependency",
                "dependency_id": dependency_id,
                "failed_dependencies": failed,
                "message": f"Failed to process dependency '{dependency.name}' (ID: {dependency_id})",
            }

    except Exception as exc:
        logger.error(f"ingest_dependency failed: {exc}", exc_info=True)
        return {
            "status": "error",
            "project_id": project_id,
            "operation": "ingest_dependency",
            "dependency_id": dependency_id,
            "error": str(exc),
            "message": f"Dependency ingestion failed: {exc}",
        }


async def _process_upload_file(msg: DiagramRequestMessageDto) -> Dict[str, Any]:
    """
    Process upload_file operation — document ingestion (v1 operation name).

    V1 wire format: msg.data has fileLocation/file_location, fileName/name,
    fileType (PDF/DOCX/MD), id, extract_images.
    Routes to KB service when enabled, falls back to local Weaviate / agent-only.
    """
    from app.services.document_ingestion_service import DocumentIngestionService
    from app.utils.config import settings

    data = msg.data or {}
    project_id = msg.projectId or "unknown"

    # V1 uses both camelCase and snake_case field names
    file_location = data.get("fileLocation") or data.get("file_location")
    file_name = data.get("name") or data.get("fileName") or data.get("file_name")
    file_type = data.get("fileType", "")
    file_id = data.get("id")
    extract_images = data.get("extract_images", True)

    logger.info(f"")
    logger.info(f"{'─' * 60}")
    logger.info(f" UPLOAD_FILE: project={project_id}, file={file_name}, type={file_type}")
    logger.info(f"{'─' * 60}")

    try:
        # V1: build S3 path from file_id + fileType when fileLocation not explicit
        if file_id and file_type and not file_location:
            from app.utils.config import load_config
            cfg = load_config()
            ext_map = {"PDF": ".pdf", "DOCX": ".docx", "DOC": ".docx", "MARKDOWN": ".md", "MD": ".md"}
            ext = ext_map.get(file_type.upper())
            if ext:
                bucket = cfg.get("s3", {}).get("documents_bucket", "codecatalyst-documents")
                file_location = f"{bucket}/{file_id}"
                logger.info(f"Built S3 path from file_id: {file_location}")
            else:
                return {"status": "error", "error": f"Unsupported fileType: {file_type}"}

        if not file_location:
            return {"status": "error", "error": "fileLocation is required for upload_file"}

        svc = DocumentIngestionService(project_id)
        result = await svc.ingest_files(
            target_files=[],
            file_location=file_location,
            file_name=file_name,
            extract_images=extract_images,
            user_id=msg.userId,
            organization_id=msg.organizationId,
        )

        # Build v1-compatible response structure
        local_copies = result.get("local_copies", [])
        v1_response = {
            "project_id": project_id,
            "file_id": file_id,
            "file_name": file_name,
            "file_type": file_type,
            "file_location": file_location,
            "location_type": "s3" if not file_location.startswith("http") else "url",
            "downloaded_files": local_copies,
            "failed_files": [],
            "file_count": len(local_copies),
            "weaviate_status": result.get("mode", "skipped"),
            "message": result.get("message", f"Successfully processed file from {file_location}"),
        }

        logger.info(f"    {v1_response.get('message', 'Done')}")
        return v1_response

    except Exception as exc:
        logger.error(f"upload_file failed: {exc}", exc_info=True)
        return {
            "project_id": project_id,
            "file_id": file_id,
            "file_name": file_name,
            "file_type": file_type,
            "file_location": file_location,
            "location_type": "s3" if file_location and not file_location.startswith("http") else "url",
            "error": str(exc),
            "failed_files": [file_location] if file_location else [],
            "file_count": 0,
            "weaviate_status": "failed",
        }


async def _process_delete_file(msg: DiagramRequestMessageDto) -> Dict[str, Any]:
    """Process delete_file operation — remove document from project."""
    from app.services.document_ingestion_service import DocumentIngestionService

    data = msg.data or {}
    project_id = msg.projectId or "unknown"
    file_name = data.get("fileName") or data.get("file_name")
    file_id = data.get("id")

    logger.info(f"DELETE_FILE: project={project_id}, file={file_name}")

    if not file_name:
        return {"status": "error", "error": "fileName is required for DELETE_FILE"}

    try:
        svc = DocumentIngestionService(project_id)
        result = await svc.delete_file(
            file_name=file_name,
            file_path=f"docs/{file_name}",
            file_id=file_id,
        )

        # v1-compatible response
        return {
            "project_id": project_id,
            "file_id": file_id,
            "file_name": file_name,
            "chunks_deleted": 0,  # no Weaviate in v2
            "weaviate_status": "skipped",
            "local_file_deleted": result.get("deleted_locally", False),
            "local_file_path": f"data/{project_id}/docs/{file_name}",
            "message": result.get("message", f"File '{file_name}' deleted."),
        }

    except Exception as exc:
        logger.error(f"delete_file failed: {exc}", exc_info=True)
        return {
            "status": "error",
            "project_id": project_id,
            "file_name": file_name,
            "error": str(exc),
        }


async def _process_delete_dependency(msg: DiagramRequestMessageDto) -> Dict[str, Any]:
    """Process delete_dependency operation — remove dependency from project."""
    from app.services.dependency_service import DependencyService

    data = msg.data or {}
    project_id = msg.projectId or "unknown"
    dependency_id = data.get("dependencyId") or data.get("id")

    logger.info(f"DELETE_DEPENDENCY: project={project_id}, dep_id={dependency_id}")

    if not dependency_id:
        return {
            "status": "error",
            "project_id": project_id,
            "operation": "delete_dependency",
            "message": "dependencyId is required",
        }

    try:
        service = DependencyService(project_id)
        result = await service.delete_dependency(dependency_id=dependency_id)
        deleted_dep = result["deleted_dependency"]

        return {
            "status": "ok",
            "project_id": project_id,
            "dependency_id": dependency_id,
            "operation": "delete_dependency",
            "deleted_dependency": deleted_dep,
            "message": f"Dependency '{deleted_dep['name']}' (ID: {deleted_dep['id']}) deleted successfully",
        }

    except ValueError as exc:
        return {
            "status": "error",
            "project_id": project_id,
            "operation": "delete_dependency",
            "dependency_id": dependency_id,
            "error": str(exc),
        }
    except Exception as exc:
        logger.error(f"delete_dependency failed: {exc}", exc_info=True)
        return {
            "status": "error",
            "project_id": project_id,
            "operation": "delete_dependency",
            "dependency_id": dependency_id,
            "error": str(exc),
        }


# v1 diagram type mappings (external camelCase ↔ internal snake_case)
DIAGRAM_TYPE_MAPPING = {
    "classDiagram": "class",
    "sequenceDiagram": "sequence",
    "useCaseDiagram": "use_case",
    "activityDiagram": "activity",
    "componentDiagram": "component",
    "deploymentDiagram": "deployment",
    "systemContextDiagram": "system_context",
    "stateMachineDiagram": "state",
    "packageDiagram": "package",
    "architectureDiagram": "architecture",
}
REVERSE_DIAGRAM_MAPPING = {v: k for k, v in DIAGRAM_TYPE_MAPPING.items()}


async def _process_generate_description(msg: DiagramRequestMessageDto) -> Dict[str, Any]:
    """
    Process generate_description operation — generate a text description for a diagram.

    v2 approach: Use a lightweight BaseAgent subclass to read the .puml and produce a description.
    No RAG needed — the agent reads the diagram source directly.
    """
    from app.agent.base_agent import BaseAgent

    data = msg.data or {}
    project_id = msg.projectId or "unknown"
    requested_type = data.get("diagramType", "classDiagram")
    diagram_type = DIAGRAM_TYPE_MAPPING.get(requested_type, requested_type)
    external_diagram_type = REVERSE_DIAGRAM_MAPPING.get(diagram_type, requested_type)
    diagram_id = data.get("diagramId")
    flow_id = data.get("flowId")
    content = data.get("content", "")

    logger.info(f"GENERATE_DESCRIPTION: project={project_id}, type={diagram_type}")

    try:
        # If no content provided, try to read the diagram file from disk
        if not content:
            diagrams_dir = Path("data") / project_id / "diagrams" / diagram_type
            if diagrams_dir.exists():
                puml_files = sorted(diagrams_dir.glob("*.puml"))
                if puml_files:
                    content = puml_files[0].read_text(encoding="utf-8")

        if not content:
            return {
                "status": "error",
                "project_id": project_id,
                "error": "No diagram content found to describe",
            }

        system_prompt = (
            "You are a software architecture documentation expert. "
            "Given a PlantUML diagram, produce a clear, concise description of what the diagram shows. "
            "Focus on the key components, their relationships, and the overall architectural patterns. "
            "Write in professional technical documentation style. "
            "Output ONLY the description text, no markdown headers or formatting wrappers."
        )

        # Create a minimal concrete agent for description generation
        class _DescriptionAgent(BaseAgent):
            async def run(self, **kwargs) -> dict:
                return {}

        agent = _DescriptionAgent(
            name="DescriptionAgent",
            system_prompt=system_prompt,
            tools=["Read", "Glob"],
            permission_mode="bypassPermissions",
        )

        prompt = (
            f"Describe the following {diagram_type} PlantUML diagram:\n\n"
            f"```plantuml\n{content}\n```\n\n"
            "Provide a 2-4 paragraph description covering:\n"
            "1. What the diagram represents\n"
            "2. Key components and their roles\n"
            "3. Important relationships and data flows\n"
        )

        description = ""
        async for message in agent.execute(prompt):
            if message.get("type") == "assistant" and message.get("text"):
                description = message["text"]

        # Save description to disk
        desc_dir = Path("data") / project_id / "diagrams" / diagram_type
        desc_dir.mkdir(parents=True, exist_ok=True)
        desc_filename = f"{diagram_type}_description.md"
        if flow_id:
            desc_filename = f"{diagram_type}_{flow_id}_description.md"
        desc_path = desc_dir / desc_filename
        desc_path.write_text(description, encoding="utf-8")

        inference_server = (msg.inferenceServer.value
                            if msg.inferenceServer else "ON_PREM")

        response = {
            "project_id": project_id,
            "diagram_type": external_diagram_type,
            "diagram_description": description,
            "description_path": str(desc_path),
            "inference_server": inference_server,
        }
        if diagram_id:
            response["diagram_id"] = diagram_id
        if flow_id:
            response["flow_id"] = flow_id

        return response

    except Exception as exc:
        logger.error(f"generate_description failed: {exc}", exc_info=True)
        result = {
            "status": "error",
            "project_id": project_id,
            "error": str(exc),
        }
        if diagram_id:
            result["diagramId"] = diagram_id
        return result


async def _process_regenerate_diagram(msg: DiagramRequestMessageDto) -> Dict[str, Any]:
    """
    Process regenerate_diagram operation — regenerate a diagram based on user feedback.

    v2 approach: Re-run the DiagramOrchestrator with the user feedback injected
    into the prompt so the agent uses it as guidance for the new version.
    """
    from app.agent.orchestrator import DiagramOrchestrator

    data = msg.data or {}
    project_id = msg.projectId or "unknown"
    requested_type = data.get("diagramType", "classDiagram")
    diagram_type = DIAGRAM_TYPE_MAPPING.get(requested_type, requested_type)
    external_diagram_type = REVERSE_DIAGRAM_MAPPING.get(diagram_type, requested_type)
    user_feedback = data.get("userFeedback", "")
    diagram_id = data.get("diagramId")
    conversation_id = data.get("conversationId")

    logger.info(f"REGENERATE_DIAGRAM: project={project_id}, type={diagram_type}")

    if not user_feedback:
        result = {
            "status": "error",
            "project_id": project_id,
            "message": "userFeedback is required for diagram regeneration",
        }
        if diagram_id:
            result["diagramId"] = diagram_id
        return result

    try:
        # Build options that pass user feedback to the orchestrator
        options = {
            "user_feedback": user_feedback,
            "regeneration": True,
        }

        orchestrator = DiagramOrchestrator(
            project_id=project_id,
            diagram_type=diagram_type,
            options=options,
        )
        result = await orchestrator.run()

        if result["status"] == "error":
            error_result = {
                "status": "error",
                "project_id": project_id,
                "error": result.get("error", "Regeneration failed"),
            }
            if diagram_id:
                error_result["diagramId"] = diagram_id
            return error_result

        # Read the regenerated diagram
        diagram_code = ""
        for fp in result.get("output_files", []):
            p = Path(fp)
            if p.exists() and p.suffix == ".puml":
                diagram_code = p.read_text(encoding="utf-8")
                break  # Use the first/main diagram

        inference_server = (msg.inferenceServer.value
                            if msg.inferenceServer else "ON_PREM")

        response = {
            "project_id": project_id,
            "diagram_type": external_diagram_type,
            "diagram_code": diagram_code,
            "status": "ok",
            "inference_server": inference_server,
            "message": "Diagram regenerated with user feedback",
        }
        if diagram_id:
            response["diagramId"] = diagram_id
        if conversation_id:
            response["conversation_id"] = conversation_id

        return response

    except Exception as exc:
        logger.error(f"regenerate_diagram failed: {exc}", exc_info=True)
        result = {
            "status": "error",
            "project_id": project_id,
            "operation": "regenerate_diagram",
            "error": str(exc),
            "message": f"Diagram regeneration failed: {str(exc)}",
        }
        if diagram_id:
            result["diagramId"] = diagram_id
        return result


async def _process_generate_diagram(msg: DiagramRequestMessageDto) -> Dict[str, Any]:
    """Single diagram type generation."""
    from app.agent.orchestrator import DiagramOrchestrator

    data = msg.data or {}
    project_id = msg.projectId or getattr(msg, 'project_id', None) or "unknown"
    # v1 sends diagramType (camelCase); also accept diagram_type for internal use
    diagram_type = data.get("diagramType") or data.get("diagram_type", "class")
    options = data.get("options")

    orchestrator = DiagramOrchestrator(
        project_id=project_id,
        diagram_type=diagram_type,
        options=options,
    )
    result = await orchestrator.run()

    if result["status"] == "error":
        return {"status": "error", "error": result.get("error", "Unknown error")}

    # Read generated .puml files
    output_files = []
    for fp in result.get("output_files", []):
        p = Path(fp)
        if p.exists() and p.suffix == ".puml":
            output_files.append(p)

    # v1-compatible: diagram_type in response is camelCase
    external_diagram_type = REVERSE_DIAGRAM_MAPPING.get(diagram_type, diagram_type)

    # Build main diagram_code (first/main file) — v1 always has this
    main_diagram_code = output_files[0].read_text(encoding="utf-8") if output_files else ""

    # Build split_flows if multiple diagrams were generated (v1-compatible format)
    split_flows = []
    if len(output_files) > 1:
        for i, p in enumerate(output_files):
            code = p.read_text(encoding="utf-8")
            # Derive flow_id from filename: e.g. "class_core_model.puml" → "core_model"
            stem = p.stem
            prefix = f"{diagram_type}_"
            flow_id = stem[len(prefix):] if stem.startswith(prefix) else stem
            diagram_id = f"{project_id}_{flow_id}"

            # Try to extract title from PlantUML content
            title = flow_id.replace("_", " ").title()
            for line in code.split("\n"):
                stripped = line.strip()
                if stripped.lower().startswith("title "):
                    title = stripped[6:].strip()
                    break

            split_flows.append({
                "flow_id": flow_id,
                "diagram_id": diagram_id,
                "title": title,
                "diagram_type": external_diagram_type,
                "diagram_code": code,
            })

    # Report token consumption if userId/organizationId provided (v1 pattern)
    user_id = msg.userId or (msg.data or {}).get("userId")
    org_id = msg.organizationId or (msg.data or {}).get("organizationId")
    if user_id and org_id:
        try:
            from app.utils.usage_reporter import record_usage_from_agent_messages
            record_usage_from_agent_messages(
                messages=result.get("messages", []),
                user_id=user_id,
                organization_id=org_id,
            )
        except Exception as usage_err:
            logger.warning(f"     Usage reporting failed: {usage_err}")

    inference_server = (msg.inferenceServer.value
                        if msg.inferenceServer else "ON_PREM")

    payload = {
        "project_id": project_id,
        "diagram_type": external_diagram_type,
        "diagram_code": main_diagram_code,
        "inference_server": inference_server,
    }

    # Add split_flows only when multiple diagrams exist (matches v1 pattern)
    if split_flows:
        payload["split_flows"] = split_flows
        payload["flows_created"] = len(split_flows)
        payload["total_flows"] = len(split_flows)
        payload["splitting_used"] = True
        payload["splitting_type"] = "pre_generation"
        payload["total_elements_preserved"] = sum(
            code.count("\n") for code in (sf["diagram_code"] for sf in split_flows)
        )

    return payload


# ── Core message processor ─────────────────────────────────────────

async def process_message(message_body: str, response_queue_url: str) -> bool:
    """Parse and process one SQS message, send response."""
    start = time.time()
    msg: DiagramRequestMessageDto | None = None

    # Log incoming message details
    incoming_bytes = len(message_body.encode("utf-8"))
    incoming_mb = incoming_bytes / 1024 / 1024
    logger.info(f"")
    logger.info(f"{'═' * 60}")
    logger.info(f" INCOMING SQS MESSAGE")
    logger.info(f"{'═' * 60}")
    logger.info(f"   Size:    {incoming_mb:.2f} MB ({incoming_bytes:,} bytes)")

    try:
        msg = DiagramRequestMessageDto.model_validate_json(message_body)
        # Support both v1 camelCase (projectId) and legacy snake_case (project_id)
        project_id = msg.projectId or getattr(msg, 'project_id', None) or "unknown"
        job_id = msg.job_id or project_id

        logger.info(f"   Job ID:  {job_id}")
        logger.info(f"   Project: {project_id}")
        logger.info(f"   Op:      {msg.operation}")
        logger.info(f"   Data:    {list(msg.data.keys()) if isinstance(msg.data, dict) and msg.data else '(empty)'}")
        if msg.request_id:
            logger.info(f"   Req ID:  {msg.request_id}")
        logger.info(f"{'─' * 60}")
        logger.info(f"  Processing {msg.operation} for project {project_id}...")
        logger.info(f"{'─' * 60}")

        op = msg.operation

        if op == DiagramOperationType.UPLOAD_REPO:
            payload = await _process_upload_repo(msg)
        elif op == DiagramOperationType.GENERATE_IR:
            payload = await _process_generate_ir(msg)
        elif op == DiagramOperationType.GENERATE_DIAGRAM:
            payload = await _process_generate_diagram(msg)
        elif op == DiagramOperationType.GENERATE_DESCRIPTION:
            payload = await _process_generate_description(msg)
        elif op == DiagramOperationType.REGENERATE_DIAGRAM:
            payload = await _process_regenerate_diagram(msg)
        elif op == DiagramOperationType.INGEST_DEPENDENCY:
            payload = await _process_ingest_dependency(msg)
        elif op == DiagramOperationType.DELETE_DEPENDENCY:
            payload = await _process_delete_dependency(msg)
        elif op == DiagramOperationType.UPLOAD_FILE:
            payload = await _process_upload_file(msg)
        elif op == DiagramOperationType.DELETE_FILE:
            payload = await _process_delete_file(msg)
        else:
            raise ValueError(
                f"Unknown operation: {op}. "
                f"Supported: upload_repo, generate_ir, generate_diagram, generate_description, "
                f"regenerate_diagram, ingest_dependency, delete_dependency, upload_file, delete_file"
            )

        # v1-compatible status: "OK" or "ERROR"
        from app.models.sqs_models import AIResponseStatus
        response_status = (
            AIResponseStatus.OK
            if payload.get("status") != "error"
            else AIResponseStatus.ERROR
        )
        processing_time = time.time() - start

        # Log processing summary
        logger.info(f"")
        logger.info(f"{'─' * 60}")
        logger.info(f" JOB PROCESSING SUMMARY")
        logger.info(f"{'─' * 60}")
        logger.info(f"   Job ID:          {job_id}")
        logger.info(f"   Status:          {response_status.value}")
        logger.info(f"   Processing time: {processing_time:.1f}s")
        if payload.get("status") != "error":
            if msg.operation == DiagramOperationType.GENERATE_DIAGRAM:
                n_diagrams = len(payload.get("diagrams", []))
                logger.info(f"   Diagrams:        {n_diagrams}")
        logger.info(f"{'─' * 60}")

        # Persist result to disk
        job_payload = {
            "job_id": job_id,
            "project_id": project_id,
            "projectId": project_id,
            "operation": op,
            "status": DiagramJobStatus.COMPLETE.value if response_status == AIResponseStatus.OK else DiagramJobStatus.ERROR.value,
            "data": payload,
            "processing_time": int(processing_time * 1000),
            "processingTime": int(processing_time * 1000),
            "timestamp": int(time.time() * 1000),
        }
        _write_job_file(project_id, job_id, job_payload)

        # Build v1-compatible SQS response
        if response_status == AIResponseStatus.ERROR:
            # v1 pattern: error is a string message, data contains specific fields only
            error_message = payload.get("message") or payload.get("error") or str(payload)
            error_data = {}
            if op == DiagramOperationType.INGEST_DEPENDENCY and isinstance(payload, dict):
                if "dependency_id" in payload:
                    error_data["dependencyId"] = payload["dependency_id"]
                if "project_id" in payload:
                    error_data["projectId"] = payload["project_id"]
            elif op == DiagramOperationType.REGENERATE_DIAGRAM and isinstance(payload, dict):
                if "diagramId" in payload:
                    error_data["diagramId"] = payload["diagramId"]
                if "conversation_id" in payload:
                    error_data["conversation_id"] = payload["conversation_id"]
            response = DiagramResponseMessageDto(
                status=response_status,
                error=error_message,
                data=error_data if error_data else None,
                processingTime=int(processing_time * 1000),
                timestamp=int(time.time() * 1000),
                projectId=project_id,
                operation=op,
                userId=msg.userId,
                organizationId=msg.organizationId,
                job_id=job_id,
                request_id=msg.request_id,
            )
        else:
            response = DiagramResponseMessageDto(
                status=response_status,
                error=None,
                data=payload,
                processingTime=int(processing_time * 1000),
                timestamp=int(time.time() * 1000),
                projectId=project_id,
                operation=op,
                userId=msg.userId,
                organizationId=msg.organizationId,
                job_id=job_id,
                request_id=msg.request_id,
            )
        _send_response(response, response_queue_url)

        logger.info(f" Job {job_id} complete in {processing_time:.1f}s — status={response_status.value}")
        return True

    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Worker error processing message: {e}")

        processing_time = time.time() - start
        from app.models.sqs_models import AIResponseStatus
        err_job_id = (msg.job_id or msg.projectId or "unknown") if msg else "unknown"
        err_project_id = (msg.projectId or "unknown") if msg else "unknown"
        err_op = msg.operation if msg else DiagramOperationType.GENERATE_DIAGRAM

        # Persist error to disk
        _write_job_file(err_project_id, err_job_id, {
            "job_id": err_job_id,
            "project_id": err_project_id,
            "projectId": err_project_id,
            "operation": err_op,
            "status": DiagramJobStatus.ERROR.value,
            "error": str(e),
            "processing_time": int(processing_time * 1000),
            "processingTime": int(processing_time * 1000),
            "timestamp": int(time.time() * 1000),
        })

        error_response = DiagramResponseMessageDto(
            status=AIResponseStatus.ERROR,
            projectId=err_project_id,
            operation=err_op,
            error=str(e),
            processingTime=int(processing_time * 1000),
            timestamp=int(time.time() * 1000),
            job_id=err_job_id,
        )
        try:
            _send_response(error_response, response_queue_url)
        except Exception as send_err:
            logger.error(f"Failed to send error response: {send_err}")

        return False


async def process_and_acknowledge(
    message: Dict[str, Any],
    request_queue_url: str,
    response_queue_url: str,
) -> None:
    """Process a message and delete it from the queue when done."""
    message_id = message["MessageId"]
    logger.info(f"START_PROCESSING — {message_id}")
    try:
        await process_message(message["Body"], response_queue_url)
        logger.info(f"END_PROCESSING — {message_id}")
    finally:
        try:
            await asyncio.to_thread(
                client.delete_message,
                QueueUrl=request_queue_url,
                ReceiptHandle=message["ReceiptHandle"],
            )
            logger.info(f"Message acknowledged: {message_id}")
        except Exception as e:
            logger.error(f"Failed to acknowledge message {message_id}: {e}")


# ── Main consumer loop ─────────────────────────────────────────────

async def sqs_consumer_loop() -> None:
    """
    Main SQS consumer loop.
    Long-polls for messages and processes them concurrently in background tasks.
    """
    request_queue_url = get_queue_url(settings.sqs_request_queue_name)
    response_queue_url = get_queue_url(settings.sqs_response_queue_name)

    logger.info(f"")
    logger.info(f"{'═' * 60}")
    logger.info(f" SQS WORKER STARTED")
    logger.info(f"   Request  queue: {request_queue_url}")
    logger.info(f"   Response queue: {response_queue_url}")
    logger.info(f"{'═' * 60}")

    max_concurrent = 10
    semaphore = asyncio.Semaphore(max_concurrent)
    background_tasks: set[asyncio.Task] = set()

    async def _run_with_semaphore(message: Dict[str, Any]) -> None:
        async with semaphore:
            await process_and_acknowledge(message, request_queue_url, response_queue_url)

    poll_count = 0

    while True:
        try:
            # Long-poll: WaitTimeSeconds=20 returns immediately when a
            # message arrives. FIFO queues in all environments (including
            # LocalStack), matching atonis-req-copilot exactly.
            response = await asyncio.to_thread(
                client.receive_message,
                QueueUrl=request_queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,
            )

            messages = response.get("Messages", [])
            poll_count += 1

            if messages:
                logger.info(f"")
                logger.info(f"{'─' * 60}")
                logger.info(f" SQS POLL #{poll_count}: {len(messages)} message(s) received")
                logger.info(f"{'─' * 60}")

                for i, message in enumerate(messages):
                    msg_id = message["MessageId"]
                    body_bytes = len(message["Body"].encode("utf-8"))
                    logger.info(f"   [{i+1}/{len(messages)}] MessageId: {msg_id}")
                    logger.info(f"           Body size: {body_bytes:,} bytes")

                    # Extend visibility timeout in background — don't block processing start
                    async def _extend_visibility(receipt: str) -> None:
                        try:
                            await asyncio.to_thread(
                                client.change_message_visibility,
                                QueueUrl=request_queue_url,
                                ReceiptHandle=receipt,
                                VisibilityTimeout=3600,
                            )
                        except Exception as ve:
                            logger.warning(f"           Failed to extend visibility: {ve}")

                    asyncio.create_task(_extend_visibility(message["ReceiptHandle"]))

                    task = asyncio.create_task(_run_with_semaphore(message))
                    background_tasks.add(task)
                    task.add_done_callback(background_tasks.discard)
            else:
                if poll_count % 30 == 0:
                    # Log heartbeat every ~10 minutes (30 polls × 20s)
                    active = len(background_tasks)
                    logger.info(f" SQS worker heartbeat — poll #{poll_count}, "
                                f"active tasks: {active}/{max_concurrent}")

            # Clean up done tasks and log any errors
            done = {t for t in background_tasks if t.done()}
            for t in done:
                background_tasks.discard(t)
                if t.exception():
                    logger.error(f"Background task failed: {t.exception()}")

            active = len(background_tasks)
            if active > 0:
                logger.info(f" Active worker tasks: {active}/{max_concurrent}")

            # Small delay before next poll (matches req-copilot pattern).
            await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"SQS consumer loop error: {e}")
            await asyncio.sleep(5)
