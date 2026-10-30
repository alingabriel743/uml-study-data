"""
DocumentIngestionService — 3-mode document ingestion orchestrator.

Mode selection (in priority order):
  1. KB service  (APP_KB_SERVICE_ENABLED=true)  → forward to KB microservice via SQS
  2. Weaviate    (APP_WEAVIATE_ENABLED=true)     → local Docker Weaviate RAG
  3. Agent-only  (default)                       → store plain text in docs/, agents read via Glob

Modes 1 and 2 fall back to mode 3 on error so the ingestion never hard-fails.

Response structure is identical to v1 DocumentationIngestion API responses.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Document file extensions accepted for ingestion
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".md", ".markdown", ".txt"}


class DocumentIngestionService:
    """
    Orchestrates document ingestion for a project across all three modes.

    Usage:
        svc = DocumentIngestionService(project_id)
        result = await svc.ingest_files(["path/to/arch.pdf", "path/to/README.md"])
    """

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.project_path = (Path("data") / project_id).resolve()
        self.docs_path = self.project_path / "docs"
        self.docs_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def ingest_files(
        self,
        target_files: list[str],
        *,
        file_location: str | None = None,
        file_name: str | None = None,
        extract_images: bool = False,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Ingest a list of document files.

        Args:
            target_files:       List of absolute/relative file paths already on disk.
            file_location:      S3 / URL location (passed to KB service when used).
            file_name:          Original filename (for KB service metadata).
            extract_images:     Whether to extract images (KB service flag).
            user_id:            Optional user ID (forwarded to KB service).
            organization_id:    Optional org ID (forwarded to KB service).

        Returns:
            v1-compatible response dict.
        """
        # Copy files to docs/ folder so agents can discover them via Glob
        copied = self._copy_to_docs(target_files)

        # Mode 1: KB service (deployed)
        if settings.kb_service_enabled and file_location:
            return await self._ingest_via_kb(
                file_location=file_location,
                file_name=file_name,
                extract_images=extract_images,
                user_id=user_id,
                organization_id=organization_id,
                local_copies=copied,
            )

        # Mode 2: local Weaviate (Docker)
        if settings.weaviate_enabled:
            return await self._ingest_via_weaviate(target_files=copied)

        # Mode 3: agent-only — files are already in docs/, nothing more needed
        return self._agent_only_response(copied)

    async def delete_file(
        self,
        file_name: str,
        *,
        file_path: str | None = None,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Delete a documentation file from all configured stores.

        Returns:
            v1-compatible response dict.
        """
        deleted_locally = self._delete_from_docs(file_name)

        kb_result: dict | None = None
        weaviate_result: dict | None = None

        if settings.kb_service_enabled:
            kb_result = await self._delete_via_kb(
                file_path=file_path or f"docs/{file_name}",
                file_name=file_name,
                file_id=file_id,
            )

        if settings.weaviate_enabled:
            weaviate_result = self._delete_via_weaviate(file_name)

        return {
            "status": "ok",
            "project_id": self.project_id,
            "file_name": file_name,
            "deleted_locally": deleted_locally,
            "kb_deletion": kb_result,
            "weaviate_deletion": weaviate_result,
            "message": f"File '{file_name}' deleted successfully.",
        }

    def list_documents(self) -> list[dict[str, Any]]:
        """Return metadata for all documents in the docs/ folder."""
        docs = []
        for fp in sorted(self.docs_path.iterdir()):
            if fp.is_file() and fp.suffix.lower() in SUPPORTED_EXTENSIONS:
                docs.append(
                    {
                        "file_name": fp.name,
                        "extension": fp.suffix.lower(),
                        "size_bytes": fp.stat().st_size,
                        "path": str(fp.relative_to(self.project_path)),
                    }
                )
        return docs

    # ------------------------------------------------------------------ #
    # Mode 1 — KB service                                                 #
    # ------------------------------------------------------------------ #

    async def _ingest_via_kb(
        self,
        *,
        file_location: str,
        file_name: str | None,
        extract_images: bool,
        user_id: str | None,
        organization_id: str | None,
        local_copies: list[str],
    ) -> dict[str, Any]:
        logger.info(
            f"DocumentIngestionService: KB mode — "
            f"project={self.project_id}, file_location={file_location}"
        )
        try:
            from app.utils.kb_sqs_client import get_kb_sqs_client

            kb = get_kb_sqs_client()
            kb_response = await kb.upload_file(
                project_id=self.project_id,
                file_location=file_location,
                file_name=file_name,
                extract_images=extract_images,
                wait_for_response=True,
                user_id=user_id,
                organization_id=organization_id,
            )
            return {
                "status": "ok",
                "project_id": self.project_id,
                "mode": "kb_service",
                "ingested_files": len(local_copies),
                "total_chunks": None,  # KB handles internally
                "kb_response": kb_response,
                "local_copies": local_copies,
                "message": f"Successfully sent {len(local_copies)} file(s) to KB service.",
            }
        except Exception as exc:
            logger.error(
                f"DocumentIngestionService: KB ingestion failed: {exc} — "
                "falling back to agent-only mode"
            )
            return self._agent_only_response(local_copies, fallback_error=str(exc))

    # ------------------------------------------------------------------ #
    # Mode 2 — local Weaviate                                             #
    # ------------------------------------------------------------------ #

    async def _ingest_via_weaviate(self, *, target_files: list[str]) -> dict[str, Any]:
        logger.info(
            f"DocumentIngestionService: Weaviate mode — "
            f"project={self.project_id}, files={len(target_files)}"
        )
        try:
            from app.weaviate.weaviate_client import get_weaviate_client
            from app.weaviate.document_ingestion import DocumentationIngestion

            client = get_weaviate_client()
            ingestion = DocumentationIngestion(client, self.project_id)
            result = ingestion.run_ingestion(target_files=target_files)

            return {
                "status": "ok",
                "project_id": self.project_id,
                "mode": "weaviate",
                "ingested_files": result["ingested_files"],
                "total_chunks": result["total_chunks"],
                "kb_response": None,
                "local_copies": target_files,
                "message": (
                    f"Successfully ingested {result['ingested_files']} file(s) "
                    f"({result['total_chunks']} chunks) into Weaviate."
                ),
            }
        except Exception as exc:
            logger.error(
                f"DocumentIngestionService: Weaviate ingestion failed: {exc} — "
                "falling back to agent-only mode"
            )
            return self._agent_only_response(target_files, fallback_error=str(exc))

    # ------------------------------------------------------------------ #
    # Mode 3 — agent-only (plain file storage)                            #
    # ------------------------------------------------------------------ #

    def _agent_only_response(
        self, local_copies: list[str], *, fallback_error: str | None = None
    ) -> dict[str, Any]:
        logger.info(
            f"DocumentIngestionService: agent-only mode — "
            f"project={self.project_id}, files={len(local_copies)}"
        )
        response: dict[str, Any] = {
            "status": "ok",
            "project_id": self.project_id,
            "mode": "agent_only",
            "ingested_files": len(local_copies),
            "total_chunks": None,
            "kb_response": None,
            "local_copies": local_copies,
            "message": (
                f"Stored {len(local_copies)} file(s) in docs/ folder. "
                "Diagram agents will read them directly via Glob/Read tools."
            ),
        }
        if fallback_error:
            response["fallback_error"] = fallback_error
        return response

    # ------------------------------------------------------------------ #
    # Deletion helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _delete_via_kb(
        self,
        *,
        file_path: str,
        file_name: str,
        file_id: str | None,
    ) -> dict[str, Any] | None:
        try:
            from app.utils.kb_sqs_client import get_kb_sqs_client

            kb = get_kb_sqs_client()
            return await kb.delete_file(
                project_id=self.project_id,
                file_path=file_path,
                file_name=file_name,
                file_id=file_id,
                wait_for_response=False,
            )
        except Exception as exc:
            logger.error(f"DocumentIngestionService: KB deletion failed: {exc}")
            return {"status": "failed", "error": str(exc)}

    def _delete_via_weaviate(self, file_name: str) -> dict[str, Any] | None:
        try:
            from app.weaviate.weaviate_client import get_weaviate_client
            from app.weaviate.document_ingestion import DocumentationIngestion

            client = get_weaviate_client()
            ingestion = DocumentationIngestion(client, self.project_id)
            deleted_chunks = ingestion.delete_file(file_name)
            return {"status": "ok", "deleted_chunks": deleted_chunks}
        except Exception as exc:
            logger.error(f"DocumentIngestionService: Weaviate deletion failed: {exc}")
            return {"status": "failed", "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Local file helpers                                                   #
    # ------------------------------------------------------------------ #

    def _copy_to_docs(self, source_files: list[str]) -> list[str]:
        """
        Copy provided files into docs/ and return the list of destination paths.
        Files already inside docs/ are kept in place.
        """
        copied: list[str] = []
        for src_str in source_files:
            src = Path(src_str).resolve()
            if not src.exists():
                logger.warning(f"DocumentIngestionService: source file not found: {src}")
                continue
            if src.suffix.lower() not in SUPPORTED_EXTENSIONS:
                logger.warning(f"DocumentIngestionService: unsupported extension: {src.suffix}")
                continue

            dest = self.docs_path / src.name
            try:
                if src != dest:
                    shutil.copy2(src, dest)
                    logger.info(f"DocumentIngestionService: copied {src.name} → {dest}")
                copied.append(str(dest))
            except Exception as exc:
                logger.error(f"DocumentIngestionService: failed to copy {src}: {exc}")

        return copied

    def _delete_from_docs(self, file_name: str) -> bool:
        """Delete a file from the docs/ folder. Returns True if deleted."""
        target = self.docs_path / file_name
        if target.exists():
            target.unlink()
            logger.info(f"DocumentIngestionService: deleted local file {file_name}")
            return True
        return False
