"""
Experiment runner for the Code2UML evaluation.

Stages (each can be run on its own):
  prepare   clone each corpus repository at the last commit before snapshot_date,
            remove vendored directories, extract + postprocess the IR
  generate  run the orchestrator for projects × diagram types × conditions × repetitions
  evaluate  run the offline evaluator over every run directory

Layout:
  data/<project>/src/                                   pinned source snapshot
  data/<project>/ir.json, ir_postprocessed.json         IR
  data/<project>/runs/<condition>/rep<k>/<type>/        diagrams, _original/, run_manifest.json
  experiments/results/<condition>/rep<k>/               evaluator CSVs

Examples:
  python -m experiments.run_experiment prepare
  python -m experiments.run_experiment generate --conditions full single_agent --reps 3 --dry-run
  python -m experiments.run_experiment generate --projects shopping-cart --types class --conditions full --reps 1
  python -m experiments.run_experiment evaluate
"""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "experiments" / "corpus.json"
RESOLVED = ROOT / "experiments" / "corpus_resolved.json"
DIAGRAM_TYPES = ["class", "sequence", "activity", "use_case", "component", "deployment", "system_context"]


def load_corpus() -> dict:
    return json.loads(CORPUS.read_text())


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


# ── prepare ─────────────────────────────────────────────────────

def prepare(projects: list[dict], corpus: dict) -> None:
    from app.api.ir_generation import LANGUAGE_EXTRACTOR_MAP
    from app.utils.ir_extraction.postprocess_ir import postprocess_ir

    resolved = json.loads(RESOLVED.read_text()) if RESOLVED.exists() else {}
    for proj in projects:
        pid = proj["id"]
        project_dir = ROOT / "data" / pid
        src = project_dir / "src"
        print(f"── {pid}")
        if not src.exists():
            project_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--quiet", proj["url"], str(src)], check=True)
        commit = proj.get("commit") or _git("rev-list", "-1", f"--before={corpus['snapshot_date']}", "HEAD", cwd=src)
        _git("checkout", "--quiet", commit, cwd=src)
        commit_date = _git("show", "-s", "--format=%cI", commit, cwd=src)

        # Extract from a clean copy so vendored code never enters the IR
        snapshot = project_dir / "snapshot"
        if snapshot.exists():
            shutil.rmtree(snapshot)
        shutil.copytree(src, snapshot, ignore=shutil.ignore_patterns(*corpus["exclude_dirs"]))

        extractor = LANGUAGE_EXTRACTOR_MAP[proj["language"]](str(snapshot))
        t0 = time.perf_counter()
        ir = extractor.extract_ir()
        ir_path = project_dir / "ir.json"
        extractor.save_ir(str(ir_path))
        postprocess_ir(str(ir_path))
        extract_s = time.perf_counter() - t0

        post = json.loads((project_dir / "ir_postprocessed.json").read_text())
        counts = {k: len(post.get(k, [])) for k in ("classes", "functions", "tables", "procedures", "types")}
        resolved[pid] = {**proj, "commit": commit, "commit_date": commit_date, "ir_counts": counts,
                         "ir_entities": sum(counts.values()), "extraction_seconds": round(extract_s, 2),
                         "source_files": len(ir.get("metadata", {}).get("sourceFiles", []))}
        print(f"   commit {commit[:10]} ({commit_date}) — {resolved[pid]['ir_entities']} IR entities, {extract_s:.1f}s")
        RESOLVED.write_text(json.dumps(resolved, indent=2))


# ── generate ────────────────────────────────────────────────────

async def _generate_one(pid: str, dtype: str, condition: str, rep: int, tag: str = "") -> dict:
    from app.agent.orchestrator import DiagramOrchestrator

    out = ROOT / "data" / pid / "runs" / (condition + tag) / f"rep{rep}" / dtype
    manifest = out / "run_manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()).get("status") == "ok":
        return {"skipped": True, "dir": str(out)}
    orch = DiagramOrchestrator(project_id=pid, diagram_type=dtype,
                               options={"condition": condition, "output_dir": str(out)})
    result = await orch.run()
    return {"status": result.get("status"), "dir": str(out), "metrics": result.get("metrics")}


def _load_bedrock_credentials(model_id: str) -> None:
    """Bedrock API key from the env or macOS Keychain (service code2uml-bedrock); never printed."""
    import getpass
    import os
    # 1) .env in the repo root (preferred), 2) environment, 3) macOS Keychain
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.removeprefix("export ").split("=", 1)
            value = value.strip().strip("'\"")
            if value and name.strip() not in os.environ:
                os.environ[name.strip()] = value
    if os.environ.get("CODE2UML_AWS_AUTH") == "iam":
        os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)  # use the standard AWS credential chain (~/.aws)
    elif not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        out = subprocess.run(["security", "find-generic-password", "-a", getpass.getuser(),
                              "-s", "code2uml-bedrock", "-w"], capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout.strip():
            raise SystemExit("No Bedrock key: set AWS_BEARER_TOKEN_BEDROCK or Keychain service 'code2uml-bedrock'")
        value = out.stdout.strip()
        # Tolerate a pasted shell line: `export AWS_BEARER_TOKEN_BEDROCK=ABSK...` (optionally quoted)
        value = value.split("=", 1)[1] if value.startswith(("export ", "AWS_BEARER_TOKEN_BEDROCK=")) else value
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = value.strip().strip("'\"")
    os.environ["APP_USE_BEDROCK"] = "true"
    os.environ["APP_BEDROCK_MODEL_ID"] = model_id
    os.environ.setdefault("APP_AWS_REGION", "us-east-1")


async def generate(projects: list[str], types: list[str], conditions: list[str], reps: int,
                   dry_run: bool, max_parallel: int, max_cost_usd: float, model_id: str, tag: str) -> None:
    jobs = [(p, t, c, r) for r in range(1, reps + 1) for c in conditions for p in projects for t in types]
    # Cache warming: the first plain-LLM call per repository writes the IR into the prompt cache;
    # running it before the others lets the remaining calls read it at ~10% of the input price.
    warm = {}
    for j in jobs:
        if j[2] == "plain_llm" and j[0] not in warm:
            warm[j[0]] = j
    warm_jobs = list(warm.values())
    jobs = warm_jobs + [j for j in jobs if j not in warm_jobs]
    print(f"{len(jobs)} runs = {len(projects)} projects × {len(types)} types × {len(conditions)} conditions × {reps} reps")
    if dry_run:
        for j in jobs[:10]:
            print("  ", j)
        print("   ... (dry run, nothing executed)")
        return
    # The SDK sessions and orchestrator use paths relative to the repo root (data/<id>/...)
    import os
    os.chdir(ROOT)
    _load_bedrock_credentials(model_id)
    sem = asyncio.Semaphore(max_parallel)
    spent = {"usd": 0.0, "reserved": 0.0, "stopped": False}

    def expected_cost(job) -> float:
        """Conservative per-run reservation (USD) so parallel runs cannot overshoot the cap."""
        pid, _, condition, _ = job
        if condition in ("single_view", "single_prefix"):
            return 0.35
        ir = ROOT / "data" / pid / "ir_postprocessed.json"
        ir_mtok = (ir.stat().st_size / 3.3) / 1e6 if ir.exists() else 0.3
        if condition == "plain_llm":
            return 3.75 * ir_mtok + 1.0          # worst case: cache write at 1.25 x $3/MTok + output
        if condition == "single_agent":
            return 6.0 * ir_mtok + 1.0           # agent may read most of the IR more than once
        return 8.0                               # full / no_corrector multi-agent pipelines (small and median repositories)

    async def run(job):
        async with sem:
            reserve = expected_cost(job)
            if spent["usd"] + spent["reserved"] + reserve > max_cost_usd:  # hard cap incl. in-flight runs
                spent["stopped"] = True
                return job, {"status": "not_run", "reason": f"cost cap ${max_cost_usd} reached"}
            spent["reserved"] += reserve
            t0 = time.time()
            try:
                res = await _generate_one(*job, tag=tag)
            except Exception as e:  # keep going; failures are recorded, not hidden
                res = {"status": "error", "error": str(e)}
            cost = (res.get("metrics") or {}).get("cost_usd", 0) or 0
            spent["reserved"] -= reserve
            spent["usd"] += cost
            print(f"  {job} → {res.get('status', 'skipped' if res.get('skipped') else '?')} "
                  f"(${cost:.2f}, total ${spent['usd']:.2f}, {time.time() - t0:.0f}s)", flush=True)
            return job, res

    # Warm-up calls first (sequentially per repo, in parallel across repos), then everything else
    results = list(await asyncio.gather(*(run(j) for j in warm_jobs)))
    results += await asyncio.gather(*(run(j) for j in jobs if j not in warm_jobs))
    print(f"total recorded cost ${spent['usd']:.2f}" + (" — STOPPED at cost cap" if spent["stopped"] else ""))
    log = ROOT / "experiments" / f"generate_log_{time.strftime('%Y%m%d-%H%M%S')}.json"
    log.write_text(json.dumps([{"job": j, **r} for j, r in results], indent=2, default=str))
    print(f"log → {log}")


# ── evaluate ────────────────────────────────────────────────────

def evaluate(projects: list[str], validate: bool) -> None:
    for pid in projects:
        runs = ROOT / "data" / pid / "runs"
        if not runs.exists():
            continue
        for rep_dir in sorted(runs.glob("*/rep*")):
            condition, rep = rep_dir.parent.name, rep_dir.name
            out = ROOT / "experiments" / "results" / condition / rep / pid
            cmd = [sys.executable, "-m", "app.evaluation.evaluate", "--root", str(rep_dir), "--layout", "data",
                   "--ir", str(ROOT / "data" / pid / "ir_postprocessed.json"), "--out", str(out)]
            if not validate:
                cmd.append("--no-validate")
            print(f"── {pid} {condition} {rep}")
            if subprocess.run(cmd, cwd=ROOT).returncode != 0:
                print(f"!! evaluation failed for {pid} {condition} {rep} (continuing)")


def main() -> None:
    corpus = load_corpus()
    all_ids = [p["id"] for p in corpus["projects"]]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["prepare", "generate", "evaluate"])
    ap.add_argument("--projects", nargs="+", default=all_ids)
    ap.add_argument("--types", nargs="+", default=DIAGRAM_TYPES)
    ap.add_argument("--conditions", nargs="+", default=["full", "single_agent", "prefix"],
                    help="no_corrector is available but redundant: full runs keep pre-correction originals")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--max-parallel", type=int, default=2, help="concurrent orchestrator runs")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-cost-usd", type=float, default=5.0, help="hard cap on recorded cost for this invocation")
    ap.add_argument("--model-id", default="us.anthropic.claude-sonnet-4-6", help="Bedrock model / inference profile")
    ap.add_argument("--tag", default="", help="suffix for the run directory, e.g. _sonnet55")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    if args.stage == "prepare":
        prepare([p for p in corpus["projects"] if p["id"] in args.projects], corpus)
    elif args.stage == "generate":
        asyncio.run(generate(args.projects, args.types, args.conditions, args.reps, args.dry_run,
                             args.max_parallel, args.max_cost_usd, args.model_id, args.tag))
    else:
        evaluate(args.projects, not args.no_validate)


if __name__ == "__main__":
    main()
