#!/usr/bin/env python3
"""Adjudicate the relation-level audit rows (V1).

The automated audit in `audit_relations.py` resolves each diagram edge against a symbol
table built with line-oriented regular expressions. That table produced known artifacts:
multi-line generic headers, `implements`/`extends` lists with more than one entry, nested
(qualified) types, and type parameters were all mis-parsed, so a `not_found` row is not
by itself evidence that the diagram is wrong.

This script re-derives every inheritance/implementation edge from the pinned sources with
a depth-aware header parser plus a transitive-closure check, and records, per row:

  adjudication            verified_confirmed | artifact_direct | artifact_qualified |
                          artifact_transitive | external_target | confirmed_absent |
                          source_not_declared
  adjudication_evidence   file:line and the clause the decision is based on
  adjudication_method     how the verdict was produced (see ADJUDICATION_METHOD below)

ADJUDICATION_METHOD: deterministic re-check by script (improved parser + transitive
closure). No human review is claimed; `results/relation_audit_human_spotcheck.csv`
carries the seeded worksheet for the human spot-check that RELATION_AUDIT_METHOD.md
requires before publication.

Outputs:
  results/relation_audit_rows_adjudicated.csv   full table with adjudication columns
  results/relation_audit_adjudication.json      bucket counts and rates
  results/relation_audit_adjudication.md        human-readable summary
  results/relation_audit_human_spotcheck.csv    seeded human spot-check worksheet
"""

from __future__ import annotations

import ast
import csv
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOS = ROOT / "corpus" / "repos"
ROWS_IN = ROOT / "results" / "relation_audit_rows.csv"

ADJUDICATION_METHOD = "script re-check: depth-aware header parser + transitive closure"
SPOTCHECK_SEED = 20260918
SPOTCHECK_SAMPLE = 30
MAX_ANCESTOR_DEPTH = 6

EXTENSIONS = {"Java": (".java",), "PHP": (".php",), "Python": (".py",),
              "JavaScript": (".js", ".jsx", ".ts", ".tsx")}

EXCLUDED_PARTS = {"node_modules", "vendor", "dist", "build", "__pycache__", ".git",
                  ".github", "coverage", "fixtures", "docs", "examples", "test",
                  "tests", "__tests__", "spec", "min", "umd", "bundle", "browser-bundle"}
MAX_FILE_BYTES = 2_000_000


# ---------------------------------------------------------------- helpers

def norm(name: str) -> str:
    if not name:
        return ""
    name = name.replace("\\n", " ")
    previous = None
    while previous != name:
        previous = name
        name = re.sub(r"<[^<>]*>", "", name)
    name = name.strip().strip('"[]()').replace("\\", ".")
    return name.split(".")[-1].strip()


def qualified(name: str) -> str:
    return name.replace("\\n", " ").strip().strip('"[]()').replace("\\", ".")


def in_scope(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    for part in rel.parts[:-1]:
        if part in EXCLUDED_PARTS or (part.startswith(".") and len(part) > 1):
            return False
    lowered = rel.name.lower()
    if ".min." in lowered or ".bundle." in lowered:
        return False
    try:
        return path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def header_end(text: str, start: int) -> int:
    """Index of the opening brace of a declaration body, ignoring generics/parens."""
    depth_angle = depth_round = depth_square = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "<":
            depth_angle += 1
        elif char == ">":
            depth_angle = max(0, depth_angle - 1)
        elif char == "(":
            depth_round += 1
        elif char == ")":
            depth_round = max(0, depth_round - 1)
        elif char == "[":
            depth_square += 1
        elif char == "]":
            depth_square = max(0, depth_square - 1)
        elif char == "{" and depth_angle == 0 and depth_round == 0 and depth_square == 0:
            return index
        elif char in ";" and depth_angle == 0:
            return -1           # e.g. an interface forward declaration
        index += 1
    return -1


def split_type_list(clause: str) -> list[str]:
    """Split a comma-separated type list at depth 0 and drop generic arguments."""
    out, depth, current = [], 0, ""
    for char in clause:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            out.append(current)
            current = ""
            continue
        current += char
    out.append(current)
    cleaned = []
    for item in out:
        item = re.sub(r"<[^<>]*>", "", item).strip().strip("{}")
        item = item.strip().split()[-1] if item.strip() else ""
        if item:
            cleaned.append(item)
    return cleaned


def cut_at(text: str, keywords: tuple[str, ...]) -> str:
    """Keep only the part of a header before the first of the given keywords."""
    stop = len(text)
    for keyword in keywords:
        match = re.search(rf"\b{keyword}\b", text)
        if match:
            stop = min(stop, match.start())
    return text[:stop]


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


# ---------------------------------------------------------------- parsers

def parse_java(path: Path, text: str, root: Path) -> list[dict]:
    declarations = []
    pattern = re.compile(r"\b(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)")
    for match in pattern.finditer(text):
        kind, name = match.group(1), match.group(2)
        end = header_end(text, match.end())
        if end < 0:
            continue
        header = text[match.end():end]
        body = re.sub(r"^<[^<>]*(?:<[^<>]*>[^<>]*)*>", "", header, count=1)  # own type params
        extends_clause = cut_at(body, ("implements", "permits", "{"))
        implements_clause = ""
        match_impl = re.search(r"\bimplements\b(.*)$", body, re.S)
        if match_impl:
            implements_clause = cut_at(match_impl.group(1), ("permits", "{"))
        match_ext = re.search(r"\bextends\b(.*)$", extends_clause, re.S)
        parents = []
        if match_ext:
            parents += split_type_list(match_ext.group(1))
        if implements_clause:
            parents += split_type_list(implements_clause)
        declarations.append({
            "name": name, "kind": kind, "parents": parents,
            "file": str(path.relative_to(root)), "line": line_of(text, match.start()),
            "clause": " ".join(header.split())[:200],
        })
    return declarations


def parse_php(path: Path, text: str, root: Path) -> list[dict]:
    declarations = []
    pattern = re.compile(r"\b(class|interface|trait)\s+([A-Za-z_]\w*)")
    for match in pattern.finditer(text):
        kind, name = match.group(1), match.group(2)
        end = header_end(text, match.end())
        if end < 0:
            continue
        header = text[match.end():end]
        parents = []
        match_ext = re.search(r"\bextends\b(.*?)(?:\bimplements\b|$)", header, re.S)
        if match_ext:
            parents += split_type_list(match_ext.group(1))
        match_impl = re.search(r"\bimplements\b(.*)$", header, re.S)
        if match_impl:
            parents += split_type_list(match_impl.group(1))
        declarations.append({
            "name": name, "kind": kind, "parents": parents,
            "file": str(path.relative_to(root)), "line": line_of(text, match.start()),
            "clause": " ".join(header.split())[:200],
        })
    return declarations


def parse_js(path: Path, text: str, root: Path) -> list[dict]:
    declarations = []
    for match in re.finditer(r"\bclass\s+([A-Za-z_$][\w$]*)\s*(?:extends\s+([\w.$]+))?", text):
        parents = [match.group(2)] if match.group(2) else []
        declarations.append({
            "name": match.group(1), "kind": "class", "parents": parents,
            "file": str(path.relative_to(root)), "line": line_of(text, match.start()),
            "clause": match.group(0)[:200],
        })
    return declarations


def parse_python(path: Path, text: str, root: Path) -> list[dict]:
    declarations = []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return declarations
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        parents = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                parents.append(base.id)
            elif isinstance(base, ast.Attribute):
                parents.append(base.attr)
            elif isinstance(base, ast.Subscript):
                inner = base.value
                if isinstance(inner, ast.Name):
                    parents.append(inner.id)
                elif isinstance(inner, ast.Attribute):
                    parents.append(inner.attr)
        declarations.append({
            "name": node.name, "kind": "class", "parents": parents,
            "file": str(path.relative_to(root)), "line": node.lineno,
            "clause": f"class {node.name}({', '.join(parents)})",
        })
    return declarations


PARSERS = {"Java": (".java", parse_java), "PHP": (".php", parse_php),
           "Python": (".py", parse_python), "JavaScript": (".js", parse_js)}


class SourceIndex:
    """Declarations of one repository, indexed for edge adjudication."""

    def __init__(self, language: str, project: str):
        self.root = REPOS / language / project
        self.declarations: list[dict] = []
        for extension in EXTENSIONS.get(language, ()):
            for path in sorted(self.root.rglob(f"*{extension}")):
                if path.is_file() and in_scope(path, self.root):
                    try:
                        text = path.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    if language == "Java":
                        self.declarations += parse_java(path, text, self.root)
                    elif language == "PHP":
                        self.declarations += parse_php(path, text, self.root)
                    elif language == "Python":
                        self.declarations += parse_python(path, text, self.root)
                    else:
                        self.declarations += parse_js(path, text, self.root)
        self.by_name: dict[str, list[dict]] = {}
        for declaration in self.declarations:
            self.by_name.setdefault(norm(declaration["name"]), []).append(declaration)
        self.parents: dict[str, set[str]] = {}
        for declaration in self.declarations:
            self.parents.setdefault(norm(declaration["name"]), set()).update(
                norm(parent) for parent in declaration["parents"] if parent)
        self.ancestors_cache: dict[str, set[str]] = {}

    def ancestors(self, name: str) -> set[str]:
        name = norm(name)
        if name in self.ancestors_cache:
            return self.ancestors_cache[name]
        seen: set[str] = set()
        frontier = [name]
        for _ in range(MAX_ANCESTOR_DEPTH):
            nxt = []
            for current in frontier:
                for parent in self.parents.get(current, ()):  # type: ignore[arg-type]
                    if parent and parent not in seen:
                        seen.add(parent)
                        nxt.append(parent)
            if not nxt:
                break
            frontier = nxt
        self.ancestors_cache[name] = seen
        return seen

    def entries(self, name: str) -> list[dict]:
        """Declarations matching a diagram label.

        A qualified label such as `OraclePartition.Provider` names a nested type, so the
        declaration in `OraclePartition.java` is preferred over any unrelated class that
        happens to share the tail name.
        """
        parts = [part for part in qualified(name).split(".") if part]
        candidates = self.by_name.get(norm(name), [])
        if len(parts) > 1 and candidates:
            preferred = [entry for entry in candidates
                         if Path(entry["file"]).stem == parts[0]]
            if preferred:
                return preferred
        if candidates:
            return candidates
        if not parts:
            return []
        return self.by_name.get(norm(parts[-1]), [])

    def declared(self, name: str) -> bool:
        return bool(self.entries(name))


def evidence(index: SourceIndex, source: str, target: str, kind: str) -> tuple[str, str | None]:
    """Return (adjudication, evidence) for one edge."""
    entries = index.entries(source)
    if not entries:
        return "source_not_declared", None
    parts = [part for part in qualified(source).split(".") if part]
    basis = "qualifier-matched file" if len(parts) > 1 and \
        Path(entries[0]["file"]).stem == parts[0] else "name match"
    target_norm = norm(target)
    for entry in entries:
        for parent in entry["parents"]:
            if norm(parent) == target_norm:
                return "artifact_direct", (
                    f"{entry['file']}:{entry['line']} declares {entry['name']} with parent "
                    f"{parent!r} ({basis}; clause: {entry['clause']})"
                )
            if qualified(parent).split(".")[0] == qualified(target).split(".")[0] and \
                    len(qualified(parent).split(".")) > 1:
                return "artifact_qualified", (
                    f"{entry['file']}:{entry['line']} declares {entry['name']} with qualified "
                    f"parent {parent!r} ({basis}; clause: {entry['clause']})"
                )
    if target_norm in index.ancestors(source):
        return "artifact_transitive", (
            f"{target_norm} is a transitive ancestor of {norm(source)} in the pinned sources"
        )
    for entry in index.entries(target):
        if norm(source) in {norm(parent) for parent in entry["parents"]}:
            return "direction_reversed", (
                f"{entry['file']}:{entry['line']} declares {entry['name']} with parent "
                f"{entry['parents']} — the statement holds in the opposite direction"
            )
    target_parts = [part for part in qualified(target).split(".") if part]
    if len(target_parts) > 1 and target_parts[0][:1].islower():
        return "external_target", (
            f"{target!r} is module-qualified; the method resolves repository-local type "
            f"names only"
        )
    if not index.declared(target):
        return "external_target", f"{target!r} is not declared in the repository"
    described = "; ".join(
        f"{entry['file']}:{entry['line']} {entry['name']} parents "
        f"{entry['parents'] or 'none'}" for entry in entries[:3]
    ) + f" ({basis})"
    return "confirmed_absent", described


# ---------------------------------------------------------------- driver

def main() -> None:
    with ROWS_IN.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    print(f"rows in: {len(rows)}")

    indexes: dict[tuple[str, str], SourceIndex] = {}

    def index_for(language: str, project: str) -> SourceIndex:
        key = (language, project)
        if key not in indexes:
            indexes[key] = SourceIndex(language, project)
            print(f"  indexed {language}/{project}: "
                  f"{len(indexes[key].declarations)} declarations")
        return indexes[key]

    buckets: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        if row["condition"] == "excluded_mislabeled":
            row["adjudication"] = "excluded_mislabeled"
            row["adjudication_evidence"] = ""
            row["adjudication_method"] = ADJUDICATION_METHOD
            continue
        if row["kind"] != "inherit" or row["status"] == "not_checkable_dynamic_language":
            row["adjudication"] = row["status"]
            row["adjudication_evidence"] = ""
            row["adjudication_method"] = ADJUDICATION_METHOD
            continue
        index = index_for(row["language"], row["project"])
        verdict, note = evidence(index, row["source_class"], row["to_class"], row["kind"])
        positive = {"artifact_direct", "artifact_qualified", "artifact_transitive"}
        if row["status"] == "verified":
            row["adjudication"] = "verified_confirmed" if verdict in positive \
                else "verified_disputed"
        elif row["status"] == "not_found":
            row["adjudication"] = verdict
        elif row["status"] == "unresolved_source_class":
            if verdict in positive:
                row["adjudication"] = "artifact_from_unresolved"
            elif verdict == "source_not_declared":
                row["adjudication"] = "source_not_declared"
            else:
                row["adjudication"] = f"resolved_{verdict}"
        else:
            row["adjudication"] = verdict
        row["adjudication_evidence"] = note or ""
        row["adjudication_method"] = ADJUDICATION_METHOD
        key = (row["corpus"], row["status"])
        buckets.setdefault(key, {}).setdefault(row["adjudication"], 0)
        buckets[key][row["adjudication"]] += 1

    out_rows = ROOT / "results" / "relation_audit_rows_adjudicated.csv"
    fieldnames = list(rows[0].keys())
    with out_rows.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out_rows}")

    # rates: over inheritance edges whose endpoints both resolve in the pinned sources
    POSITIVE = {"verified_confirmed", "artifact_direct", "artifact_qualified",
                "artifact_transitive", "artifact_from_unresolved"}
    NEGATIVE = {"confirmed_absent", "resolved_confirmed_absent", "direction_reversed"}
    OUT_OF_SCOPE = {"external_target", "resolved_external_target", "source_not_declared"}
    summary: dict[str, dict] = {}
    for corpus in sorted({row["corpus"] for row in rows}):
        inherit = [row for row in rows
                   if row["corpus"] == corpus and row["kind"] == "inherit"
                   and row["condition"] != "excluded_mislabeled"]
        counts: dict[str, int] = {}
        for row in inherit:
            counts[row["adjudication"]] = counts.get(row["adjudication"], 0) + 1
        positive = sum(count for name, count in counts.items() if name in POSITIVE)
        negative = sum(count for name, count in counts.items() if name in NEGATIVE)
        out_of_scope = sum(count for name, count in counts.items() if name in OUT_OF_SCOPE)
        disputed = counts.get("verified_disputed", 0)
        screen_positive = sum(1 for row in inherit if row["status"] == "verified")
        screen_negative = sum(1 for row in inherit if row["status"] == "not_found")
        summary[corpus] = {
            "inherit_edges": len(inherit),
            "buckets": counts,
            "adjudicated_positive": positive,
            "adjudicated_negative": negative,
            "out_of_scope": out_of_scope,
            "verified_disputed": disputed,
            "rate_adjudicated_of_checkable": (
                round(positive / (positive + negative), 4) if positive + negative else None
            ),
            "screen_positive": screen_positive,
            "screen_negative": screen_negative,
            "rate_screen": (
                round(screen_positive / max(1, screen_positive + screen_negative), 4)
            ),
        }

    (ROOT / "results" / "relation_audit_adjudication.json").write_text(
        json.dumps({"method": ADJUDICATION_METHOD, "by_corpus": summary,
                    "transition_counts": {f"{k[0]}|{k[1]}": v for k, v in buckets.items()}},
                   indent=2),
        encoding="utf-8",
    )

    # seeded human spot-check worksheet: every confirmed_absent row plus a random sample
    rng = random.Random(SPOTCHECK_SEED)
    disputed = [row for row in rows if row["adjudication"] in
                ("confirmed_absent", "resolved_confirmed_absent", "source_not_declared",
                 "verified_disputed")]
    pool = [row for row in rows if row["adjudication"] in
            ("verified_confirmed", "artifact_direct", "artifact_qualified",
             "artifact_transitive", "artifact_from_unresolved", "external_target",
             "resolved_external_target")]
    sample = rng.sample(pool, min(SPOTCHECK_SAMPLE, len(pool)))
    worksheet = disputed + sample
    spot = ROOT / "results" / "relation_audit_human_spotcheck.csv"
    with spot.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames + ["human_verdict", "human_note"])
        writer.writeheader()
        for row in worksheet:
            writer.writerow({**row, "human_verdict": "", "human_note": ""})
    print(f"wrote {spot} ({len(worksheet)} rows: {len(disputed)} disputed + "
          f"{len(sample)} sampled)")

    lines = ["# Relation audit — adjudication of inheritance/implementation edges", "",
             f"Method: {ADJUDICATION_METHOD}. No human review is claimed here; the seeded "
             "worksheet in `relation_audit_human_spotcheck.csv` carries the pending human "
             "spot-check.", ""]
    for corpus, data in summary.items():
        lines += [f"## {corpus}", "",
                  f"- inheritance edges: {data['inherit_edges']}",
                  f"- automated screen: {data['screen_positive']} verified / "
                  f"{data['screen_positive'] + data['screen_negative']} checkable = "
                  f"{data['rate_screen']:.1%}",
                  f"- adjudicated: {data['adjudicated_positive']} confirmed / "
                  f"{data['adjudicated_positive'] + data['adjudicated_negative']} with both "
                  f"endpoints resolvable = {data['rate_adjudicated_of_checkable']:.1%}",
                  f"- endpoints not resolvable in the pinned sources (outside the method): "
                  f"{data['out_of_scope']}",
                  "", "| verdict | edges |", "|---|---:|"]
        for verdict, count in sorted(data["buckets"].items(), key=lambda kv: -kv[1]):
            lines.append(f"| {verdict} | {count} |")
        lines.append("")
    (ROOT / "results" / "relation_audit_adjudication.md").write_text(
        "\n".join(lines), encoding="utf-8")
    print("wrote results/relation_audit_adjudication.md")
    for corpus, data in summary.items():
        print(f"{corpus}: screen {data['rate_screen']:.1%} -> adjudicated "
              f"{data['rate_adjudicated_of_checkable']:.1%} "
              f"({data['adjudicated_positive']} of "
              f"{data['adjudicated_positive'] + data['adjudicated_negative']}; "
              f"{data['out_of_scope']} out of scope)")


if __name__ == "__main__":
    main()
