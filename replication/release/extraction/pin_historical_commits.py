#!/usr/bin/env python3
"""Pin corpus clones to the latest reachable commit before an archival cutoff."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


def run(*args: str, cwd: Path) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr[-2000:]}")
    return proc.stdout.strip()


def pin_one(row: dict[str, str], root: Path, cutoff: str, since: str) -> dict:
    repo = root / row["language"] / row["project"]
    head_ref = run("git", "symbolic-ref", "refs/remotes/origin/HEAD", cwd=repo)
    remote_branch = head_ref.removeprefix("refs/remotes/")
    cutoff_epoch = datetime.fromisoformat(cutoff).timestamp()
    remote_head_time = int(run("git", "show", "-s", "--format=%ct", remote_branch, cwd=repo))
    if remote_head_time <= cutoff_epoch:
        commit = run("git", "rev-parse", remote_branch, cwd=repo)
    else:
        run("git", "fetch", "--shallow-since", since, "origin", cwd=repo)
        commit = run("git", "rev-list", "-1", f"--before={cutoff}", remote_branch, cwd=repo)
    if not commit:
        run("git", "fetch", "--deepen", "2000", "origin", cwd=repo)
        commit = run("git", "rev-list", "-1", f"--before={cutoff}", remote_branch, cwd=repo)
    if not commit:
        raise RuntimeError(f"no commit found before {cutoff}")
    run("git", "checkout", "--detach", commit, cwd=repo)
    return {
        **row,
        "destination": str(repo),
        "commit": commit,
        "commit_time": run("git", "show", "-s", "--format=%cI", commit, cwd=repo),
        "remote_branch": remote_branch,
        "selection_rule": f"latest commit on default branch before {cutoff}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--cutoff", default="2026-05-22T00:00:00+03:00")
    parser.add_argument("--since", default="2026-03-01")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    with args.manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    results: list[dict] = []
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(pin_one, row, args.root, args.cutoff, args.since): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(f"pinned {result['language']}/{result['project']} {result['commit'][:12]} {result['commit_time']}", flush=True)
            except Exception as exc:
                failures.append({**row, "error": str(exc)})
                print(f"failed {row['language']}/{row['project']}: {exc}", flush=True)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cohort": "replication-archival-cutoff-approximation",
        "archival_cutoff": args.cutoff,
        "repositories": sorted(results, key=lambda x: (x["language"], x["project"].lower())),
        "failures": failures,
    }
    args.metadata.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
