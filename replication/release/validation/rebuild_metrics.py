#!/usr/bin/env python3
"""
rebuild_metrics.py — Code2UML v2 reconciliation script.

Recomputes all reported artifact metrics directly from the released corpus
(`uml-study-data-main/`) so that every number in the manuscript can be
traced to a public script + public data.

Outputs (into v2-rebuild/results/):
  - per_case.csv      one row per repository x diagram-type case (84 rows)
  - by_type.csv       means by diagram type
  - by_language.csv   means by language
  - by_project.csv    means by project
  - summary.json      machine-readable summary

Counters (both are reported, deliberately):
  - published: element/relationship detection exactly as in the original
    pipeline counter (Archive/app/agent/metrics.py). Kept for continuity
    with the manuscript's Table 6.
  - extended:  published + activity-diagram constructs (actions, decisions,
    swimlanes, start/stop, forks). The original counter detects ZERO
    elements in activity files, which is why activity rows in earlier
    analyses were computed with an unreleased variant.

Proxy replacements for the retired composite "Structural Proxy Score":
  connectivity_pct  share of declared elements with >= 1 relationship
  labeling_pct      share of relationship lines carrying a ': label'
  notes_per_diagram count of note/legend blocks and single-line notes

Endpoint grounding: fraction of parsed relationships whose two endpoint
names resolve to entities declared (with aliases) in the same diagram.
Limitations (documented in RESULTS_SUMMARY.md): IR-side grounding is not
checked because full post-processed IRs are not part of the released set;
quoted multiplicity tokens and dynamic constructions can depress it.

Renders: PNG presence per file = de facto compile check of the original run
(917 files, 864 rendered). The official independent check is
scripts/check_syntax.sh (PlantUML CLI).

Usage:  python3 rebuild_metrics.py [--root PATH] [--out PATH]
"""

import argparse
import csv
import glob
import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict

# --------------------------------------------------------------------------
# Counter definitions
# --------------------------------------------------------------------------

# Published keyword counter (verbatim from the original pipeline).
ELEMENT_KEYWORDS = [
    "class ", "interface ", "abstract ", "enum ", "actor ", "usecase ",
    "participant ", "component ", "node ", "database ", "rectangle ",
    "cloud ", "artifact ", "package ", "folder ", "frame ",
    "queue ", "stack ", "card ", "collections ",
]

# Extended declaration regex (superset incl. all published forms).
DECL_RE = re.compile(
    r'^\s*(?:abstract\s+)?'
    r'(class|interface|enum|actor|usecase|participant|component|node|database|'
    r'rectangle|cloud|artifact|package|folder|frame|queue|stack|card|collections|'
    r'boundary|control|entity)\b\s*(.*)$'
)
ALIAS_RE = re.compile(r'\bas\s+("[^"]+"|[\w.\-]+)')
# A name token: quoted, bracketed [Component], parenthesized (UseCase), or plain.
TOKEN_RE = re.compile(r'^\s*(?:"([^"]*)"|\[([^\]]*)\]|\(([^)]*)\)|([\w.\-]+))')
END_TOKEN_RE = re.compile(r'("[^"]*"|\[[^\]]*\]|\([^)]*\)|[\w.\-]+)\s*$')
# Shorthand declarations: `[Name] as alias` (components) / `(Name) as alias` (use cases).
BRACKET_DECL_RE = re.compile(r'^\s*\[([^\]]*)\]\s*(?:\bas\s+("[^"]+"|[\w.\-]+))?\s*$')
PAREN_DECL_RE = re.compile(r'^\s*\(([^)]*)\)\s*(?:\bas\s+("[^"]+"|[\w.\-]+))?\s*$')


def token(s):
    """Return (value, rest) for the leading name token, or (None, s)."""
    m = TOKEN_RE.match(s)
    if not m:
        return None, s
    val = next(g for g in m.groups() if g is not None)
    return val.strip(), s[m.end():]

# Activity-diagram constructs (extended counter only).
ACT_STARTSTOP_RE = re.compile(r'^\s*(start|stop|end|detach|kill)\s*$')
ACT_ACTION_RE = re.compile(r'^\s*[#\w]*\s*:[^;]*;\s*$')
ACT_DECISION_RE = re.compile(r'^\s*if\s*\(')
ACT_LOOP_RE = re.compile(r'^\s*(while|repeat|backward)\b')
ACT_FORK_RE = re.compile(r'^\s*(fork|split|end\s*(fork|split|merge))\b')
ACT_LANE_RE = re.compile(r'^\s*\|([^|]+)\|\s*$')

# Relationship arrows (verbatim set from the original pipeline).
ARROWS = ["--|>", "<|--", "<|..", "..|>", "-->", "<--", "..>", "<..",
          "--o", "o--", "--*", "*--", "->", "<-", ".>", "<.", "--", ".."]
ARROW_ALT = sorted(ARROWS, key=len, reverse=True)
ARROW_RE = re.compile("|".join(re.escape(a) for a in ARROW_ALT))
LINE_ARROW_RE = re.compile("|".join(re.escape(a) for a in ARROWS))

BLOCK_OPEN_RE = re.compile(r'^\s*(note|hnote|rnote|legend|header|footer)\b', re.I)
BLOCK_END_RE = re.compile(r'^\s*end\s*(note|hnote|rnote|legend|header|footer|legend)\s*$', re.I)


def strip_blocks(text: str) -> str:
    """Remove /' ... '/ block comments and note/legend blocks (line-wise)."""
    text = re.sub(r"/'.*?'/", "", text, flags=re.S)
    out, in_block = [], False
    for line in text.split("\n"):
        s = line.strip()
        if in_block:
            if BLOCK_END_RE.match(s):
                in_block = False
            continue
        if BLOCK_OPEN_RE.match(s) and ":" not in s:
            in_block = True          # multi-line block (single-line notes keep ':')
            continue
        out.append(line)
    return "\n".join(out)


def count_published_elements(text: str) -> int:
    n = 0
    for line in text.split("\n"):
        s = line.strip()
        if any(s.startswith(k) for k in ELEMENT_KEYWORDS):
            n += 1
    return n


def count_extended_elements(text: str) -> int:
    n = count_published_elements(text)
    lanes = set()
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("'") or s.startswith("@"):
            continue
        if DECL_RE.match(s):
            continue  # already counted by published counter
        if BRACKET_DECL_RE.match(s) or PAREN_DECL_RE.match(s):
            n += 1  # shorthand declaration ([Component] / (UseCase))
            continue
        if ACT_STARTSTOP_RE.match(s) or ACT_ACTION_RE.match(s) \
           or ACT_DECISION_RE.match(s) or ACT_LOOP_RE.match(s) or ACT_FORK_RE.match(s):
            n += 1
        m = ACT_LANE_RE.match(s)
        if m:
            lanes.add(m.group(1).strip())
    return n + len(lanes)


def count_relationship_lines(text: str):
    """Line-based relationship count (published rules). Returns (n_lines, {arrow})"""
    n = 0
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("'") or s.startswith("@") or s.startswith("!"):
            continue
        if LINE_ARROW_RE.search(s) and not s.endswith("{") and not s.endswith("}"):
            n += 1
    return n


def declared_names(text: str):
    """Return (names_set, alias_map) of diagram-declared entities."""
    names, alias_map = set(), {}

    def add(principal, alias=None):
        if principal:
            names.add(principal)
        if alias:
            alias = alias.strip().strip('"')
            names.add(alias)
            alias_map[alias] = principal

    for line in text.split("\n"):
        s = line.strip()
        m = DECL_RE.match(line)
        if m:
            principal, _ = token(m.group(2).strip())
            am = ALIAS_RE.search(line)
            add(principal, am.group(1) if am else None)
            continue
        m = BRACKET_DECL_RE.match(s)
        if m:
            add(m.group(1).strip(), m.group(2))
            continue
        m = PAREN_DECL_RE.match(s)
        if m:
            add(m.group(1).strip(), m.group(2))
    return names, alias_map


def _clean(tok):
    return tok.strip().strip('"').strip("[]").strip("()").strip()


def endpoints(line: str):
    """Extract (left, right) endpoint tokens around the first arrow on a line."""
    m = ARROW_RE.search(line)
    if not m:
        return None
    left, right = line[:m.start()], line[m.end():]
    right = right.split(":", 1)[0]  # drop ': label'
    lm = END_TOKEN_RE.search(left)
    rname, _ = token(right.strip())
    if not lm or not rname:
        return None
    return _clean(lm.group(1)), _clean(rname)


def grounding(text: str, names, alias_map):
    """Return (grounded_events, total_events)."""
    g = t = 0
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("'") or s.startswith("@") or s.startswith("!"):
            continue
        if s.endswith("{") or s.endswith("}"):
            continue
        for _ in ARROW_RE.finditer(s):
            ep = endpoints(s)
            if ep is None:
                continue
            a, b = ep
            a = alias_map.get(a, a)
            b = alias_map.get(b, b)
            t += 1
            if a in names and b in names:
                g += 1
    return g, t


def labeling_pct(text: str) -> float:
    tot = lab = 0
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("'") or s.startswith("@"):
            continue
        if LINE_ARROW_RE.search(s) and not s.endswith("{") and not s.endswith("}"):
            tot += 1
            if ":" in s:
                lab += 1
    return 100.0 * lab / tot if tot else 0.0


def connectivity_pct(text: str, names) -> float:
    if not names:
        return 0.0
    connected = set()
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("'") or s.startswith("@"):
            continue
        ep = endpoints(s)
        if ep:
            connected.update(ep)
    if not names:
        return 0.0
    return 100.0 * len(connected & names) / len(names)


def notes_count(text: str) -> int:
    n = 0
    for line in text.split("\n"):
        s = line.strip()
        if re.match(r'^(note|hnote|rnote)\b', s, re.I):
            n += 1
    return n


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser(
        "~/Desktop/UML upload/uml-study-data-main"))
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rows = []
    for mpath in sorted(glob.glob(os.path.join(args.root, "*", "*", "*", "metrics.json"))):
        case_dir = os.path.dirname(mpath)
        rel = os.path.relpath(case_dir, args.root)
        lang, project, dtype = rel.split(os.sep)
        meta = json.load(open(mpath))

        files = sorted(glob.glob(os.path.join(case_dir, "*.puml")))
        pub_e = ext_e = rel_lines = 0
        sci_pub, sci_ext = [], []
        g_tot = g_ground = 0
        render_pass = 0
        conns, labels, notes = [], [], []
        for f in files:
            text = strip_blocks(open(f, encoding="utf8", errors="ignore").read())
            pe = count_published_elements(text)
            ee = count_extended_elements(text)
            rl = count_relationship_lines(text)
            pub_e += pe; ext_e += ee; rel_lines += rl
            if pe:
                sci_pub.append(pe * math.log2(1 + 2 * rl / pe))
            if ee:
                sci_ext.append(ee * math.log2(1 + 2 * rl / ee))
            names, alias_map = declared_names(text)
            gg, tt = grounding(text, names, alias_map)
            g_ground += gg; g_tot += tt
            if os.path.exists(f[:-5] + ".png"):
                render_pass += 1
            conns.append(connectivity_pct(text, names))
            labels.append(labeling_pct(text))
            notes.append(notes_count(text))

        ir = meta.get("total_ir_elements", 0)
        rows.append({
            "language": lang, "project": project, "type": dtype,
            "files": len(files), "renders_ok": render_pass,
            "elements_published": pub_e, "elements_extended": ext_e,
            "rel_lines": rel_lines,
            "sci_published_mean": round(statistics.mean(sci_pub), 2) if sci_pub else 0.0,
            "sci_extended_mean": round(statistics.mean(sci_ext), 2) if sci_ext else 0.0,
            "grounding": round(g_ground / g_tot, 3) if g_tot else None,
            "grounding_events": g_tot,
            "connectivity_pct": round(statistics.mean(conns), 1) if conns else 0.0,
            "labeling_pct": round(statistics.mean(labels), 1) if labels else 0.0,
            "notes_mean": round(statistics.mean(notes), 1) if notes else 0.0,
            "ir_elements": ir,
            "coverage_raw": round(meta.get("total_elements_in_diagrams", 0) / ir, 3) if ir else None,
            "coverage_capped_pct": meta.get("code_coverage_pct"),
            "internal_validity_pct": meta.get("syntax_validity_pct"),
            "pipeline_elements": meta.get("total_elements_in_diagrams"),
        })

    def agg(rows, keyf):
        out = defaultdict(list)
        for r in rows:
            out[keyf(r)].append(r)
        res = []
        for k, rs in sorted(out.items()):
            g = [r["grounding"] for r in rs if r["grounding"] is not None]
            res.append({
                "group": k, "cases": len(rs),
                "files": sum(r["files"] for r in rs),
                "renders_ok": sum(r["renders_ok"] for r in rs),
                "render_pct": round(100.0 * sum(r["renders_ok"] for r in rs) / sum(r["files"] for r in rs), 1),
                "elements_extended_mean": round(statistics.mean(r["elements_extended"] for r in rs), 1),
                "rel_lines_mean": round(statistics.mean(r["rel_lines"] for r in rs), 1),
                "sci_extended_mean": round(statistics.mean(r["sci_extended_mean"] for r in rs), 1),
                "grounding_mean": round(statistics.mean(g), 3) if g else None,
                "connectivity_pct_mean": round(statistics.mean(r["connectivity_pct"] for r in rs), 1),
                "labeling_pct_mean": round(statistics.mean(r["labeling_pct"] for r in rs), 1),
                "coverage_raw_mean": round(statistics.mean(r["coverage_raw"] for r in rs if r["coverage_raw"] is not None), 3),
                "internal_validity_mean": round(statistics.mean(r["internal_validity_pct"] for r in rs), 1),
            })
        return res

    def write_csv(path, data):
        if not data:
            return
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
            w.writeheader()
            w.writerows(data)

    write_csv(os.path.join(args.out, "per_case.csv"), rows)
    write_csv(os.path.join(args.out, "by_type.csv"), agg(rows, lambda r: r["type"]))
    write_csv(os.path.join(args.out, "by_language.csv"), agg(rows, lambda r: r["language"]))
    write_csv(os.path.join(args.out, "by_project.csv"), agg(rows, lambda r: r["language"] + "/" + r["project"]))

    full = [r for r in rows]

    def overall(sub):
        g = [r["grounding"] for r in sub if r["grounding"] is not None]
        return {
            "cases": len(sub),
            "files": sum(r["files"] for r in sub),
            "renders_ok": sum(r["renders_ok"] for r in sub),
            "render_pct": round(100.0 * sum(r["renders_ok"] for r in sub) / sum(r["files"] for r in sub), 1),
            "grounding_mean": round(statistics.mean(g), 3) if g else None,
            "coverage_raw_mean": round(statistics.mean(r["coverage_raw"] for r in sub if r["coverage_raw"] is not None), 3),
            "connectivity_pct_mean": round(statistics.mean(r["connectivity_pct"] for r in sub), 1),
            "labeling_pct_mean": round(statistics.mean(r["labeling_pct"] for r in sub), 1),
            "internal_validity_mean": round(statistics.mean(r["internal_validity_pct"] for r in sub), 1),
        }

    with open(os.path.join(args.out, "summary.json"), "w") as fh:
        json.dump({"overall": overall(full),
                   "by_type": agg(rows, lambda r: r["type"]),
                   "by_language": agg(rows, lambda r: r["language"])}, fh, indent=2)

    ov = overall(full)
    print("=== OVERALL ===")
    print(f"  cases: {ov['cases']}   files: {ov['files']}   rendered: {ov['renders_ok']} "
          f"({ov['render_pct']}%)")
    print(f"  grounding mean: {ov['grounding_mean']}  coverage_raw mean: {ov['coverage_raw_mean']}")
    print(f"  connectivity: {ov['connectivity_pct_mean']}%  labeling: {ov['labeling_pct_mean']}%")
    print(f"  internal validity mean: {ov['internal_validity_mean']}%")
    print("\n=== BY TYPE ===")
    for r in agg(rows, lambda r: r["type"]):
        print(f"  {r['group']:15s} files={r['files']:3d} render={r['render_pct']:5.1f}% "
              f"ground={r['grounding_mean']}  sci={r['sci_extended_mean']:6.1f}  "
              f"cov_raw={r['coverage_raw_mean']:.3f}  conn={r['connectivity_pct_mean']:5.1f} "
              f"label={r['labeling_pct_mean']:5.1f}")
    print("\n=== BY LANGUAGE ===")
    for r in agg(rows, lambda r: r["language"]):
        print(f"  {r['group']:11s} files={r['files']:3d} render={r['render_pct']:5.1f}% "
              f"ground={r['grounding_mean']}  cov_raw={r['coverage_raw_mean']:.3f}")
    print(f"\nCSV + summary written to {args.out}")


if __name__ == "__main__":
    main()
