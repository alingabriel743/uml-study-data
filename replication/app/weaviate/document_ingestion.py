"""
Weaviate document ingestion for diagrams-v2 — local Docker mode.

Mirrors the v1 DocumentationIngestion class exactly (same schema, same collection names,
same chunk properties) so the structure is compatible if the same Weaviate instance is shared.

Used when APP_WEAVIATE_ENABLED=true.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from app.utils.logger import get_logger
from app.weaviate.parsers import PARSER_REGISTRY

logger = get_logger(__name__)


class DocumentationIngestion:
    """
    Parses and ingests documentation files (PDF, DOCX, Markdown) into Weaviate.

    Collections created:
        DocFile  — one record per document file
        DocChunk — one record per semantic chunk
    """

    def __init__(self, weaviate_client, project_id: str, batch_size: int = 100) -> None:
        self.client = weaviate_client
        self.project_id = project_id
        self.repo_path = (Path("data") / project_id).resolve()
        self.batch_size = batch_size
        self._ensure_schema()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run_ingestion(self, target_files: list[str]) -> dict[str, Any]:
        """
        Ingest a list of explicit file paths.

        Args:
            target_files: Absolute or relative file paths to ingest.

        Returns:
            dict with ingested_files and total_chunks.
        """
        if not target_files:
            raise ValueError("run_ingestion() requires explicit target_files.")

        ingested_files = 0
        total_chunks = 0

        files = [
            Path(f).resolve()
            for f in target_files
            if Path(f).suffix.lower() in PARSER_REGISTRY
        ]

        for file_path in files:
            ext = file_path.suffix.lower()
            parser = PARSER_REGISTRY.get(ext)
            if not parser:
                logger.warning(f"DocumentationIngestion: no parser for {ext}, skipping {file_path}")
                continue

            try:
                logger.info(f"DocumentationIngestion: parsing {file_path.name}")
                full_content, chunks = parser(file_path)
                if not chunks:
                    logger.warning(f"DocumentationIngestion: no chunks from {file_path.name}")
                    continue
                self._insert_file_and_chunks(file_path, full_content, chunks)
                ingested_files += 1
                total_chunks += len(chunks)
                logger.info(
                    f"DocumentationIngestion: ingested {file_path.name} "
                    f"({len(chunks)} chunks)"
                )
            except Exception as exc:
                logger.error(f"DocumentationIngestion: failed on {file_path}: {exc}")

        return {"ingested_files": ingested_files, "total_chunks": total_chunks}

    def delete_file(self, file_name: str) -> int:
        """
        Delete a document file and all its chunks from Weaviate.

        Args:
            file_name: The filename (e.g. "architecture.pdf") — matched against filePath.

        Returns:
            Number of chunks deleted.
        """
        file_path_key = f"docs/{file_name}"
        file_uuid = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"{self.project_id}:{file_path_key}")
        )

        doc_file_coll = self.client.collections.get("DocFile")
        doc_chunk_coll = self.client.collections.get("DocChunk")

        try:
            doc_file_coll.data.delete_by_id(file_uuid)
            logger.info(f"DocumentationIngestion: deleted DocFile {file_uuid}")
        except Exception as exc:
            logger.warning(f"DocumentationIngestion: DocFile not found or already deleted: {exc}")

        try:
            from weaviate.classes.query import Filter

            combined = (
                Filter.by_property("projectId").equal(self.project_id)
                & Filter.by_property("filePath").equal(file_path_key)
            )
            result = doc_chunk_coll.data.delete_many(where=combined)
            deleted = result.matches if hasattr(result, "matches") else 0
            logger.info(
                f"DocumentationIngestion: deleted {deleted} DocChunks for {file_name}"
            )
            return deleted
        except Exception as exc:
            logger.error(
                f"DocumentationIngestion: failed to delete chunks for {file_name}: {exc}"
            )
            return 0

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _insert_file_and_chunks(
        self, file_path: Path, full_content: str, chunks: list[dict]
    ) -> None:
        doc_file_coll = self.client.collections.get("DocFile")
        doc_chunk_coll = self.client.collections.get("DocChunk")

        try:
            rel_path = str(file_path.relative_to(self.repo_path))
        except ValueError:
            rel_path = file_path.name
            logger.warning(
                f"DocumentationIngestion: using filename as rel_path for {file_path}"
            )

        extension = file_path.suffix.lower()
        file_uuid = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"{self.project_id}:{rel_path}")
        )

        # Upsert DocFile
        doc_file_coll.data.insert(
            uuid=file_uuid,
            properties={
                "projectId": self.project_id,
                "filePath": rel_path,
                "content": full_content,
                "extension": extension,
            },
        )

        # Batch-insert DocChunks
        with doc_chunk_coll.batch.dynamic() as batch:
            for i, chunk in enumerate(chunks):
                chunk_uuid = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_DNS, f"{self.project_id}:{rel_path}:chunk:{i}"
                    )
                )
                props = dict(chunk)
                props["projectId"] = self.project_id
                props["filePath"] = rel_path
                batch.add_object(properties=props, uuid=chunk_uuid)

    def _ensure_schema(self) -> None:
        """Create DocFile and DocChunk collections if they don't exist."""
        try:
            from weaviate.collections.classes.config import (
                Property,
                DataType,
                Configure,
            )

            existing = self.client.collections.list_all()
            vectorizer = Configure.Vectorizer.text2vec_transformers()

            definitions = {
                "DocFile": {
                    "description": "A full documentation file",
                    "props": [
                        Property(name="projectId", data_type=DataType.TEXT),
                        Property(name="filePath", data_type=DataType.TEXT),
                        Property(name="content", data_type=DataType.TEXT),
                        Property(name="extension", data_type=DataType.TEXT),
                    ],
                },
                "DocChunk": {
                    "description": "Chunked content from documentation",
                    "props": [
                        Property(name="projectId", data_type=DataType.TEXT),
                        Property(name="filePath", data_type=DataType.TEXT),
                        Property(name="content", data_type=DataType.TEXT),
                        Property(name="startPage", data_type=DataType.INT),
                        Property(name="endPage", data_type=DataType.INT),
                        Property(name="lineCount", data_type=DataType.INT),
                        Property(name="docType", data_type=DataType.TEXT),
                        Property(name="hasImages", data_type=DataType.BOOL),
                        Property(name="hasTables", data_type=DataType.BOOL),
                    ],
                },
            }

            for name, defn in definitions.items():
                if name not in existing:
                    self.client.collections.create(
                        name=name,
                        description=defn["description"],
                        properties=defn["props"],
                        vectorizer_config=vectorizer,
                    )
                    logger.info(
                        f"DocumentationIngestion: created Weaviate collection '{name}'"
                    )
        except Exception as exc:
            logger.warning(
                f"DocumentationIngestion: could not ensure schema: {exc}"
            )
