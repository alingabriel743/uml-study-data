"""
IR Generation API endpoint.
Generates IR from code using Tree-sitter extractors.
Supports SQS async mode (APP_SQS_ENABLED=true) matching v1 operation name 'generate_ir'.
"""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Path as PathParam

from app.models.schemas import IRGenerationRequest, IRGenerationResponse
from app.utils.ir_extraction.postprocess_ir import postprocess_ir
from app.utils.config import settings
from app.extractors.sql_extractor import SqlTreeSitterExtractor
from app.extractors.java_extractor import JavaTreeSitterExtractor
from app.extractors.csharp_extractor import CSharpTreeSitterExtractor
from app.extractors.javascript_extractor import JavaScriptTreeSitterExtractor
from app.extractors.cpp_extractor import CppTreeSitterExtractor
from app.extractors.kotlin_extractor import KotlinTreeSitterExtractor
from app.extractors.cobol_extractor import CobolTreeSitterExtractor
from app.extractors.php_extractor import PHPTreeSitterExtractor
from app.extractors.python_extractor import PythonTreeSitterExtractor
from app.extractors.perl_extractor import PerlTreeSitterExtractor
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/ai")

LANGUAGE_EXTRACTOR_MAP = {
    "cobol": CobolTreeSitterExtractor,
    "cob": CobolTreeSitterExtractor,
    "cbl": CobolTreeSitterExtractor,
    "sql": SqlTreeSitterExtractor,
    "java": JavaTreeSitterExtractor,
    "c#": CSharpTreeSitterExtractor,
    "csharp": CSharpTreeSitterExtractor,
    "javascript": JavaScriptTreeSitterExtractor,
    "js": JavaScriptTreeSitterExtractor,
    "typescript": JavaScriptTreeSitterExtractor,
    "ts": JavaScriptTreeSitterExtractor,
    "cpp": CppTreeSitterExtractor,
    "c++": CppTreeSitterExtractor,
    "kotlin": KotlinTreeSitterExtractor,
    "php": PHPTreeSitterExtractor,
    "python": PythonTreeSitterExtractor,
    "py": PythonTreeSitterExtractor,
    "perl": PerlTreeSitterExtractor,
    "pl": PerlTreeSitterExtractor,
    "pm": PerlTreeSitterExtractor,
}


def _send_ir_to_sqs(job_id: str, project_id: str, language: str) -> None:
    """Dispatch generate_ir job to SQS."""
    from app.utils.sqs_client import get_sqs_client, get_queue_url
    from app.models.sqs_models import DiagramRequestMessageDto, DiagramOperationType

    client = get_sqs_client()
    queue_url = get_queue_url(settings.sqs_request_queue_name)

    msg = DiagramRequestMessageDto(
        projectId=project_id,
        operation=DiagramOperationType.GENERATE_IR,
        data={"language": language},
        timestamp=int(time.time() * 1000),
        job_id=job_id,
        request_id=job_id,
    )

    body = msg.model_dump_json()
    dedup_id = hashlib.sha256(f"{job_id}-{int(time.time()*1000)}".encode()).hexdigest()

    logger.info(f" Dispatching generate_ir job {job_id} to SQS (language={language})")
    from app.utils.sqs_client import send_sqs_message
    send_sqs_message(
        client,
        queue_url=queue_url,
        message_body=body,
        message_group_id=f"{project_id}-generate_ir",
        message_deduplication_id=dedup_id,
    )
    # No job file persisted — v1 pattern uses the response queue for status tracking.
    logger.info(f" generate_ir job {job_id} enqueued")


@router.post("/generate_ir/{project_id}", response_model=IRGenerationResponse)
async def generate_ir(
    project_id: str = PathParam(..., description="The project ID"),
    request: IRGenerationRequest = None,
):
    """Generate IR from uploaded code using Tree-sitter extractors.
    
    In SQS mode (APP_SQS_ENABLED=true), dispatches 'generate_ir' job to the queue
    and returns immediately with status='queued'.
    """
    language = request.language.lower() if request and request.language else "none"
    logger.info(f"IR generation request: project={project_id} language={language}")

    # Validate project exists
    repo_path = Path("data") / project_id
    if not repo_path.exists():
        return IRGenerationResponse(
            status="error",
            project_id=project_id,
            ir_file=f"Project {project_id} not found",
            stats={"error": 1},
        )

    # Validate language
    extractor_class = LANGUAGE_EXTRACTOR_MAP.get(language)
    if not extractor_class:
        available = ", ".join(sorted(set(LANGUAGE_EXTRACTOR_MAP.keys())))
        return IRGenerationResponse(
            status="error",
            project_id=project_id,
            ir_file=f"Language '{language}' not supported. Available: {available}",
            stats={"error": 1},
        )

    # ── SQS async mode ──
    if settings.sqs_enabled:
        job_id = str(uuid4())
        _send_ir_to_sqs(job_id, project_id, language)
        return IRGenerationResponse(
            status="queued",
            project_id=project_id,
            ir_file=f"Job queued: {job_id}. Poll GET /ai/jobs/{project_id}/{job_id}",
            stats={},
        )

    # ── Sync mode (default) ──
    try:
        logger.info(f"Using extractor: {extractor_class.__name__}")
        loop = asyncio.get_event_loop()

        # Step 1: Extract IR
        extractor = extractor_class(str(repo_path))
        ir = await loop.run_in_executor(None, extractor.extract_ir)
        ir_path = str(repo_path / "ir.json")
        extractor.save_ir(ir_path)
        logger.info(f"IR extracted to: {ir_path}")

        # Step 2: Postprocess IR
        postprocessed_path = await loop.run_in_executor(None, postprocess_ir, ir_path)
        logger.info(f"IR postprocessed: {postprocessed_path}")

        # Calculate stats from postprocessed file
        try:
            final_path = postprocessed_path if postprocessed_path else ir_path
            with open(final_path, "r") as f:
                actual_ir = json.load(f)
            stats = {
                "tables": len(actual_ir.get("tables", [])),
                "procedures": len(actual_ir.get("procedures", [])),
                "functions": len(actual_ir.get("functions", [])),
                "classes": len(actual_ir.get("classes", [])),
            }
        except Exception as e:
            logger.error(f"Failed to read IR for stats: {e}")
            stats = {"error": 1}

        logger.info(f"IR stats: {stats}")

        return IRGenerationResponse(
            status="ok",
            project_id=project_id,
            ir_file=postprocessed_path if postprocessed_path else ir_path,
            stats=stats,
        )

    except Exception as e:
        logger.error(f"IR generation failed: {e}")
        return IRGenerationResponse(
            status="error",
            project_id=project_id,
            ir_file=f"Exception: {str(e)}",
            stats={"error": 1},
        )
