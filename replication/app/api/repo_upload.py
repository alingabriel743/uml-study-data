"""
Repository Upload API endpoint.
Supports: GitHub clone, local directory reference, and simple ZIP extraction.
Supports SQS async mode (APP_SQS_ENABLED=true) matching v1 operation name 'upload_repo'.
"""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from collections import Counter
from uuid import uuid4

from fastapi import APIRouter, Path as PathParam, UploadFile, File, Form
from fastapi.responses import JSONResponse

from app.models.schemas import RepoInput, RepoResponse, LanguageDistribution
from app.utils.config import load_config, settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/ai")
config = load_config()

DATA_DIR = Path("data")

# Language detection mapping: file extension → language name
EXTENSION_LANGUAGE_MAP = {
    ".java": "java",
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".cs": "csharp",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".h": "cpp",
    ".kt": "kotlin", ".kts": "kotlin",
    ".cob": "cobol", ".cbl": "cobol",
    ".php": "php",
    ".pl": "perl", ".pm": "perl", ".t": "perl",
    ".sql": "sql", ".plsql": "sql",
}

# Map detected language to extractor-compatible language key
LANGUAGE_MAPPING = {
    "java": "java",
    "python": "python",
    "javascript": "javascript",
    "typescript": "javascript",  # Uses same extractor
    "csharp": "csharp",
    "cpp": "cpp",
    "kotlin": "kotlin",
    "cobol": "cobol",
    "php": "php",
    "perl": "perl",
    "sql": "sql",
}


def _send_upload_repo_to_sqs(job_id: str, project_id: str, data: RepoInput) -> None:
    """Dispatch upload_repo job to SQS."""
    from app.utils.sqs_client import get_sqs_client, get_queue_url
    from app.models.sqs_models import DiagramRequestMessageDto, DiagramOperationType

    client = get_sqs_client()
    queue_url = get_queue_url(settings.sqs_request_queue_name)

    msg = DiagramRequestMessageDto(
        projectId=project_id,
        operation=DiagramOperationType.UPLOAD_REPO,
        data={
            "url": data.url,
            "location": data.location,
            "language": data.language,
            "token": data.token,
        },
        timestamp=int(time.time() * 1000),
        job_id=job_id,
        request_id=job_id,
    )

    body = msg.model_dump_json()
    dedup_id = hashlib.sha256(f"{job_id}-{int(time.time()*1000)}".encode()).hexdigest()

    logger.info(f" Dispatching upload_repo job {job_id} to SQS")
    from app.utils.sqs_client import send_sqs_message
    send_sqs_message(
        client,
        queue_url=queue_url,
        message_body=body,
        message_group_id=f"{project_id}-upload_repo",
        message_deduplication_id=dedup_id,
    )
    # Note: no job file persisted here — v1 pattern uses response queue for status,
    # not disk files. The project directory is NOT pre-created so git clone works cleanly.
    logger.info(f" upload_repo job {job_id} enqueued")


@router.post("/upload_repo/", response_model=RepoResponse)
async def upload_repo(data: RepoInput, project_id: str):
    """
    Upload a repository for analysis.

    Supports:
    - GitHub URLs (public or private with token) — can be slow, benefits from SQS
    - Local directory paths (via 'location' field)

    In SQS mode (APP_SQS_ENABLED=true), dispatches 'upload_repo' job to the queue.
    """
    # ── SQS async mode — GitHub clone can be very slow ──
    if settings.sqs_enabled and data.url:
        # Do NOT pre-create the project directory — the worker will create it during git clone.
        # Pre-creating causes "already exists and is not an empty directory" git clone errors.
        job_id = str(uuid4())
        _send_upload_repo_to_sqs(job_id, project_id, data)

        return RepoResponse(
            status="queued",
            project_id=project_id,
            language=data.language,
            detected_language=f"Job queued: {job_id}. Poll GET /ai/jobs/{project_id}/{job_id}",
            source_type="github",
        )

    # ── Sync mode (default) or local path ──
    try:
        if data.url:
            result = await _process_github_repo(data, project_id)
        elif data.location:
            result = await _process_local_or_path(data, project_id)
        else:
            return RepoResponse(
                status="error",
                project_id=project_id,
                language=data.language,
                detected_language="Either 'url' or 'location' must be provided",
                source_type="unknown",
            )
        return result

    except Exception as e:
        logger.error(f"Upload failed for project {project_id}: {e}")
        return RepoResponse(
            status="error",
            project_id=project_id,
            language=data.language,
            detected_language=str(e),
            source_type="unknown",
        )


@router.post("/upload_file/")
async def upload_file(
    project_id: str = Form(...),
    language: str = Form("sql"),
    file: UploadFile = File(...),
):
    """Upload a ZIP file containing source code."""
    try:
        project_dir = DATA_DIR / project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        # Save uploaded file
        zip_path = project_dir / file.filename
        with zip_path.open("wb") as f:
            content = await file.read()
            f.write(content)

        # Extract if ZIP
        if file.filename.endswith(".zip"):
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(project_dir)
            zip_path.unlink()  # Remove ZIP after extraction

        # Detect language
        detected = _detect_language(project_dir)
        mapped = LANGUAGE_MAPPING.get(detected, language)
        distribution = _get_language_distribution(project_dir)

        return {
            "status": "ok",
            "project_id": project_id,
            "language": mapped,
            "detected_language": detected,
            "mapped_language": mapped,
            "source_type": "upload",
            "total_files": _count_code_files(project_dir),
            "language_distribution": [
                {"language": d.language, "file_count": d.file_count, "percentage": d.percentage}
                for d in distribution
            ],
        }

    except Exception as e:
        logger.error(f"File upload failed: {e}")
        return JSONResponse(
            content={"status": "error", "message": str(e)},
            status_code=500,
        )


async def _process_github_repo(data: RepoInput, project_id: str) -> RepoResponse:
    """Clone a GitHub repository."""
    logger.info(f"Cloning GitHub repo: {data.url}")
    project_dir = DATA_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_event_loop()

    try:
        # Build clone URL with token if provided
        clone_url = data.url
        if data.token and "github.com" in data.url:
            # Insert token for authentication
            clone_url = data.url.replace("https://", f"https://{data.token}@")

        # Clone the repo
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, str(project_dir)],
                check=True,
                capture_output=True,
                timeout=120,
            ),
        )
        logger.info(f"Repository cloned to {project_dir}")

        # Remove .git directory to save space
        git_dir = project_dir / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir)

        # Detect language
        detected = _detect_language(project_dir)
        mapped = LANGUAGE_MAPPING.get(detected, data.language)
        distribution = _get_language_distribution(project_dir)
        total_files = _count_code_files(project_dir)

        return RepoResponse(
            status="ok",
            project_id=project_id,
            language=mapped,
            detected_language=detected,
            mapped_language=mapped,
            main_language=detected,
            language_distribution=distribution,
            total_files=total_files,
            detection_confidence="high" if total_files > 5 else "low",
            source_type="github",
        )

    except subprocess.TimeoutExpired:
        return RepoResponse(
            status="error",
            project_id=project_id,
            language=data.language,
            detected_language="Clone timed out after 120 seconds",
            source_type="github",
        )
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        return RepoResponse(
            status="error",
            project_id=project_id,
            language=data.language,
            detected_language=f"Git clone failed: {error_msg}",
            source_type="github",
        )


async def _process_local_or_path(data: RepoInput, project_id: str) -> RepoResponse:
    """Process a local directory or path."""
    logger.info(f"Processing local path: {data.location}")
    source_path = Path(data.location)

    if not source_path.exists():
        return RepoResponse(
            status="error",
            project_id=project_id,
            language=data.language,
            detected_language=f"Path not found: {data.location}",
            source_type="local",
        )

    project_dir = DATA_DIR / project_id

    if source_path.is_dir():
        # Copy directory contents
        if project_dir.exists():
            shutil.rmtree(project_dir)
        shutil.copytree(source_path, project_dir)
    elif source_path.suffix == ".zip":
        # Extract ZIP
        project_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source_path, "r") as zf:
            zf.extractall(project_dir)
    else:
        return RepoResponse(
            status="error",
            project_id=project_id,
            language=data.language,
            detected_language=f"Unsupported file type: {source_path.suffix}",
            source_type="local",
        )

    detected = _detect_language(project_dir)
    mapped = LANGUAGE_MAPPING.get(detected, data.language)
    distribution = _get_language_distribution(project_dir)
    total_files = _count_code_files(project_dir)

    return RepoResponse(
        status="ok",
        project_id=project_id,
        language=mapped,
        detected_language=detected,
        mapped_language=mapped,
        main_language=detected,
        language_distribution=distribution,
        total_files=total_files,
        detection_confidence="high" if total_files > 5 else "low",
        source_type="local",
    )


def _detect_language(project_dir: Path) -> str:
    """Detect the main programming language in a project directory."""
    lang_counts: Counter = Counter()

    for f in project_dir.rglob("*"):
        if f.is_file():
            ext = f.suffix.lower()
            lang = EXTENSION_LANGUAGE_MAP.get(ext)
            if lang:
                lang_counts[lang] += 1

    if not lang_counts:
        return "unknown"

    return lang_counts.most_common(1)[0][0]


def _get_language_distribution(project_dir: Path) -> list[LanguageDistribution]:
    """Get language distribution for a project."""
    lang_counts: Counter = Counter()

    for f in project_dir.rglob("*"):
        if f.is_file():
            ext = f.suffix.lower()
            lang = EXTENSION_LANGUAGE_MAP.get(ext)
            if lang:
                lang_counts[lang] += 1

    total = sum(lang_counts.values())
    if total == 0:
        return []

    return [
        LanguageDistribution(
            language=lang,
            file_count=count,
            percentage=round(count / total, 3),
        )
        for lang, count in lang_counts.most_common()
    ]


def _count_code_files(project_dir: Path) -> int:
    """Count total code files in project directory."""
    count = 0
    for f in project_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in EXTENSION_LANGUAGE_MAP:
            count += 1
    return count
