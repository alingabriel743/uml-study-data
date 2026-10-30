#!/usr/bin/env python3
"""Clone the public corpus and record immutable commit metadata."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


def run(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr[-2000:]}")
    return proc.stdout.strip()


def clone_one(row: dict[str, str], root: Path) -> dict:
    language = row["language"]
    project = row["project"]
    url = row["url"]
    destination = root / language / project
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        if not (destination / ".git").exists():
            raise RuntimeError(f"existing non-git destination: {destination}")
    else:
        run("git", "clone", "--depth", "1", "--filter=blob:none", "--no-tags", url, str(destination))

    commit = run("git", "rev-parse", "HEAD", cwd=destination)
    commit_time = run("git", "show", "-s", "--format=%cI", "HEAD", cwd=destination)
    branch = run("git", "branch", "--show-current", cwd=destination)
    dirty = bool(run("git", "status", "--porcelain", cwd=destination))
    return {
        "language": language,
        "project": project,
        "url": url,
        "destination": str(destination),
        "commit": commit,
        "commit_time": commit_time,
        "branch": branch,
        "dirty": dirty,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    with args.manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    results: list[dict] = []
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(clone_one, row, args.root): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(f"cloned {result['language']}/{result['project']} {result['commit'][:12]}", flush=True)
            except Exception as exc:
                failures.append({**row, "error": str(exc)})
                print(f"failed {row['language']}/{row['project']}: {exc}", flush=True)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cohort": "replication-current-head",
        "repositories": sorted(results, key=lambda x: (x["language"], x["project"].lower())),
        "failures": failures,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
