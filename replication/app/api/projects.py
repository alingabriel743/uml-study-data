"""Projects listing endpoint — scans data/ folder for existing projects.

Performance: single rglob per project, results cached for 10s to avoid
hammering the filesystem on every poll cycle.
"""

import asyncio
import shutil
import time
from pathlib import Path
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Path as PathParam
from fastapi.responses import JSONResponse
from app.utils.logger import get_logger
from app.api.repo_upload import EXTENSION_LANGUAGE_MAP, LANGUAGE_MAPPING

logger = get_logger(__name__)

router = APIRouter(prefix="/ai")

DATA_DIR = Path("data")

# ── In-memory cache ────────────────────────────────────────────────
_cache: dict = {"projects": None, "timestamp": 0.0}
_CACHE_TTL = 10  # seconds


def _scan_project(entry: Path) -> Optional[dict]:
    """Scan a single project directory in ONE pass (not 5 separate rglobs)."""
    project_id = entry.name
    lang_counts: Counter = Counter()
    has_ir = False
    diagram_files: list[str] = []
    code_file_count = 0

    try:
        for f in entry.rglob("*"):
            if not f.is_file():
                continue

            name = f.name
            ext = f.suffix.lower()

            # Check IR
            if not has_ir and (name.endswith("_ir.json") or name == "ir.json"):
                has_ir = True

            # Check diagrams
            if ext in (".puml", ".plantuml"):
                diagram_files.append(name)

            # Count languages
            lang = EXTENSION_LANGUAGE_MAP.get(ext)
            if lang:
                lang_counts[lang] += 1
                code_file_count += 1
    except OSError as e:
        logger.warning(f"Error scanning project {project_id}: {e}")
        return None

    # Derive fields from single scan
    detected = lang_counts.most_common(1)[0][0] if lang_counts else "unknown"
    total = sum(lang_counts.values())

    distribution = []
    if total > 0:
        distribution = [
            {"language": lang, "file_count": count, "percentage": round(count / total, 3)}
            for lang, count in lang_counts.most_common()
        ]

    return {
        "id": project_id,
        "name": project_id,
        "language": LANGUAGE_MAPPING.get(detected, detected) if detected != "unknown" else "sql",
        "detected_language": detected,
        "source": "local",
        "has_ir": has_ir,
        "total_files": code_file_count,
        "diagram_count": len(diagram_files),
        "diagram_files": diagram_files[:20],
        "language_distribution": distribution,
    }


def _scan_all_projects() -> list[dict]:
    """Scan all project directories (blocking I/O)."""
    if not DATA_DIR.exists():
        return []

    projects = []
    for entry in sorted(DATA_DIR.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            result = _scan_project(entry)
            if result:
                projects.append(result)
    return projects


@router.get("/projects")
async def list_projects():
    """List all projects in the data directory (cached, async-safe)."""
    now = time.time()

    # Return cached result if fresh
    if _cache["projects"] is not None and (now - _cache["timestamp"]) < _CACHE_TTL:
        return {"projects": _cache["projects"]}

    # Run blocking I/O in a thread to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    projects = await loop.run_in_executor(None, _scan_all_projects)

    _cache["projects"] = projects
    _cache["timestamp"] = time.time()

    return {"projects": projects}


def _invalidate_cache():
    """Clear the project list cache so the next request re-scans."""
    _cache["projects"] = None
    _cache["timestamp"] = 0.0


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str = PathParam(..., description="The project ID to delete"),
):
    """Delete a project and all its data (code, IR, diagrams, jobs)."""
    project_dir = DATA_DIR / project_id
    if not project_dir.exists():
        return JSONResponse(content={"error": f"Project {project_id} not found"}, status_code=404)

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, shutil.rmtree, project_dir)
        _invalidate_cache()
        logger.info(f"Deleted project {project_id}")
        return {"status": "ok", "project_id": project_id, "message": "Project deleted"}
    except Exception as e:
        logger.error(f"Failed to delete project {project_id}: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)
