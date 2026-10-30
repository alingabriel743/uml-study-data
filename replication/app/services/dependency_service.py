"""
DependencyService — orchestrates dependency ingestion for diagrams-v2.

Pipeline per dependency:
1. Download / clone source (file upload → S3, link → HTTP, git → clone)
2. Validate source contains code files
3. Run Tree-sitter IR extraction (reuses existing extractors)
4. Run DependencyAnalyzerAgent → writes dependency_context.json
5. Update project_metadata.json

No Weaviate. No LangGraph. The agent's output file is the only artifact
consumed downstream — diagram agents can Read it via the SDK built-in tool.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from app.agent.dependency_analyzer_agent import DependencyAnalyzerAgent
from app.models.dependency_models import DependencyDto
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Code extensions accepted during validation / language detection
_CODE_EXTENSIONS = {
    ".java", ".sql", ".cs", ".js", ".ts", ".jsx", ".tsx",
    ".cpp", ".cc", ".cxx", ".hpp", ".h", ".c",
    ".py", ".php", ".kt", ".cob", ".cbl", ".rb", ".go",
    ".rs", ".swift", ".scala", ".pl", ".r",
}

_EXT_TO_LANG: dict[str, str] = {
    ".java": "java",
    ".sql": "sql",
    ".cs": "csharp",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
    ".c": "c",
    ".py": "python",
    ".php": "php",
    ".kt": "kotlin",
    ".cob": "cobol",
    ".cbl": "cobol",
    ".pl": "perl",
    ".pm": "perl",
}


class DependencyService:
    """
    Orchestrates the full dependency ingestion pipeline.

    Usage:
        service = DependencyService(project_id)
        ok, failed = await service.process_dependencies(deps)
    """

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.project_path = Path("data") / project_id
        self.dependencies_path = self.project_path / "dependencies"
        self.dependencies_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def process_dependencies(
        self,
        dependencies: list[DependencyDto],
    ) -> tuple[int, list[str]]:
        """
        Process a list of dependencies sequentially.

        Returns:
            (successful_count, failed_dependency_names)
        """
        successful = 0
        failed: list[str] = []

        for dep in dependencies:
            try:
                await self._process_single(dep)
                successful += 1
                logger.info(f"DependencyService: processed {dep.name!r} successfully")
            except Exception as exc:
                logger.error(f"DependencyService: failed to process {dep.name!r}: {exc}")
                failed.append(dep.name)

        if successful > 0:
            await self._update_metadata(dependencies)

        return successful, failed

    async def delete_dependency(self, dependency_id: str) -> dict[str, Any]:
        """
        Delete a dependency by ID — removes files and metadata entry.

        Returns:
            dict with deleted_dependency info.
        """
        metadata_file = self.project_path / "project_metadata.json"
        if not metadata_file.exists():
            raise ValueError("Project has no dependencies")

        with metadata_file.open("r", encoding="utf-8") as fh:
            metadata = json.load(fh)

        dependencies: list[dict] = metadata.get("dependencies", [])
        target = next((d for d in dependencies if d.get("id") == dependency_id), None)

        if not target:
            raise ValueError(f"Dependency with ID '{dependency_id}' not found")

        dep_name: str = target["name"]
        sanitized = self._sanitize(dep_name)

        # Remove dependency directory
        dep_dir = self.dependencies_path / sanitized
        if dep_dir.exists():
            shutil.rmtree(dep_dir)

        # Remove analysis file (legacy / compat)
        analysis_file = self.dependencies_path / f"{sanitized}_analysis.json"
        if analysis_file.exists():
            analysis_file.unlink()

        # Remove from metadata
        metadata["dependencies"] = [
            d for d in dependencies if d.get("id") != dependency_id
        ]
        with metadata_file.open("w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2)

        logger.info(f"DependencyService: deleted dependency {dep_name!r} ({dependency_id})")
        return {"deleted_dependency": {"id": dependency_id, "name": dep_name}}

    def list_dependencies(self) -> list[dict]:
        """Return the list of dependencies from project_metadata.json."""
        metadata_file = self.project_path / "project_metadata.json"
        if not metadata_file.exists():
            return []
        with metadata_file.open("r", encoding="utf-8") as fh:
            return json.load(fh).get("dependencies", [])

    def get_dependency_context(self, dependency_name: str) -> dict[str, Any] | None:
        """
        Return the parsed dependency_context.json for a named dependency, or None.
        """
        ctx_file = (
            self.dependencies_path / self._sanitize(dependency_name) / "dependency_context.json"
        )
        if not ctx_file.exists():
            return None
        with ctx_file.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    # ------------------------------------------------------------------ #
    # Single dependency processing                                         #
    # ------------------------------------------------------------------ #

    async def _process_single(self, dep: DependencyDto) -> None:
        logger.info(
            f"DependencyService._process_single: {dep.name!r} "
            f"method={dep.uploadMethod}"
        )

        dep_dir = self.dependencies_path / self._sanitize(dep.name)
        dep_dir.mkdir(exist_ok=True)

        # 1. Acquire source
        if dep.uploadMethod == "file":
            source_path = await self._handle_file(dep, dep_dir)
        elif dep.uploadMethod == "link":
            source_path = await self._handle_link(dep, dep_dir)
        elif dep.uploadMethod in {"github", "gitlab", "bitbucket"}:
            source_path = await self._handle_git(dep, dep_dir)
        else:
            raise ValueError(f"Unsupported uploadMethod: {dep.uploadMethod!r}")

        if not source_path.exists():
            raise ValueError(f"Source path does not exist after download: {source_path}")

        # 2. Validate code files
        code_count = self._count_code_files(source_path)
        if code_count == 0:
            raise ValueError(
                f"No valid code files found in dependency '{dep.name}'. "
                "Ensure the archive contains recognised source files "
                "(.java, .py, .ts, .cs, .cpp, .php, .sql, ...)."
            )
        logger.info(f"DependencyService: {code_count} code files found for {dep.name!r}")

        # 3. Tree-sitter IR extraction (best-effort — non-fatal)
        await self._extract_ir(dep, source_path, dep_dir)

        # 4. Agent analysis → dependency_context.json
        output_path = dep_dir / "dependency_context.json"
        agent = DependencyAnalyzerAgent()
        agent_result = await agent.run(
            dependency_name=dep.name,
            dependency_type=dep.type,
            usage_type=dep.usageType,
            source_path=source_path.resolve(),
            output_path=output_path.resolve(),
        )

        if agent_result["status"] != "success":
            # Write a minimal fallback context so downstream agents aren't blocked
            self._write_fallback_context(dep, output_path, agent_result.get("error"))

        logger.info(
            f"DependencyService: agent analysis done for {dep.name!r} → "
            f"status={agent_result['status']}, elements={agent_result.get('element_count', 0)}"
        )

    # ------------------------------------------------------------------ #
    # Source acquisition helpers                                           #
    # ------------------------------------------------------------------ #

    async def _handle_file(self, dep: DependencyDto, dep_dir: Path) -> Path:
        """Handle S3 file-based dependency."""
        if not dep.data.fileLocation:
            raise ValueError("fileLocation is required for file dependencies")

        from app.utils.s3_client import download_file_from_s3
        from app.utils.config import load_config

        cfg = load_config()
        file_location = dep.data.fileLocation

        if file_location.startswith("s3://"):
            parts = file_location[5:].split("/", 1)
            bucket, key = parts[0], parts[1] if len(parts) > 1 else ""
        elif "/" in file_location:
            bucket, key = file_location.split("/", 1)
        else:
            bucket = cfg.get("s3", {}).get("documents_bucket", "codecatalyst-documents")
            key = file_location

        filename = dep.data.fileName or key.split("/")[-1]
        local_path = dep_dir / filename

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, download_file_from_s3, bucket, key, local_path)
        logger.info(f"DependencyService: downloaded S3 file → {local_path}")

        if local_path.suffix.lower() in {".zip", ".tar", ".tar.gz", ".tgz"}:
            extract_dir = dep_dir / "extracted"
            extract_dir.mkdir(exist_ok=True)
            await self._extract_archive(local_path, extract_dir)
            return extract_dir

        return local_path.parent

    async def _handle_link(self, dep: DependencyDto, dep_dir: Path) -> Path:
        """Handle public URL dependency — detects git URLs and delegates."""
        if not dep.data.url:
            raise ValueError("url is required for link dependencies")

        url_lower = dep.data.url.lower()
        if any(h in url_lower for h in ["github.com", "gitlab.com", "bitbucket.org", ".git"]):
            # Treat as public git repo
            return await self._clone_repo(dep.data.url, token=None, clone_dir=dep_dir / "repo")

        # Plain file download
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, requests.get, dep.data.url)
        response.raise_for_status()

        filename = dep.data.fileName or Path(urlparse(dep.data.url).path).name or "downloaded"
        local_path = dep_dir / filename
        local_path.write_bytes(response.content)
        logger.info(f"DependencyService: downloaded URL → {local_path}")

        if local_path.suffix.lower() in {".zip", ".tar", ".tar.gz", ".tgz"}:
            extract_dir = dep_dir / "extracted"
            extract_dir.mkdir(exist_ok=True)
            await self._extract_archive(local_path, extract_dir)
            return extract_dir

        return local_path.parent

    async def _handle_git(self, dep: DependencyDto, dep_dir: Path) -> Path:
        """Handle GitHub / GitLab / Bitbucket dependency."""
        if not dep.data.url:
            raise ValueError("url is required for git dependencies")

        token: str | None = None
        if dep.token:
            try:
                from app.utils.token_decryption import token_decryption_service

                token = token_decryption_service.process_token(dep.token)
            except Exception as exc:
                raise ValueError(f"Token processing failed: {exc}") from exc

        return await self._clone_repo(dep.data.url, token=token, clone_dir=dep_dir / "repo")

    async def _clone_repo(
        self, url: str, *, token: str | None, clone_dir: Path
    ) -> Path:
        """Clone a git repository using subprocess (same approach as repo_upload.py)."""
        import subprocess
        import shutil as _shutil

        clone_dir.mkdir(parents=True, exist_ok=True)

        # Embed token into URL for private repos
        clone_url = url
        if token:
            if "github.com" in url or "gitlab.com" in url or "bitbucket.org" in url:
                clone_url = url.replace("https://", f"https://{token}@")

        logger.info(f"DependencyService: cloning {url} → {clone_dir}")

        loop = asyncio.get_event_loop()

        def _do_clone() -> None:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, str(clone_dir)],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise ValueError(f"git clone failed: {result.stderr[:500]}")
            # Remove .git dir to save space
            git_dir = clone_dir / ".git"
            if git_dir.exists():
                _shutil.rmtree(git_dir)

        await loop.run_in_executor(None, _do_clone)
        logger.info(f"DependencyService: cloned successfully → {clone_dir}")
        return clone_dir

    async def _extract_archive(self, archive_path: Path, extract_dir: Path) -> None:
        """Extract zip or tar archive with integrity checks."""
        suffix = archive_path.suffix.lower()
        try:
            if suffix == ".zip":
                with zipfile.ZipFile(archive_path, "r") as zf:
                    bad = zf.testzip()
                    if bad:
                        raise ValueError(f"Corrupted entry in archive: {bad}")
                    if not zf.namelist():
                        raise ValueError("Archive is empty")
                    zf.extractall(extract_dir)
            else:
                import tarfile

                with tarfile.open(archive_path, "r:*") as tf:
                    if not tf.getmembers():
                        raise ValueError("Archive is empty")
                    tf.extractall(extract_dir)
        except (zipfile.BadZipFile, Exception) as exc:
            raise ValueError(f"Archive extraction failed: {exc}") from exc

        # Flatten single-root-directory archives (common with GitHub downloads)
        items = list(extract_dir.iterdir())
        if len(items) == 1 and items[0].is_dir():
            tmp = extract_dir.parent / f"{extract_dir.name}_tmp"
            items[0].rename(tmp)
            shutil.rmtree(extract_dir)
            tmp.rename(extract_dir)
            logger.info(f"DependencyService: flattened single-root archive")

    # ------------------------------------------------------------------ #
    # IR extraction (Tree-sitter, best-effort)                            #
    # ------------------------------------------------------------------ #

    async def _extract_ir(
        self, dep: DependencyDto, source_path: Path, dep_dir: Path
    ) -> None:
        """
        Run Tree-sitter IR extraction on the dependency source.
        Saves result to dep_dir/ir.json — non-fatal on failure.
        """
        from app.extractors.java_extractor import JavaTreeSitterExtractor
        from app.extractors.python_extractor import PythonTreeSitterExtractor
        from app.extractors.javascript_extractor import JavaScriptTreeSitterExtractor
        from app.extractors.csharp_extractor import CSharpTreeSitterExtractor
        from app.extractors.cpp_extractor import CppTreeSitterExtractor
        from app.extractors.sql_extractor import SqlTreeSitterExtractor
        from app.extractors.kotlin_extractor import KotlinTreeSitterExtractor
        from app.extractors.cobol_extractor import CobolTreeSitterExtractor
        from app.extractors.php_extractor import PHPTreeSitterExtractor

        from app.extractors.perl_extractor import PerlTreeSitterExtractor

        extractor_map = {
            "java": JavaTreeSitterExtractor,
            "python": PythonTreeSitterExtractor,
            "javascript": JavaScriptTreeSitterExtractor,
            "typescript": JavaScriptTreeSitterExtractor,
            "csharp": CSharpTreeSitterExtractor,
            "cpp": CppTreeSitterExtractor,
            "c": CppTreeSitterExtractor,
            "sql": SqlTreeSitterExtractor,
            "kotlin": KotlinTreeSitterExtractor,
            "cobol": CobolTreeSitterExtractor,
            "php": PHPTreeSitterExtractor,
            "perl": PerlTreeSitterExtractor,
        }

        detected = self._detect_language(source_path)
        extractor_cls = extractor_map.get(detected)

        if not extractor_cls:
            logger.warning(
                f"DependencyService: no extractor for language '{detected}' in {dep.name!r}"
            )
            return

        try:
            loop = asyncio.get_event_loop()
            ir_data = await loop.run_in_executor(
                None, lambda: extractor_cls(str(source_path)).extract_ir()
            )
            ir_file = dep_dir / "ir.json"
            with ir_file.open("w", encoding="utf-8") as fh:
                json.dump(ir_data, fh, indent=2, default=str)
            file_count = len(ir_data.get("files", []))
            logger.info(
                f"DependencyService: IR extracted for {dep.name!r} "
                f"({file_count} files) → {ir_file}"
            )
        except Exception as exc:
            logger.warning(
                f"DependencyService: IR extraction failed for {dep.name!r}: {exc} "
                "(continuing without IR)"
            )

    # ------------------------------------------------------------------ #
    # Metadata                                                             #
    # ------------------------------------------------------------------ #

    async def _update_metadata(self, dependencies: list[DependencyDto]) -> None:
        metadata_file = self.project_path / "project_metadata.json"

        if metadata_file.exists():
            with metadata_file.open("r", encoding="utf-8") as fh:
                metadata = json.load(fh)
        else:
            metadata = {"dependencies": []}

        existing: list[dict] = metadata.setdefault("dependencies", [])

        for dep in dependencies:
            entry = {
                "id": dep.id,
                "name": dep.name,
                "type": dep.type,
                "usageType": dep.usageType,
                "uploadMethod": dep.uploadMethod,
                "context_file": str(
                    self.dependencies_path / self._sanitize(dep.name) / "dependency_context.json"
                ),
            }
            idx = next(
                (i for i, d in enumerate(existing) if d.get("name") == dep.name), None
            )
            if idx is not None:
                existing[idx] = entry
            else:
                existing.append(entry)

        with metadata_file.open("w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2)

        logger.info(
            f"DependencyService: updated metadata with {len(dependencies)} dependencies"
        )

    # ------------------------------------------------------------------ #
    # Utility helpers                                                      #
    # ------------------------------------------------------------------ #

    def _write_fallback_context(
        self, dep: DependencyDto, output_path: Path, error: str | None
    ) -> None:
        """Write a minimal context file so downstream agents are not blocked."""
        fallback = {
            "name": dep.name,
            "type": dep.type,
            "usageType": dep.usageType,
            "detected_language": "unknown",
            "summary": f"Context could not be generated automatically. Error: {error}",
            "public_api": [],
            "key_types": [],
            "integration_points": [],
            "element_count": 0,
            "source_files_scanned": 0,
            "_fallback": True,
        }
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(fallback, fh, indent=2)
        logger.warning(
            f"DependencyService: wrote fallback context for {dep.name!r} → {output_path}"
        )

    def _detect_language(self, source_path: Path) -> str:
        """Detect the primary programming language by file-extension frequency."""
        counts: dict[str, int] = {}
        files = [source_path] if source_path.is_file() else source_path.rglob("*")
        for fp in files:
            if isinstance(fp, Path) and fp.is_file():
                lang = _EXT_TO_LANG.get(fp.suffix.lower())
                if lang:
                    counts[lang] = counts.get(lang, 0) + 1
        if not counts:
            return "unknown"
        return max(counts, key=counts.get)

    def _count_code_files(self, source_path: Path) -> int:
        if not source_path.exists():
            return 0
        if source_path.is_file():
            return 1 if source_path.suffix.lower() in _CODE_EXTENSIONS else 0
        return sum(
            1
            for fp in source_path.rglob("*")
            if fp.is_file() and fp.suffix.lower() in _CODE_EXTENSIONS
        )

    @staticmethod
    def _sanitize(name: str) -> str:
        """Sanitise a dependency name for use in file-system paths."""
        return re.sub(r"[^\w\-_.]", "_", name)
