"""
Retrospective correction experiment (R2): the revised, compiler-gated CorrectorAgent applied to the archived
files that fail to compile.

Copies each failing archived file to experiments/results/corrector_retro/files/<lang>/<project>/<type>/,
runs the CorrectorAgent on the copy (the archive itself is never modified), and records compiler validity
before and after, cost and time per file. Structure preservation is evaluated afterwards from the preserved
originals in _original/.

  python -m experiments.corrector_retro --max-cost-usd 6 --model-id global.anthropic.claude-sonnet-4-6
"""

import argparse
import asyncio
import json
import os
import shutil
import time
from pathlib import Path

from experiments.run_experiment import ROOT, _load_bedrock_credentials

OUT = ROOT / "experiments" / "results" / "corrector_retro"
COMPILER_TSV = (ROOT / "release" / "archived_plantuml_check.tsv" if (ROOT / "release").exists()
                else ROOT / "revision_work" / "eval" / "plantuml_check.tsv")


def failing_files():
    rows = COMPILER_TSV.read_text().splitlines()
    return [ROOT / r.split("\t")[0] for r in rows if r.split("\t")[1] != "0"]


async def main(max_cost: float, model_id: str, parallel: int, resume: bool = False):
    _load_bedrock_credentials(model_id)
    os.chdir(ROOT)
    from app.agent.base_agent import usage_ledger
    from app.agent.corrector_agent import CorrectorAgent

    files = failing_files()
    print(f"{len(files)} failing archived files")
    done = {}
    if resume and (OUT / "results.json").exists():  # keep completed rows, run only those stopped by the cost cap
        done = {r["file"]: r for r in json.loads((OUT / "results.json").read_text()) if r.get("valid_before") is not None}
        files = [f for f in files if str(f.relative_to(ROOT / "uml-study-data-main")) not in done]
        print(f"resuming: {len(done)} done, {len(files)} to run")
    results, spent = [], {"usd": 0.0}
    sem = asyncio.Semaphore(parallel)

    async def one(src: Path):
        rel = src.relative_to(ROOT / "uml-study-data-main")
        lang, project, dtype = rel.parts[0], rel.parts[1], rel.parts[2]
        dst = OUT / "files" / rel
        async with sem:
            if spent["usd"] >= max_cost:
                return {"file": str(rel), "status": "not_run", "reason": "cost cap"}
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            ledger: list = []
            usage_ledger.set(ledger)
            t0 = time.time()
            try:
                res = await CorrectorAgent(diagram_type=dtype).run(dst)
            except Exception as e:  # recorded, not hidden
                res = {"error": str(e)}
            cost = sum(r.get("cost_usd", 0) or 0 for r in ledger)
            spent["usd"] += cost
            row = {"file": str(rel), "language": lang, "project": project, "type": dtype,
                   "valid_before": res.get("valid_before"), "valid_after": res.get("valid_after"),
                   "llm_invoked": res.get("llm_invoked"), "changed": res.get("corrected"),
                   "error_before": res.get("error_before"), "error_after": res.get("error_after"),
                   "cost_usd": round(cost, 4), "seconds": round(time.time() - t0, 1),
                   "tokens": sum((r.get("usage") or {}).get("total_tokens", 0) for r in ledger),
                   "model": model_id, "error": res.get("error")}
            print(f"  {rel} → before {row['valid_before']} after {row['valid_after']} (${cost:.3f}, total ${spent['usd']:.2f})",
                  flush=True)
            return row

    results = list(done.values()) + list(await asyncio.gather(*(one(f) for f in files)))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    ran = [r for r in results if r.get("valid_before") is not None]
    fixed = sum(1 for r in ran if r["valid_after"])
    print(f"ran {len(ran)} | fixed {fixed} | cost this session ${spent['usd']:.2f} → {OUT / 'results.json'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-cost-usd", type=float, default=6.0)
    ap.add_argument("--model-id", default="global.anthropic.claude-sonnet-4-6")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.max_cost_usd, a.model_id, a.parallel, a.resume))
