"""
Markdown parser — ported from v1 with v2 conventions.

Uses markdown-it-py for block extraction and sentence-transformers for semantic chunking.
Falls back to naive chunking if the embedding model is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.utils.logger import get_logger

logger = get_logger(__name__)


def parse_markdown(
    file_path: Path,
    similarity_threshold: float = 0.75,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Parse a Markdown file and return (full_text, semantic_chunks).

    Each chunk dict:
        content, lineCount, docType, filePath,
        startPage, endPage, hasImages, hasTables
    """
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise ImportError(
            "markdown-it-py is required for Markdown parsing. "
            "Add it to requirements.txt: markdown-it-py>=3.0.0"
        ) from exc

    content = file_path.read_text(encoding="utf-8")
    md = MarkdownIt()
    tokens = md.parse(content)

    block_meta: list[dict] = []
    for token in tokens:
        if token.type == "inline" and token.content.strip():
            has_img = any(
                child.type == "image" for child in (token.children or [])
            )
            block_meta.append(
                {"text": token.content.strip(), "has_image": has_img, "has_table": False}
            )
        elif token.type == "table_open":
            block_meta.append({"text": "", "has_image": False, "has_table": True})

    if not block_meta:
        return content, []

    blocks = [b["text"] for b in block_meta]
    rel_path = _rel_path(file_path)

    try:
        from sklearn.metrics.pairwise import cosine_similarity
        from app.utils.model_loader import get_model

        model = get_model()
        embeddings = model.encode(blocks)
        chunks = _semantic_chunks(blocks, block_meta, embeddings, rel_path, similarity_threshold)
    except Exception as exc:
        logger.warning(f"md_parser: semantic chunking unavailable ({exc}), using naive chunking")
        chunks = _naive_chunks(blocks, block_meta, rel_path)

    return content, chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _semantic_chunks(
    blocks: list[str],
    block_meta: list[dict],
    embeddings,
    rel_path: str,
    threshold: float,
) -> list[dict[str, Any]]:
    from sklearn.metrics.pairwise import cosine_similarity

    chunks: list[dict] = []
    current: list[str] = [blocks[0]]
    line_count = blocks[0].count("\n") + 1
    has_image = block_meta[0]["has_image"]
    has_table = block_meta[0]["has_table"]

    def flush() -> None:
        nonlocal current, line_count, has_image, has_table
        text = "\n\n".join(current).strip()
        if not text:
            return
        chunks.append(
            {
                "content": text,
                "lineCount": line_count,
                "docType": "markdown",
                "filePath": rel_path,
                "startPage": None,
                "endPage": None,
                "hasImages": has_image,
                "hasTables": has_table,
            }
        )
        current.clear()
        line_count = 0
        has_image = False
        has_table = False

    for i in range(1, len(blocks)):
        sim = cosine_similarity([embeddings[i]], [embeddings[i - 1]])[0][0]
        text = blocks[i]
        if sim >= threshold:
            current.append(text)
            line_count += text.count("\n") + 1
            has_image = has_image or block_meta[i]["has_image"]
            has_table = has_table or block_meta[i]["has_table"]
        else:
            flush()
            current = [text]
            line_count = text.count("\n") + 1
            has_image = block_meta[i]["has_image"]
            has_table = block_meta[i]["has_table"]

    if current:
        flush()
    return chunks


def _naive_chunks(
    blocks: list[str],
    block_meta: list[dict],
    rel_path: str,
    chunk_size: int = 10,
) -> list[dict[str, Any]]:
    chunks: list[dict] = []
    for i in range(0, len(blocks), chunk_size):
        window = blocks[i : i + chunk_size]
        metas = block_meta[i : i + chunk_size]
        chunks.append(
            {
                "content": "\n\n".join(window).strip(),
                "lineCount": sum(t.count("\n") + 1 for t in window),
                "docType": "markdown",
                "filePath": rel_path,
                "startPage": None,
                "endPage": None,
                "hasImages": any(m["has_image"] for m in metas),
                "hasTables": any(m["has_table"] for m in metas),
            }
        )
    return chunks


def _rel_path(file_path: Path) -> str:
    try:
        return str(file_path.relative_to(file_path.parents[2]))
    except (ValueError, IndexError):
        return file_path.name
