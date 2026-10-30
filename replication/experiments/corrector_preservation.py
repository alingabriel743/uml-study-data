"""
Structure preservation of the retrospective correction experiment (R2).

For every archived file that the revised CorrectorAgent changed, the preserved original (_original/) and the
corrected file are parsed with the evaluator's PlantUML parser, and the parsed structure is compared:

  element retention       share of the original's elements (normalized names) still present after correction
  relationship retention  share of the original's directed relationships (source, target) still present
  member retention        share of the original's class members (owner, member name) still present (class diagrams)
  added                   elements / relationships present only after correction

The parser is tolerant, so it reads most of the structure of a file that the compiler rejects. Files whose
original yields no elements are reported separately rather than counted as preserved.

  python -m experiments.corrector_preservation
"""

import csv
import json
from pathlib import Path

from app.evaluation.evaluate import normalize
from app.evaluation.puml_parser import parse_puml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments" / "results" / "corrector_retro"


def structure(path: Path, dtype: str):
    d = parse_puml(path.read_text(encoding="utf-8", errors="replace"), dtype)
    name = {k: normalize(e.label or e.name or k) for k, e in d.elements.items()}
    elements = set(name.values()) - {""}
    rels = {(name.get(r.src, normalize(r.src)), name.get(r.dst, normalize(r.dst))) for r in d.relationships}
    members = {(name[k], m.name) for k, e in d.elements.items() for m in e.members}
    return elements, rels, members


def share(before: set, after: set):
    return len(before & after) / len(before) if before else None


def main():
    rows = json.loads((OUT / "results.json").read_text())
    out = []
    for r in rows:
        if r.get("valid_before") is None:
            continue
        rel = Path(r["file"])
        fixed = OUT / "files" / rel
        original = fixed.parent / "_original" / fixed.name
        row = {k: r.get(k) for k in ("file", "language", "project", "type", "valid_before", "valid_after", "changed",
                                     "cost_usd", "seconds")}
        if original.exists():
            eb, rb, mb = structure(original, r["type"])
            ea, ra, ma = structure(fixed, r["type"])
            row.update({"elements_before": len(eb), "elements_after": len(ea),
                        "element_retention": share(eb, ea), "elements_added": len(ea - eb),
                        "relationships_before": len(rb), "relationships_after": len(ra),
                        "relationship_retention": share(rb, ra), "relationships_added": len(ra - rb),
                        "members_before": len(mb), "members_after": len(ma), "member_retention": share(mb, ma)})
        out.append(row)
    keys = sorted({k for r in out for k in r}, key=lambda k: list(out[0]).index(k) if k in out[0] else 99)
    with (OUT / "preservation.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(out)

    def pooled(rs, a, b):
        num = sum(r[a] * r[b] for r in rs if r.get(a) is not None)
        den = sum(r[b] for r in rs if r.get(a) is not None)
        return num / den if den else None

    ran = out
    changed = [r for r in ran if "element_retention" in r]
    summary = {
        "files": len(ran),
        "valid_after": sum(1 for r in ran if r["valid_after"]),
        "changed": len(changed),
        "cost_usd": round(sum(r["cost_usd"] or 0 for r in ran), 2),
        "mean_seconds": round(sum(r["seconds"] or 0 for r in ran) / len(ran), 1) if ran else None,
        "element_retention_pooled": pooled(changed, "element_retention", "elements_before"),
        "relationship_retention_pooled": pooled(changed, "relationship_retention", "relationships_before"),
        "member_retention_pooled": pooled(changed, "member_retention", "members_before"),
        "files_all_elements_kept": sum(1 for r in changed if r["element_retention"] == 1.0),
        "files_all_relationships_kept": sum(1 for r in changed if r["relationship_retention"] in (1.0, None)),
        "files_original_unparsed": sum(1 for r in changed if r["elements_before"] == 0),
        "elements_added": sum(r["elements_added"] for r in changed),
        "relationships_added": sum(r["relationships_added"] for r in changed),
        "elements_before": sum(r["elements_before"] for r in changed),
        "relationships_before": sum(r["relationships_before"] for r in changed),
        "by_type": {},
    }
    repaired = [r for r in changed if r["valid_after"]]
    summary["repaired"] = {
        "files": len(repaired),
        "element_retention_pooled": pooled(repaired, "element_retention", "elements_before"),
        "relationship_retention_pooled": pooled(repaired, "relationship_retention", "relationships_before"),
        "elements_added": sum(r["elements_added"] for r in repaired),
        "relationships_added": sum(r["relationships_added"] for r in repaired),
        "files_original_unparsed": sum(1 for r in repaired if r["elements_before"] == 0),
    }
    parsed = [r for r in repaired if r["elements_before"] > 0]
    summary["repaired_parsed"] = {
        "files": len(parsed),
        "element_retention_pooled": pooled(parsed, "element_retention", "elements_before"),
        "relationship_retention_pooled": pooled(parsed, "relationship_retention", "relationships_before"),
        "elements_added": sum(r["elements_added"] for r in parsed),
        "relationships_added": sum(r["relationships_added"] for r in parsed),
    }
    for t in sorted({r["type"] for r in ran}):
        rs = [r for r in ran if r["type"] == t]
        summary["by_type"][t] = {"files": len(rs), "valid_after": sum(1 for r in rs if r["valid_after"])}
    (OUT / "preservation_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
