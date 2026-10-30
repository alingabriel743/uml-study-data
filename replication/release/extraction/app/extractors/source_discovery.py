"""Deterministic production-source discovery shared by the replication extractors."""

from __future__ import annotations

from pathlib import Path


EXCLUDED_DIRECTORIES = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "__pycache__",
    "benchmark",
    "benchmarks",
    "build",
    "coverage",
    "demo",
    "demos",
    "dist",
    "doc",
    "docs",
    "example",
    "examples",
    "fixture",
    "fixtures",
    "generated",
    "node_modules",
    "out",
    "public",
    "spec",
    "specs",
    "target",
    "test",
    "tests",
    "third-party",
    "third_party",
    "vendor",
}

EXCLUDED_FILENAME_FRAGMENTS = (
    ".bundle.",
    ".generated.",
    ".min.",
    ".umd.",
    "-min.",
)

MAX_SOURCE_BYTES = 2_000_000


def discover_source_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Return sorted production-source files under a language-independent scope rule."""
    discovered: set[Path] = set()
    for pattern in patterns:
        for path in root.rglob(pattern):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            lowered_parts = {part.lower() for part in relative.parts[:-1]}
            if EXCLUDED_DIRECTORIES.intersection(lowered_parts) or any(
                part.startswith(".") for part in relative.parts[:-1]
            ):
                continue
            lowered_name = path.name.lower()
            if any(fragment in lowered_name for fragment in EXCLUDED_FILENAME_FRAGMENTS):
                continue
            if path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            discovered.add(path)
    return sorted(discovered)
