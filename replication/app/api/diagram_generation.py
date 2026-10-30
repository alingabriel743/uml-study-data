"""
Diagram Generation API endpoint — uses the multi-agent orchestrator.

Supports two modes:
  SYNC  (default, APP_SQS_ENABLED=false): runs the orchestrator directly, returns full response.
  ASYNC (APP_SQS_ENABLED=true):           sends job to SQS, returns job_id immediately.
                                           Frontend polls GET /ai/jobs/{job_id} for results.

Pipeline:
  Phase 0: IR View (Python) → Phase 1: Planning (agent) → Phase 2: Analysis (parallel agents) → Phase 3: Generation (parallel agents)

For simple types or small projects, phases 1-2 are skipped.
"""

import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Path as PathParam, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent.orchestrator import DiagramOrchestrator
from app.models.schemas import (
    DiagramGenerationRequest,
    DiagramGenerationResponse,
    DiagramOutput,
    BatchDiagramRequest,
    BatchDiagramResponse,
    BatchDiagramTypeResult,
)
from app.utils.config import get_supported_diagram_types, settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/ai")


# ── SQS helpers ────────────────────────────────────────────────────

def _send_to_sqs(job_id: str, project_id: str, operation: str, data: dict) -> None:
    """Send a diagram generation job to the SQS request queue."""
    from app.utils.sqs_client import get_sqs_client, get_queue_url
    from app.models.sqs_models import DiagramRequestMessageDto, DiagramOperationType

    client = get_sqs_client()
    queue_url = get_queue_url(settings.sqs_request_queue_name)
    queue_name = queue_url.split("/")[-1]

    msg = DiagramRequestMessageDto(
        projectId=project_id,
        operation=DiagramOperationType(operation),
        data=data,
        timestamp=int(time.time() * 1000),
        job_id=job_id,
        request_id=job_id,
    )

    body = msg.model_dump_json()
    size_bytes = len(body.encode("utf-8"))
    size_mb = size_bytes / 1024 / 1024
    dedup_id = hashlib.sha256(f"{job_id}-{int(time.time()*1000)}".encode()).hexdigest()
    group_id = f"{project_id}-{operation}"

    logger.info(f"")
    logger.info(f"{'─' * 60}")
    logger.info(f" SENDING TO SQS")
    logger.info(f"{'─' * 60}")
    logger.info(f"   Queue:     {queue_name}")
    logger.info(f"   Job ID:    {job_id}")
    logger.info(f"   Operation: {operation}")
    logger.info(f"   Project:   {project_id}")
    logger.info(f"   Group ID:  {group_id}")
    logger.info(f"   Dedup ID:  {dedup_id[:16]}...")
    logger.info(f"   Payload:   {size_mb:.2f} MB ({size_bytes:,} bytes)")
    if data:
        for k, v in data.items():
            logger.info(f"   Data.{k:<10} {v}")
    logger.info(f"{'─' * 60}")

    # Use job_id as the MessageGroupId so each job gets its own FIFO group.
    # If all jobs shared one group, SQS would deliver them one-at-a-time (sequential).
    from app.utils.sqs_client import send_sqs_message
    send_sqs_message(
        client,
        queue_url=queue_url,
        message_body=body,
        message_group_id=job_id,
        message_deduplication_id=dedup_id,
    )
    logger.info(f" Job {job_id} enqueued — poll GET /ai/jobs/{project_id}/{job_id}")


def _write_job_queued(project_id: str, job_id: str, operation: str, data: dict) -> None:
    """Persist a 'queued' job stub so the status endpoint can return it immediately."""
    jobs_dir = Path("data") / project_id / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_file = jobs_dir / f"{job_id}.json"
    with job_file.open("w", encoding="utf-8") as f:
        json.dump({
            "job_id": job_id,
            "project_id": project_id,
            "operation": operation,
            "status": "queued",
            "data": None,
            "timestamp": int(time.time() * 1000),
        }, f, indent=2)


@router.post(
    "/generate_diagram/{project_id}",
    response_model=DiagramGenerationResponse,
)
async def generate_diagram(
    project_id: str = PathParam(..., description="The project ID whose IR should be used"),
    request: DiagramGenerationRequest = None,
):
    """
    Generate UML diagram(s) for a project using the multi-agent orchestrator.

    For class/sequence/activity/use_case diagrams on large projects, this generates
    MULTIPLE focused diagrams (one per workflow/module) in parallel.
    """
    diagram_type = request.diagram_type if request else "class"
    options = request.options if request else None
    start_time = time.time()

    logger.info(f"")
    logger.info(f"{'=' * 70}")
    logger.info(f" DIAGRAM GENERATION REQUEST")
    logger.info(f"{'=' * 70}")
    logger.info(f"  Project ID:    {project_id}")
    logger.info(f"  Diagram Type:  {diagram_type}")
    logger.info(f"  Options:       {options}")
    logger.info(f"{'=' * 70}")

    # Validate diagram type
    supported = get_supported_diagram_types()
    if diagram_type not in supported:
        logger.error(f" Unsupported diagram type: '{diagram_type}'. Supported: {supported}")
        return DiagramGenerationResponse(
            status="error",
            project_id=project_id,
            diagram_type=diagram_type,
            error=f"Unsupported diagram type '{diagram_type}'. Supported: {supported}",
        )

    # Check that IR exists
    ir_path = Path("data") / project_id / "ir_postprocessed.json"
    ir_fallback = Path("data") / project_id / "ir.json"

    if ir_path.exists():
        logger.info(f" Found IR: {ir_path} ({ir_path.stat().st_size:,} bytes)")
        try:
            with ir_path.open("r") as f:
                ir_data = json.load(f)
            metadata = ir_data.get("metadata", {})
            total = sum(len(ir_data.get(k, [])) for k in ["classes", "functions", "tables", "procedures", "types"])
            logger.info(f"   Project: {metadata.get('projectName', 'N/A')} | "
                        f"Languages: {metadata.get('sourceLanguages', [])} | "
                        f"Elements: {total}")
        except Exception:
            pass
    elif ir_fallback.exists():
        logger.info(f"  Using fallback IR: {ir_fallback}")
    else:
        logger.error(f" No IR file found for project {project_id}")
        return DiagramGenerationResponse(
            status="error",
            project_id=project_id,
            diagram_type=diagram_type,
            error=f"No IR file found for project {project_id}. Run /generate_ir first.",
        )

    # ── SQS async mode ──
    if settings.sqs_enabled:
        job_id = str(uuid4())
        logger.info(f" SQS mode: dispatching job {job_id}")
        _write_job_queued(project_id, job_id, "generate_diagram", {
            "diagram_type": diagram_type,
            "options": options,
        })
        _send_to_sqs(job_id, project_id, "generate_diagram", {
            "diagram_type": diagram_type,
            "options": options,
        })
        return DiagramGenerationResponse(
            status="queued",
            project_id=project_id,
            diagram_type=diagram_type,
            agent_messages=[f"Job queued: {job_id}. Poll GET /ai/jobs/{project_id}/{job_id} for results."],
        )

    # ── Sync mode (default) ──
    try:
        orchestrator = DiagramOrchestrator(
            project_id=project_id,
            diagram_type=diagram_type,
            options=options,
        )
        result = await orchestrator.run()

        if result["status"] == "error":
            return DiagramGenerationResponse(
                status="error",
                project_id=project_id,
                diagram_type=diagram_type,
                error=result.get("error", "Unknown error"),
            )

        # Collect agent messages for the response
        agent_messages = []
        for msg in result.get("messages", []):
            if msg.get("type") == "assistant" and msg.get("text"):
                agent_messages.append(msg["text"][:500])
            elif msg.get("type") == "error":
                agent_messages.append(f"ERROR: {msg.get('message', '')}")

        # Read all generated .puml files
        diagrams = []
        for file_path in result.get("output_files", []):
            path = Path(file_path)
            if path.exists() and path.suffix == ".puml":
                plantuml_code = path.read_text(encoding="utf-8")
                element_count = _count_plantuml_elements(plantuml_code)

                diagrams.append(
                    DiagramOutput(
                        diagram_type=diagram_type,
                        plantuml_code=plantuml_code,
                        element_count=element_count,
                        filename=path.name,
                    )
                )

        total_duration = time.time() - start_time
        logger.info(f"")
        logger.info(f"{'=' * 70}")
        logger.info(f" API RESPONSE: {len(diagrams)} diagram(s), {total_duration:.1f}s")
        logger.info(f"{'=' * 70}")

        return DiagramGenerationResponse(
            status=result.get("status", "ok"),  # "partial" when scopes failed (see run_manifest.json)
            project_id=project_id,
            diagram_type=diagram_type,
            diagrams=diagrams,
            agent_messages=agent_messages,
        )

    except Exception as e:
        total_duration = time.time() - start_time
        logger.error(f" DIAGRAM GENERATION FAILED: {e} ({total_duration:.1f}s)", exc_info=True)
        return DiagramGenerationResponse(
            status="error",
            project_id=project_id,
            diagram_type=diagram_type,
            error=str(e),
        )


@router.post(
    "/generate_diagrams_batch/{project_id}",
    response_model=BatchDiagramResponse,
)
async def generate_diagrams_batch(
    project_id: str = PathParam(..., description="The project ID"),
    request: BatchDiagramRequest = None,
):
    """
    Generate MULTIPLE diagram types in PARALLEL for a project.

    Use this when the user selects multiple diagram types at once
    (e.g., class + sequence + component). Each type runs its own
    orchestrator concurrently.
    """
    diagram_types = request.diagram_types if request else ["class"]
    options = request.options if request else None
    start_time = time.time()

    supported = get_supported_diagram_types()
    # Validate all types
    invalid = [t for t in diagram_types if t not in supported]
    if invalid:
        return BatchDiagramResponse(
            status="error",
            project_id=project_id,
            results=[BatchDiagramTypeResult(diagram_type=t, status="error", error=f"Unsupported type") for t in invalid],
        )

    logger.info(f"")
    logger.info(f"{'=' * 70}")
    logger.info(f" BATCH DIAGRAM GENERATION")
    logger.info(f"{'=' * 70}")
    logger.info(f"   Project:  {project_id}")
    logger.info(f"   Types:    {diagram_types}")
    logger.info(f"   Parallel: {len(diagram_types)} orchestrators")
    logger.info(f"{'=' * 70}")

    # ── SQS async mode — dispatch N individual generate_diagram jobs ──
    # (generate_diagram_batch is not a v1 operation; each type gets its own message)
    if settings.sqs_enabled:
        job_ids = []
        for dtype in diagram_types:
            job_id = str(uuid4())
            _write_job_queued(project_id, job_id, "generate_diagram", {
                "diagramType": dtype,
                "options": options,
            })
            _send_to_sqs(job_id, project_id, "generate_diagram", {
                "diagramType": dtype,
                "options": options,
            })
            job_ids.append(job_id)
            logger.info(f" Dispatched generate_diagram job {job_id} for {dtype}")
        logger.info(f" SQS mode: {len(job_ids)} individual generate_diagram jobs dispatched")
        return BatchDiagramResponse(
            status="queued",
            project_id=project_id,
            results=[],
            total_diagrams=0,
            total_duration_seconds=0,
        )

    # ── Sync mode (default) ──
    # Run all diagram types in parallel
    async def run_one_type(dtype: str) -> BatchDiagramTypeResult:
        type_start = time.time()
        try:
            orchestrator = DiagramOrchestrator(
                project_id=project_id,
                diagram_type=dtype,
                options=options,
            )
            result = await orchestrator.run()
            duration = time.time() - type_start

            if result["status"] == "error":
                return BatchDiagramTypeResult(
                    diagram_type=dtype, status="error",
                    error=result.get("error"), duration_seconds=round(duration, 1),
                )

            # Read .puml files
            diagrams = []
            for fp in result.get("output_files", []):
                p = Path(fp)
                if p.exists() and p.suffix == ".puml":
                    code = p.read_text(encoding="utf-8")
                    diagrams.append(DiagramOutput(
                        diagram_type=dtype,
                        plantuml_code=code,
                        element_count=_count_plantuml_elements(code),
                        filename=p.name,
                    ))

            return BatchDiagramTypeResult(
                diagram_type=dtype, status=result.get("status", "ok"),
                diagrams=diagrams,
                duration_seconds=round(duration, 1),
            )
        except Exception as e:
            duration = time.time() - type_start
            logger.error(f" Batch: {dtype} failed: {e}")
            return BatchDiagramTypeResult(
                diagram_type=dtype, status="error",
                error=str(e), duration_seconds=round(duration, 1),
            )

    import asyncio
    results = await asyncio.gather(*[run_one_type(dt) for dt in diagram_types])

    total_duration = time.time() - start_time
    total_diagrams = sum(len(r.diagrams) for r in results)

    logger.info(f"")
    logger.info(f"{'=' * 70}")
    logger.info(f" BATCH COMPLETE: {total_diagrams} diagrams, {total_duration:.1f}s")
    for r in results:
        logger.info(f"   {r.diagram_type}: {r.status} ({len(r.diagrams)} diagrams, {r.duration_seconds}s)")
    logger.info(f"{'=' * 70}")

    return BatchDiagramResponse(
        status="ok",
        project_id=project_id,
        results=list(results),
        total_diagrams=total_diagrams,
        total_duration_seconds=round(total_duration, 1),
    )


# ── Job status endpoint ────────────────────────────────────────────

@router.get("/jobs/{project_id}/{job_id}")
async def get_job_status(
    project_id: str = PathParam(..., description="The project ID"),
    job_id: str = PathParam(..., description="The job ID returned by the generation endpoint"),
):
    """
    Poll the status of an async diagram generation job.

    Returns:
      - status: queued | processing | complete | error
      - data: full generation result when status == complete
      - error: error message when status == error
    """
    job_file = Path("data") / project_id / "jobs" / f"{job_id}.json"
    if not job_file.exists():
        return JSONResponse(
            content={"error": f"Job {job_id} not found"},
            status_code=404,
        )
    with job_file.open("r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/metrics/{project_id}")
async def get_project_metrics(
    project_id: str = PathParam(..., description="The project ID"),
):
    """
    Get persisted generation metrics for all diagram types of a project.
    Returns metrics per diagram type + aggregated totals.
    """
    diagrams_dir = Path("data") / project_id / "diagrams"
    if not diagrams_dir.exists():
        return {"project_id": project_id, "metrics_by_type": {}, "aggregate": None}

    metrics_by_type: dict[str, dict] = {}
    for type_dir in sorted(diagrams_dir.iterdir()):
        if type_dir.is_dir():
            metrics_file = type_dir / "metrics.json"
            if metrics_file.exists():
                try:
                    with metrics_file.open("r", encoding="utf-8") as f:
                        metrics_by_type[type_dir.name] = json.load(f)
                except Exception:
                    pass

    # Compute aggregate across all types
    aggregate = None
    if metrics_by_type:
        all_m = list(metrics_by_type.values())
        total_diagrams = sum(m.get("total_diagrams", 0) for m in all_m)
        total_elements = sum(m.get("total_elements_in_diagrams", 0) for m in all_m)
        total_ir = max(m.get("total_ir_elements", 1) for m in all_m)
        coverage = min((total_elements / max(total_ir, 1)) * 100, 100)
        avg_complexity = sum(m.get("diagram_complexity_score", 0) for m in all_m) / len(all_m)
        avg_validity = sum(m.get("syntax_validity_pct", 100) for m in all_m) / len(all_m)
        avg_efficiency = sum(m.get("token_efficiency", 0) for m in all_m) / len(all_m)
        total_time = sum(m.get("generation_time_seconds", 0) for m in all_m)

        aggregate = {
            "code_coverage_pct": round(coverage, 1),
            "generation_time_seconds": round(total_time, 1),
            "diagram_complexity_score": round(avg_complexity, 1),
            "syntax_validity_pct": round(avg_validity, 1),
            "token_efficiency": round(avg_efficiency, 2),
            "total_diagrams": total_diagrams,
            "total_elements_in_diagrams": total_elements,
            "total_ir_elements": total_ir,
        }

    return {
        "project_id": project_id,
        "metrics_by_type": metrics_by_type,
        "aggregate": aggregate,
    }


@router.get("/diagram_types")
async def list_diagram_types():
    """List all supported diagram types."""
    return {"diagram_types": get_supported_diagram_types()}


# ── Description generation endpoint ───────────────────────────────

class DescriptionRequest(BaseModel):
    diagram_type: str = "class"
    content: str = ""
    diagram_id: str | None = None
    flow_id: str | None = None


@router.post("/generate_description/{project_id}")
async def generate_description(
    project_id: str = PathParam(..., description="The project ID"),
    request: DescriptionRequest = None,
):
    """Generate a text description of a diagram (sync, no SQS needed)."""
    from app.agent.base_agent import BaseAgent

    diagram_type = request.diagram_type if request else "class"
    content = request.content if request else ""

    if not content:
        # Try to read from disk
        diagrams_dir = Path("data") / project_id / "diagrams" / diagram_type
        if diagrams_dir.exists():
            puml_files = sorted(diagrams_dir.glob("*.puml"))
            if puml_files:
                content = puml_files[0].read_text(encoding="utf-8")

    if not content:
        return JSONResponse(content={"error": "No diagram content to describe"}, status_code=400)

    try:
        system_prompt = (
            "You are a software architecture documentation expert. "
            "Given a PlantUML diagram, produce a clear, concise description. "
            "Focus on key components, relationships, and architectural patterns. "
            "Output ONLY the description text."
        )

        class _DescAgent(BaseAgent):
            async def run(self, **kwargs) -> dict:
                return {}

        agent = _DescAgent(
            name="DescriptionAgent",
            system_prompt=system_prompt,
            tools=["Read", "Glob"],
            permission_mode="bypassPermissions",
        )

        prompt = (
            f"Describe this {diagram_type} PlantUML diagram:\n\n"
            f"```plantuml\n{content}\n```\n\n"
            "Provide a 2-4 paragraph description."
        )

        description = ""
        async for message in agent.execute(prompt):
            if message.get("type") == "assistant" and message.get("text"):
                description = message["text"]

        # Save to disk
        desc_dir = Path("data") / project_id / "diagrams" / diagram_type
        desc_dir.mkdir(parents=True, exist_ok=True)
        desc_path = desc_dir / f"{diagram_type}_description.md"
        desc_path.write_text(description, encoding="utf-8")

        return {
            "project_id": project_id,
            "diagram_type": diagram_type,
            "diagram_description": description,
            "description_path": str(desc_path),
        }

    except Exception as e:
        logger.error(f"Description generation failed: {e}", exc_info=True)
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ── Diagram regeneration endpoint ─────────────────────────────────

class RegenerateRequest(BaseModel):
    diagram_type: str = "class"
    user_feedback: str = ""
    diagram_id: str | None = None


@router.post("/regenerate_diagram/{project_id}")
async def regenerate_diagram(
    project_id: str = PathParam(..., description="The project ID"),
    request: RegenerateRequest = None,
):
    """Regenerate a diagram with user feedback (sync, no SQS needed)."""
    diagram_type = request.diagram_type if request else "class"
    user_feedback = request.user_feedback if request else ""

    if not user_feedback:
        return JSONResponse(content={"error": "user_feedback is required"}, status_code=400)

    try:
        orchestrator = DiagramOrchestrator(
            project_id=project_id,
            diagram_type=diagram_type,
            options={"user_feedback": user_feedback, "regeneration": True},
        )
        result = await orchestrator.run()

        if result["status"] == "error":
            return JSONResponse(
                content={"error": result.get("error", "Regeneration failed")},
                status_code=500,
            )

        diagram_code = ""
        for fp in result.get("output_files", []):
            p = Path(fp)
            if p.exists() and p.suffix == ".puml":
                diagram_code = p.read_text(encoding="utf-8")
                break

        return {
            "project_id": project_id,
            "diagram_type": diagram_type,
            "diagram_code": diagram_code,
            "status": "ok",
            "message": "Diagram regenerated with user feedback",
        }

    except Exception as e:
        logger.error(f"Regeneration failed: {e}", exc_info=True)
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/diagrams/{project_id}")
async def get_diagrams(
    project_id: str = PathParam(..., description="The project ID"),
):
    """List all generated diagrams for a project, organized by type folder."""
    diagrams_dir = Path("data") / project_id / "diagrams"
    if not diagrams_dir.exists():
        return {"project_id": project_id, "diagram_types": {}, "total_diagrams": 0}

    diagram_types: dict[str, list] = {}

    # Scan subfolders (class/, sequence/, etc.)
    for type_dir in sorted(diagrams_dir.iterdir()):
        if type_dir.is_dir():
            type_name = type_dir.name
            type_diagrams = []
            for f in sorted(type_dir.iterdir()):
                if f.is_file() and f.suffix == ".puml":
                    type_diagrams.append({
                        "filename": f.name,
                        "path": f"{type_name}/{f.name}",
                        "size_bytes": f.stat().st_size,
                    })
            if type_diagrams:
                diagram_types[type_name] = type_diagrams

    # Also check root-level .puml files (legacy)
    root_diagrams = []
    for f in sorted(diagrams_dir.iterdir()):
        if f.is_file() and f.suffix == ".puml":
            root_diagrams.append({
                "filename": f.name,
                "path": f.name,
                "size_bytes": f.stat().st_size,
            })
    if root_diagrams:
        diagram_types["_root"] = root_diagrams

    total = sum(len(v) for v in diagram_types.values())
    return {"project_id": project_id, "diagram_types": diagram_types, "total_diagrams": total}


@router.get("/diagrams/{project_id}/{diagram_path:path}")
async def get_diagram_content(
    project_id: str = PathParam(..., description="The project ID"),
    diagram_path: str = PathParam(..., description="Diagram path (e.g., class/class_diagram.puml)"),
):
    """Get the content of a specific diagram file. Supports subfolder paths."""
    file_path = Path("data") / project_id / "diagrams" / diagram_path
    if not file_path.exists():
        return JSONResponse(content={"error": f"File not found: {diagram_path}"}, status_code=404)
    return {"filename": file_path.name, "path": diagram_path, "content": file_path.read_text(encoding="utf-8")}


def _count_plantuml_elements(plantuml_code: str) -> int:
    """Rough count of elements in PlantUML code."""
    keywords = ["class ", "interface ", "abstract ", "enum ", "actor ", "usecase ",
                 "participant ", "component ", "node ", "database ", "rectangle "]
    return sum(1 for line in plantuml_code.split("\n")
               if any(line.strip().startswith(kw) for kw in keywords))
