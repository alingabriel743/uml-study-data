"""
Analysis for the revision: every number reported in Section 4 and in the response letter.

    python -m experiments.analyze            # after `run_experiment evaluate` for the core cases

Inputs : data/v2-*/runs/<condition>/<rep>/<type>/run_manifest.json  (cost, tokens, time, compiler verdicts)
         experiments/results/<condition>/<rep>/<project>/batches.csv  (evaluator metrics per case)
         data/v2-*/reference/                                          (deterministic RE references)
         archived cohort: uml-study-data-main + revision_work/eval/plantuml_check.tsv + v2 corrected corpus
Outputs: experiments/results/summary.json   (flat values used by the manuscript and the letter)
         experiments/results/tables/*.csv  (tables for Section 4)
"""

import csv
import json
import math
import random
import statistics as st
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from scipy.stats import spearmanr, wilcoxon, binomtest

ROOT = Path(__file__).resolve().parent.parent
COMPILER_TSV = (ROOT / "release" / "archived_plantuml_check.tsv" if (ROOT / "release").exists()
                else ROOT / "revision_work" / "eval" / "plantuml_check.tsv")
RES = ROOT / "experiments" / "results"
TABLES = RES / "tables"
CORE = ["v2-Mindustry", "v2-evolver", "v2-composer", "v2-lyra"]
TYPES = ["class", "sequence", "activity", "use_case"]
V2 = ROOT / "release" if (ROOT / "release").exists() else Path.home() / "Desktop" / "Code2UML_v2_submission" / "successor-release"

LABEL = {
    "single_view": "Single agent, ranked view",
    "single_prefix": "Single agent, declaration-order view",
    "single_agent": "Single agent, full IR",
    "plain_llm": "Plain LLM, full IR (200K context)",
    "plain_llm_1m": "Plain LLM, full IR (1M context)",
    "single_view_opus46": "Single agent, ranked view (Opus 4.6)",
    "single_view_sonnet45": "Single agent, ranked view (Sonnet 4.5)",
    "reverse_engineering": "Deterministic reverse engineering",
}


def fnum(x, nd=3):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


# ── run manifests: cost, time, tokens, compiler verdicts ────────

MULTI_AGENT = ("full", "no_corrector")
REVISED_FULL_PROJECTS = ["v2-shopping-cart", "v2-composer"]


RUN_FIELDS = ["project", "condition", "rep", "type", "status", "files", "checked", "valid_before", "valid_after",
              "cost", "seconds", "tokens", "output_tokens", "sessions", "model", "corrector_calls"]


def _manifest_row(m: Path) -> dict:
    j = json.loads(m.read_text())
    s = j["summary"]
    return {"project": m.parts[-6], "condition": m.parts[-4], "rep": m.parts[-3], "type": m.parts[-2],
            "status": j["status"], "files": s["diagrams"], "checked": s["checked"],
            "valid_before": s["valid_before_correction"], "valid_after": s["valid_after_correction"],
            "cost": s["cost_usd"] or 0.0, "seconds": s["total_seconds"],
            "tokens": s["input_tokens"] + s["output_tokens"] + s["cache_read_input_tokens"]
                      + s["cache_creation_input_tokens"],
            "output_tokens": s["output_tokens"], "sessions": s["agent_sessions"], "model": j.get("model"),
            "corrector_calls": sum(1 for c in j.get("corrections", []) if c.get("llm_invoked"))}


def all_runs() -> list[dict]:
    """One row per run. Read from the run manifests when present, otherwise from the released tables/runs.csv
    (the replication package ships this path-free table instead of the manifests)."""
    rows = [_manifest_row(m) for m in sorted((ROOT / "data").glob("v2-*/runs/*/rep*/*/run_manifest.json"))
            if m.parts[-6] != "v2-PHPMailer"]  # pilot runs on the excluded folder
    table = TABLES / "runs.csv"
    if rows:
        TABLES.mkdir(parents=True, exist_ok=True)
        _write(table, [{k: r[k] for k in RUN_FIELDS} for r in rows])
        return rows
    ints = {"files", "checked", "valid_before", "valid_after", "tokens", "output_tokens", "sessions", "corrector_calls"}
    return [{k: (int(float(v)) if k in ints else float(v) if k in ("cost", "seconds") else v) for k, v in r.items()}
            for r in csv.DictReader(table.open())]


def revised_full():
    """Revised multi-agent pipeline against the single agent (ranked view) on the same cases (R1.4, R2.10)."""
    rows, per_case = [], []
    for cond in ("full", "single_view"):
        agg = defaultdict(list)
        for pid in REVISED_FULL_PROJECTS:
            for t in TYPES:
                ms = [r for r in RUNS if r["project"] == pid and r["condition"] == cond and r["type"] == t]
                runs = [dict(r, ktokens=r["tokens"] / 1000) for r in ms if r["status"] == "ok"]
                if not runs:
                    continue
                ev = []
                for m in ms:
                    f = RES / cond / m["rep"] / pid / "batches.csv"
                    if f.exists():
                        ev += [r for r in csv.DictReader(f.open()) if r["diagram_type"] == t]
                val = lambda k: mean([float(r[k]) for r in ev if r.get(k) not in ("", "None", None)])
                case = {"condition": cond, "project": pid.removeprefix("v2-"), "type": t, "runs": len(runs),
                        "files": sum(r["files"] for r in runs),          # totals over repetitions
                        "valid_before": sum(r["valid_before"] for r in runs),
                        "valid_after": sum(r["valid_after"] for r in runs),
                        "usd": mean([r["cost"] for r in runs]), "seconds": mean([r["seconds"] for r in runs]),
                        "sessions": mean([r["sessions"] for r in runs]),
                        "corrector_calls": mean([r["corrector_calls"] for r in runs]),
                        "ktokens": mean([r["ktokens"] for r in runs]),
                        "entity_recall": val("entity_recall"), "grounded_element_rate": val("grounded_element_rate"),
                        "endpoint_grounding": val("endpoint_grounding"),
                        "relationship_precision_ir": val("relationship_precision_ir")}
                per_case.append({k: (fnum(v) if isinstance(v, float) else v) for k, v in case.items()})
                for k, v in case.items():
                    agg[k].append(v)
        if not agg:
            continue
        cls_seq = [c for c in per_case if c["condition"] == cond and c["type"] in ("class", "sequence")]
        cls = [c for c in per_case if c["condition"] == cond and c["type"] == "class"]
        files, before, after = sum(agg["files"]), sum(agg["valid_before"]), sum(agg["valid_after"])
        rows.append({"condition": cond, "cases": len(agg["type"]), "files": fnum(files, 1),
                     "valid_before": fnum(before, 1), "valid_after": fnum(after, 1),
                     "validity_before": fnum(before / files), "validity_after": fnum(after / files),
                     "entity_recall": fnum(mean([c["entity_recall"] for c in cls_seq])),
                     "grounded_element_rate": fnum(mean([c["grounded_element_rate"] for c in cls_seq])),
                     "endpoint_grounding": fnum(mean([c["endpoint_grounding"] for c in cls_seq])),
                     "relationship_precision_ir": fnum(mean([c["relationship_precision_ir"] for c in cls])),
                     "usd_per_case": fnum(mean(agg["usd"]), 2), "usd_total": fnum(sum(agg["usd"]), 2),
                     "seconds_per_case": fnum(mean(agg["seconds"]), 0),
                     "seconds_min": fnum(min(agg["seconds"]), 0), "seconds_max": fnum(max(agg["seconds"]), 0),
                     "sessions_per_case": fnum(mean(agg["sessions"]), 1),
                     "corrector_calls": fnum(sum(agg["corrector_calls"]), 1),
                     "runs": sum(agg["runs"])})
    _write(TABLES / "revised_full.csv", rows)
    _write(TABLES / "revised_full_cases.csv", per_case)
    return {"conditions": {r["condition"]: r for r in rows}, "cases": per_case}

def load_runs():
    # multi-agent runs are summarized in revised_full()
    return [r for r in RUNS if r["project"] in CORE and r["type"] in TYPES and r["condition"] not in MULTI_AGENT]


# ── evaluator metrics per case ──────────────────────────────────

METRICS = ["compile_validity", "entity_recall", "grounded_element_rate", "endpoint_grounding",
           "relationship_precision_ir", "generalization_precision", "mean_elements", "mean_relationships", "mean_sci",
           "member_precision", "member_recall", "attribute_type_agreement"]


def load_cases():
    cases = defaultdict(dict)  # (cond, project, type) -> rep -> row
    for f in RES.glob("*/rep*/*/batches.csv"):
        cond, rep, pid = f.parts[-4], f.parts[-3], f.parts[-2]
        if pid not in CORE:
            continue
        for row in csv.DictReader(f.open()):
            if row["diagram_type"] in TYPES:
                vals = {k: (None if row.get(k) in ("", "None", None) else float(row[k])) for k in METRICS}
                vals["files"] = int(row["files"])
                cases[(cond, pid, row["diagram_type"])][rep] = vals
    return cases


def case_value(cases, cond, pid, dtype, metric):
    reps = cases.get((cond, pid, dtype), {})
    return mean([r[metric] for r in reps.values()])


# ── deterministic reverse-engineering baseline (class diagrams) ─

def evaluate_references():
    out = {}
    for pid in CORE:
        ref = ROOT / "data" / pid / "reference"
        if not (ref / "class").exists():
            continue
        dest = RES / "reverse_engineering" / "rep1" / pid
        subprocess.run([sys.executable, "-m", "app.evaluation.evaluate", "--root", str(ref), "--layout", "data",
                        "--ir", str(ROOT / "data" / pid / "ir_postprocessed.json"), "--out", str(dest)],
                       cwd=ROOT, check=True, capture_output=True)
        manifest = json.loads((ref / "reference_manifest.json").read_text())
        out[pid] = {"tool": manifest.get("tool"), "version": manifest.get("version"),
                    "seconds": manifest.get("runtime_seconds") or manifest.get("generation_seconds")}
    return out


# ── statistics ──────────────────────────────────────────────────

def cliffs_delta(a, b):
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    return (gt - lt) / (len(a) * len(b)) if a and b else None


def paired(cases, cond_a, cond_b, metric):
    pairs = []
    for pid in CORE:
        for t in TYPES:
            a, b = case_value(cases, cond_a, pid, t, metric), case_value(cases, cond_b, pid, t, metric)
            if a is not None and b is not None:
                pairs.append((a, b))
    if len(pairs) < 3:
        return {"n": len(pairs)}
    a, b = [p[0] for p in pairs], [p[1] for p in pairs]
    diffs = [x - y for x, y in pairs]
    try:
        p = wilcoxon(a, b, zero_method="zsplit").pvalue if any(diffs) else 1.0
    except ValueError:
        p = None
    return {"n": len(pairs), "mean_a": fnum(mean(a)), "mean_b": fnum(mean(b)), "median_diff": fnum(st.median(diffs)),
            "wilcoxon_p": fnum(p, 4), "cliffs_delta": fnum(cliffs_delta(a, b))}


def spearman_ci(x, y, n_boot=5000, seed=7):
    rho = spearmanr(x, y).statistic
    rng = random.Random(seed)
    idx = list(range(len(x)))
    boots = []
    for _ in range(n_boot):
        s = [rng.choice(idx) for _ in idx]
        xs, ys = [x[i] for i in s], [y[i] for i in s]
        if len(set(xs)) > 1 and len(set(ys)) > 1:
            boots.append(spearmanr(xs, ys).statistic)
    boots.sort()
    return fnum(rho), fnum(boots[int(0.025 * len(boots))]), fnum(boots[int(0.975 * len(boots)) - 1])


# ── archived cohort: size correlation (RQ4) ─────────────────────

def archived_size_analysis():
    sizes = {r["project"]: int(r["ir_entities"]) for r in csv.DictReader((V2 / "corrected-corpus" / "per_repository_counts.csv").open())}
    v2proj = {r["group"].split("/")[1]: r for r in csv.DictReader((V2 / "validation" / "by_project.csv").open())}
    compile_ok, compile_n = defaultdict(int), defaultdict(int)
    for line in COMPILER_TSV.read_text().splitlines():
        path, rc = line.split("\t")[:2]
        proj = path.split("/")[2]
        compile_n[proj] += 1
        compile_ok[proj] += rc == "0"
    rows = []
    for proj, size in sizes.items():
        name = {"laravel-ai": "laravel-ai"}.get(proj, proj)
        if proj == "PHPMailer" or name not in compile_n:
            continue
        g = v2proj.get(name, {})
        rows.append({"project": proj, "ir_entities": size, "compile_validity": compile_ok[name] / compile_n[name],
                     "grounding": float(g["grounding_mean"]) if g.get("grounding_mean") else None,
                     "coverage": float(g["coverage_raw_mean"]) if g.get("coverage_raw_mean") else None})
    out = {"n_repositories": len(rows)}
    for metric in ("compile_validity", "grounding", "coverage"):
        pts = [(r["ir_entities"], r[metric]) for r in rows if r[metric] is not None]
        rho, lo, hi = spearman_ci([p[0] for p in pts], [p[1] for p in pts])
        out[metric] = {"rho": rho, "ci_low": lo, "ci_high": hi, "n": len(pts)}
    return rows, out


# ── archived cohort with the evaluator of Section 3.7 ────────────

def archived_cohort():
    """Per-type and per-repository tables for the archived 917 files.
    Compiler validity uses all 917 files; IR-based metrics use the 11 repositories of the corrected corpus."""
    compile_by = defaultdict(lambda: [0, 0])
    for line in COMPILER_TSV.read_text().splitlines():
        path, rc = line.split("\t")[:2]
        _, lang, proj, dtype, _ = path.split("/", 4)
        for key in (("type", dtype), ("lang", lang), ("proj", proj), ("lang_type", f"{lang}|{dtype}"), ("all", "all")):
            compile_by[key][0] += rc == "0"
            compile_by[key][1] += 1
    sizes = {r["project"]: int(r["ir_entities"]) for r in csv.DictReader((V2 / "corrected-corpus" / "per_repository_counts.csv").open())}
    batches = []
    for f in (RES / "archived").glob("*/*/batches.csv"):
        lang, proj = f.parts[-3], f.parts[-2]
        for row in csv.DictReader(f.open()):
            batches.append({**row, "language": lang, "project": proj})
    def m(rows, k):
        return fnum(mean([float(r[k]) for r in rows if r.get(k) not in ("", "None", None)]))
    type_rows = []
    for t in ["activity", "class", "component", "deployment", "sequence", "system_context", "use_case"]:
        rs = [r for r in batches if r["diagram_type"] == t]
        ok, n = compile_by[("type", t)]
        type_rows.append({"type": t, "files": n, "compile_valid": ok, "compile_validity": fnum(ok / n),
                          "entity_recall": m(rs, "entity_recall"), "grounded_element_rate": m(rs, "grounded_element_rate"),
                          "endpoint_grounding": m(rs, "endpoint_grounding"),
                          "relationship_precision_ir": m(rs, "relationship_precision_ir"),
                          "generalization_precision": m(rs, "generalization_precision"),
                          "mean_elements": m(rs, "mean_elements"), "mean_relationships": m(rs, "mean_relationships"),
                          "mean_sci": m(rs, "mean_sci"), "member_precision": m(rs, "member_precision"),
                          "member_recall": m(rs, "member_recall"),
                          "attribute_type_agreement": m(rs, "attribute_type_agreement")})
    proj_rows = []
    for proj in sorted({r["project"] for r in batches}):
        rs = [r for r in batches if r["project"] == proj]
        ok, n = compile_by[("proj", proj)]
        proj_rows.append({"language": rs[0]["language"], "project": proj, "ir_entities": sizes.get(proj), "files": n,
                          "compile_validity": fnum(ok / n), "entity_recall": m(rs, "entity_recall"),
                          "grounded_element_rate": m(rs, "grounded_element_rate"),
                          "endpoint_grounding": m(rs, "endpoint_grounding")})
    lang_rows = []
    for lang in ["Java", "JavaScript", "PHP", "Python"]:
        ok, n = compile_by[("lang", lang)]
        rs = [r for r in batches if r["language"] == lang]
        lang_rows.append({"language": lang, "files": n, "compile_validity": fnum(ok / n),
                          "entity_recall": m(rs, "entity_recall"), "endpoint_grounding": m(rs, "endpoint_grounding")})
    heat = [{"language": k[1].split("|")[0], "type": k[1].split("|")[1], "compile_validity": fnum(v[0] / v[1]), "files": v[1]}
            for k, v in compile_by.items() if k[0] == "lang_type"]
    size = {}
    for metric in ("entity_recall", "grounded_element_rate", "endpoint_grounding", "compile_validity"):
        pts = [(r["ir_entities"], r[metric]) for r in proj_rows if r["ir_entities"] and r[metric] is not None]
        rho, lo, hi = spearman_ci([p[0] for p in pts], [p[1] for p in pts])
        size[metric] = {"rho": rho, "ci_low": lo, "ci_high": hi, "n": len(pts)}
    ok, n = compile_by[("all", "all")]
    ci = binomtest(ok, n).proportion_ci(method="exact")
    return {"types": type_rows, "projects": proj_rows, "languages": lang_rows, "heatmap": heat, "size": size,
            "overall": {"compile_valid": ok, "files": n, "compile_validity": fnum(ok / n, 4),
                        "ci_low": fnum(ci.low, 4), "ci_high": fnum(ci.high, 4)}}


# ── main ────────────────────────────────────────────────────────

def main():
    global RUNS
    TABLES.mkdir(parents=True, exist_ok=True)
    RUNS = all_runs()
    runs = load_runs()
    re_meta = evaluate_references()
    cases = load_cases()
    conditions = sorted({r["condition"] for r in runs}) + (["reverse_engineering"] if re_meta else [])
    summary = {}

    # Table: per condition (all four types pooled; file-level validity, case-level metrics)
    cond_rows = []
    for cond in conditions:
        rs = [r for r in runs if r["condition"] == cond]
        ok = [r for r in rs if r["status"] == "ok"]
        files = sum(r["files"] for r in ok)
        valid = sum(r["valid_after"] for r in ok)
        ci = binomtest(valid, files).proportion_ci(method="exact") if files else None
        row = {"condition": cond, "label": LABEL.get(cond, cond), "runs": len(rs), "runs_ok": len(ok),
               "cells_ok": len({(r["project"], r["type"]) for r in ok}), "files": files,
               "compile_valid": valid, "compile_validity": fnum(valid / files if files else None),
               "ci_low": fnum(ci.low if ci else None), "ci_high": fnum(ci.high if ci else None),
               "usd_per_run": fnum(mean([r["cost"] for r in ok]), 3), "usd_total": fnum(sum(r["cost"] for r in rs), 2),
               "seconds_per_run": fnum(mean([r["seconds"] for r in ok]), 1),
               "ktokens_per_run": fnum((mean([r["tokens"] for r in ok]) or 0) / 1000, 1)}
        for metric in ("entity_recall", "grounded_element_rate", "endpoint_grounding", "relationship_precision_ir",
                       "generalization_precision", "mean_sci"):
            vals = [case_value(cases, cond, p, t, metric) for p in CORE for t in TYPES]
            row[metric] = fnum(mean(vals))
        cond_rows.append(row)
    _write(TABLES / "conditions.csv", cond_rows)
    summary["conditions"] = {r["condition"]: r for r in cond_rows}

    # Class-diagram table including the deterministic baseline
    class_rows = []
    for cond in conditions:
        vals = {m: fnum(mean([case_value(cases, cond, p, "class", m) for p in CORE])) for m in
                ("compile_validity", "entity_recall", "grounded_element_rate", "endpoint_grounding",
                 "relationship_precision_ir", "generalization_precision", "mean_elements",
                 "member_precision", "member_recall", "attribute_type_agreement")}
        class_rows.append({"condition": cond, "label": LABEL.get(cond, cond), **vals})
    _write(TABLES / "class_diagrams.csv", class_rows)
    summary["class"] = {r["condition"]: r for r in class_rows}
    summary["reverse_engineering_tools"] = re_meta

    # Paired comparisons (RQ3)
    comparisons = {}
    for a, b in [("single_view", "single_prefix"), ("single_view", "single_agent"), ("single_view", "plain_llm_1m"),
                 ("single_view", "single_view_opus46"), ("single_view", "single_view_sonnet45")]:
        for metric in ("compile_validity", "entity_recall", "grounded_element_rate", "endpoint_grounding",
                       "relationship_precision_ir"):
            comparisons[f"{a}__vs__{b}__{metric}"] = paired(cases, a, b, metric)
        # cost per case
        ca = {(r["project"], r["type"]): [] for r in runs}
        for r in runs:
            ca[(r["project"], r["type"])].append(r)
        costs = [(mean([r["cost"] for r in runs if r["condition"] == a and (r["project"], r["type"]) == k]),
                  mean([r["cost"] for r in runs if r["condition"] == b and (r["project"], r["type"]) == k])) for k in ca]
        costs = [c for c in costs if None not in c]
        if costs:
            comparisons[f"{a}__vs__{b}__cost"] = {"n": len(costs), "mean_a": fnum(mean([c[0] for c in costs]), 3),
                                                  "mean_b": fnum(mean([c[1] for c in costs]), 3),
                                                  "ratio_b_over_a": fnum(mean([c[1] for c in costs]) / mean([c[0] for c in costs]), 2)}
    summary["comparisons"] = comparisons
    _write(TABLES / "comparisons.csv", [{"comparison": k, **v} for k, v in comparisons.items()])

    # Run-to-run variability (3 repetitions of single_view / single_prefix)
    var = {}
    for cond in ("single_view", "single_prefix"):
        sds = []
        for p in CORE:
            for t in TYPES:
                reps = [v["entity_recall"] for v in cases.get((cond, p, t), {}).values() if v["entity_recall"] is not None]
                if len(reps) >= 2:
                    sds.append(st.stdev(reps))
        var[cond] = {"cells": len(sds), "mean_sd_entity_recall": fnum(mean(sds))}
    summary["variability"] = var

    # Cost / time per condition, type and repository (R1.5, R2.9)
    cost_rows = []
    for cond in sorted({r["condition"] for r in runs}):
        for p in CORE:
            for t in TYPES:
                rs = [r for r in runs if r["condition"] == cond and r["project"] == p and r["type"] == t and r["status"] == "ok"]
                if rs:
                    cost_rows.append({"condition": cond, "project": p.removeprefix("v2-"), "type": t, "runs": len(rs),
                                      "usd": fnum(mean([r["cost"] for r in rs]), 3),
                                      "seconds": fnum(mean([r["seconds"] for r in rs]), 1),
                                      "ktokens": fnum(mean([r["tokens"] for r in rs]) / 1000, 1),
                                      "sessions": fnum(mean([r["sessions"] for r in rs]), 1)})
    _write(TABLES / "cost_time.csv", cost_rows)
    summary["total_recorded_usd"] = fnum(sum(r["cost"] for r in runs), 2)
    summary["plain_200k"] = {"attempts": sum(1 for r in runs if r["condition"] == "plain_llm"),
                             "ok": sum(1 for r in runs if r["condition"] == "plain_llm" and r["status"] == "ok")}

    # Archived cohort with the Section 3.7 evaluator (Tables 2, 4, 5; RQ1, RQ2, RQ4)
    arch = archived_cohort()
    _write(TABLES / "archived_by_type.csv", arch["types"])
    _write(TABLES / "archived_by_project.csv", arch["projects"])
    _write(TABLES / "archived_by_language.csv", arch["languages"])
    _write(TABLES / "archived_heatmap.csv", arch["heatmap"])
    summary["archived"] = arch
    summary["size_correlation"] = arch["size"]

    summary["revised_full"] = revised_full()

    # Extractor validation against the reverse-engineering references (R1.1b)
    ev = RES / "extractor_validation" / "extractor_validation.csv"
    if ev.exists():
        summary["extractor_validation"] = list(csv.DictReader(ev.open()))

    (RES / "summary_full.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k in ("total_recorded_usd", "plain_200k", "variability",
                                                                  "size_correlation")}, indent=2))
    print(f"→ {RES / 'summary_full.json'} and {TABLES}/")


def _write(path, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
