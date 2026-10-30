#!/usr/bin/env python3
"""Run the archived Tree-sitter extractors on the pinned replication corpus."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


LANGUAGE_TO_CLASS = {
    "Java": ("app.extractors.java_extractor", "JavaTreeSitterExtractor"),
    "JavaScript": ("app.extractors.javascript_extractor", "JavaScriptTreeSitterExtractor"),
    "PHP": ("app.extractors.php_extractor", "PHPTreeSitterExtractor"),
    "Python": ("app.extractors.python_extractor", "PythonTreeSitterExtractor"),
}


def count_elements(ir: dict) -> dict[str, int]:
    keys = ["classes", "functions", "tables", "views", "statements", "procedures", "types"]
    counts = {key: len(ir.get(key, [])) for key in keys}
    counts["total"] = sum(counts.values())
    return counts


def extract_one(row: dict[str, str], repo_root: str, output_root: str, archive_root: str) -> dict:
    import importlib
    import logging

    sys.path.insert(0, archive_root)
    logging.disable(logging.WARNING)
    from app.utils.ir_extraction.postprocess_ir import postprocess_ir

    language = row["language"]
    project = row["project"]
    repo = Path(repo_root) / language / project
    output = Path(output_root) / language / project
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "ir.json"

    module_name, class_name = LANGUAGE_TO_CLASS[language]
    extractor_class = getattr(importlib.import_module(module_name), class_name)
    started = time.perf_counter()
    extractor = extractor_class(str(repo))
    raw = extractor.extract_ir()
    extractor.save_ir(str(raw_path))
    processed_path = Path(postprocess_ir(str(raw_path)))
    processed = json.loads(processed_path.read_text(encoding="utf-8"))
    elapsed = time.perf_counter() - started
    return {
        "language": language,
        "project": project,
        "repo": str(repo),
        "raw_ir": str(raw_path),
        "processed_ir": str(processed_path),
        "source_files": len(raw.get("metadata", {}).get("sourceFiles", [])),
        "raw_files_with_elements": len(raw.get("files", [])),
        "raw_elements": sum(len(x.get("elements", [])) for x in raw.get("files", [])),
        "processed_counts": count_elements(processed),
        "raw_bytes": raw_path.stat().st_size,
        "processed_bytes": processed_path.stat().st_size,
        "elapsed_seconds": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    with args.manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    results: list[dict] = []
    failures: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(extract_one, row, str(args.repo_root), str(args.output_root), str(args.archive_root)): row
            for row in rows
        }
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(
                    f"extracted {result['language']}/{result['project']}: "
                    f"{result['processed_counts']['total']} elements in {result['elapsed_seconds']:.1f}s",
                    flush=True,
                )
            except Exception as exc:
                failures.append({**row, "error": repr(exc)})
                print(f"failed {row['language']}/{row['project']}: {exc!r}", flush=True)

    payload = {
        "cohort": "replication-current-head",
        "extractor_source": str(args.archive_root),
        "results": sorted(results, key=lambda x: (x["language"], x["project"].lower())),
        "failures": failures,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
