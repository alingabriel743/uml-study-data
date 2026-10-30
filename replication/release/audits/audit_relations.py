#!/usr/bin/env python3
"""Automated relation-level verification for class diagrams (Code2UML audit).

For every relationship edge in generated class diagrams, resolve it against the
production source of the pinned repository and record evidence.

Primary outcome (strict, all languages): inheritance / implementation edges
    (--|>, <|--, ..|>, <|..) verified against `extends` / `implements` / base-class
    declarations in the source.
Secondary outcome (statically typed languages only: Java, PHP, TS): association
    edges (-->, <--, ..>, <.., --, .., *--, --*, o--, --o) verified as a member of
    the endpoint type in the other class (or reverse direction).
Not checkable by this method: association edges in dynamically typed JavaScript/
    Python (reported as such, excluded from rates); behavioral/flow content of
    sequence, activity, use-case and architecture diagrams (out of audit scope).

Known exclusion: the archived folder PHP/PHPMailer is a mislabeled corpus case
(Datadog/WordPress/Drupal content); it is retained in the audit only as a canary
and flagged separately.

Outputs:
  results/relation_audit_rows.csv      one row per relationship edge
  results/relation_audit_summary.json  aggregate rates and status counts
"""

from __future__ import annotations

import ast
import csv
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "independent_verification"))
from rebuild_metrics import strip_blocks, declared_names, endpoints  # noqa: E402

ARCHIVED = Path.home() / "Desktop" / "UML upload" / "uml-study-data-main"
REPOS = ROOT / "corpus" / "repos"
RUNS = ROOT / "data" / "experiment_runs"

INHERIT_ARROWS = {"--|>": (0, 1), "<|--": (1, 0), "..|>": (0, 1), "<|..": (1, 0)}
ASSOC_ARROWS = {"-->", "<--", "..>", "<..", "--o", "o--", "--*", "*--", "--", ".."}
ARROW_RE = re.compile("|".join(sorted(
    (re.escape(a) for a in list(INHERIT_ARROWS) + list(ASSOC_ARROWS)),
    key=len, reverse=True)))
EXCLUDED_PARTS = {"node_modules", "vendor", "dist", "build", "__pycache__",
                  ".git", ".github", "coverage", "fixtures", "docs", "examples",
                  "test", "tests", "__tests__", "spec", "min", "umd", "bundle",
                  "browser-bundle"}
EXCLUDED_PREFIX = "."
MAX_FILE_BYTES = 2_000_000


def in_scope(path: Path, repo_root: Path) -> bool:
    rel = path.relative_to(repo_root)
    for part in rel.parts[:-1]:
        if part in EXCLUDED_PARTS or (part.startswith(EXCLUDED_PREFIX) and len(part) > 1):
            return False
    name = rel.name.lower()
    if ".min." in name or ".bundle." in name:
        return False
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
    except OSError:
        return False
    return True


def source_files(repo_root: Path, ext: str):
    for path in repo_root.rglob(f"*{ext}"):
        if path.is_file() and in_scope(path, repo_root):
            yield path


def norm(name: str) -> str:
    """Normalize a type name for comparison: drop generics, qualifiers, quoting."""
    if not name:
        return ""
    prev = None
    while prev != name:
        prev = name
        name = re.sub(r'<[^<>]*>', '', name)
    return name.strip().strip('"[]()').strip().split(".")[-1].split("\\")[-1].strip()


def strip_generics(clause: str) -> str:
    prev = None
    while prev != clause:
        prev = clause
        clause = re.sub(r'<[^{};]*?>', '', clause)
    return clause


# ---------------------------------------------------------------- symbol tables

JAVA_DECL = re.compile(
    r'(?:^|\s)(class|interface|enum|record)\s+(\w+)\s*([^{;]*)\{', re.M)
PHP_DECL = re.compile(
    r'(?:^|\s)(class|interface|trait)\s+(\w+)\s*([^{]*)\{', re.M)
JS_DECL = re.compile(r'(?:^|\s)class\s+([A-Za-z_$][\w$]*)\s*(?:extends\s+([\w.$]+))?\s*\{')


def load_symbols(lang: str, repo_root: Path) -> dict:
    table: dict[str, dict] = {}

    def add(name, kind, extends, implements, file):
        entry = table.setdefault(name, {"kind": kind, "extends": set(),
                                        "implements": set(), "member_types": set(),
                                        "files": set()})
        entry["extends"] |= set(extends)
        entry["implements"] |= set(implements)
        entry["files"].add(str(file))

    if lang == "Java":
        for path in source_files(repo_root, ".java"):
            text = path.read_text(encoding="utf8", errors="ignore")
            for m in JAVA_DECL.finditer(text):
                kind, name = m.group(1), m.group(2)
                clause = strip_generics(m.group(3))
                ext, impl = set(), set()
                me = re.search(r'extends\s+([\w.<>,\s]+?)(?:\s+implements|\s*$)', clause)
                if me:
                    ext = {t.strip().split("<")[0].split(".")[-1]
                           for t in me.group(1).split(",") if t.strip()}
                mi = re.search(r'implements\s+([\w.<>,\s]+)$', clause)
                if mi:
                    impl = {t.strip().split("<")[0].split(".")[-1]
                            for t in mi.group(1).split(",") if t.strip()}
                add(name, kind, ext, impl, path)
                body = text[m.end():]
                for fm in re.finditer(
                        r'^\s*(?:(?:public|protected|private|static|final|transient|volatile)\s+)+'
                        r'([A-Z]\w*)(?:<[^>\n]*>)?(?:\[\])?\s+\w+\s*[;=]', body, re.M):
                    table[name]["member_types"].add(fm.group(1))
    elif lang == "PHP":
        for path in source_files(repo_root, ".php"):
            text = path.read_text(encoding="utf8", errors="ignore")
            for m in PHP_DECL.finditer(text):
                kind, name, clause = m.group(1), m.group(2), m.group(3)
                ext, impl = set(), set()
                me = re.search(r'extends\s+([\w\\]+)', clause)
                if me:
                    ext = {me.group(1).split("\\")[-1]}
                mi = re.search(r'implements\s+([\w\\,\s]+)', clause)
                if mi:
                    impl = {t.strip().split("\\")[-1]
                            for t in mi.group(1).split(",") if t.strip()}
                add(name, kind, ext, impl, path)
                body = text[m.end():m.end() + 60000]
                for fm in re.finditer(
                        r'(?:public|protected|private)(?:\s+static)?\s+(\??[A-Za-z_\\][\w\\]*)\s+\$\w+',
                        body):
                    table[name]["member_types"].add(fm.group(1).split("\\")[-1].lstrip("?"))
    elif lang in ("JavaScript", "TypeScript"):
        for path in source_files(repo_root, ".js"):
            _scan_js(path, table, add)
        for path in source_files(repo_root, ".jsx"):
            _scan_js(path, table, add)
        for path in source_files(repo_root, ".ts"):
            _scan_js(path, table, add)
        for path in source_files(repo_root, ".tsx"):
            _scan_js(path, table, add)
    elif lang == "Python":
        for path in source_files(repo_root, ".py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf8", errors="ignore"))
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = set()
                    for b in node.bases:
                        if isinstance(b, ast.Name):
                            bases.add(b.id)
                        elif isinstance(b, ast.Attribute):
                            bases.add(b.attr)
                    add(node.name, "class", bases, set(), path)
                    for item in node.body:
                        t = None
                        if isinstance(item, ast.AnnAssign) and item.annotation is not None:
                            t = _py_name(item.annotation)
                        elif isinstance(item, ast.Assign) and isinstance(item.value, ast.Call):
                            t = _py_name(item.value.func)
                        if t and t[:1].isupper():
                            table[node.name]["member_types"].add(t)
    for entry in table.values():
        entry["files"] = sorted(entry["files"])
    return table


def _py_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _py_name(node.value)
    return None


def _scan_js(path: Path, table: dict, add):
    text = path.read_text(encoding="utf8", errors="ignore")
    for m in JS_DECL.finditer(text):
        name, ext = m.group(1), m.group(2)
        bases = {ext.split(".")[-1]} if ext else set()
        add(name, "class", bases, set(), path)
        body = text[m.end():m.end() + 60000]
        for tm in re.finditer(r':\s*([A-Z]\w*)', body):
            table[name]["member_types"].add(tm.group(1))
        for tm in re.finditer(r'new\s+([A-Z]\w*)\s*\(', body):
            table[name]["member_types"].add(tm.group(1))


# ---------------------------------------------------------------- edge extraction

def edges_from_file(path: Path):
    text = strip_blocks(path.read_text(encoding="utf8", errors="ignore"))
    names, alias_map = declared_names(text)
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith(("'", "@", "!", "skinparam", "title", "note")):
            continue
        m = ARROW_RE.search(s)
        if not m:
            continue
        ep = endpoints(s)
        if ep is None:
            continue
        a = alias_map.get(ep[0], ep[0])
        b = alias_map.get(ep[1], ep[1])
        arrow = m.group(0)
        if arrow in INHERIT_ARROWS:
            i, j = INHERIT_ARROWS[arrow]
            pair = (a, b) if i == 0 else (b, a)
            out.append((pair[0], pair[1], "inherit", arrow))
        elif arrow in ASSOC_ARROWS:
            out.append((a, b, "assoc", arrow))
    return out, names


# ---------------------------------------------------------------- audit drivers

def audit_edges(rows, corpus, language, project, file_path, symbols, condition=None):
    edges, diagram_names = edges_from_file(file_path)
    for a, b, kind, arrow in edges:
        row = {"corpus": corpus, "language": language, "project": project,
               "file": file_path.name, "condition": condition or "",
               "source_class": a, "to_class": b, "kind": kind, "arrow": arrow}
        entry_a = symbols.get(a) or symbols.get(norm(a))
        entry_b = symbols.get(b) or symbols.get(norm(b))
        b_n, a_n = norm(b), norm(a)
        if kind == "inherit":
            if entry_a is None:
                row.update(status="unresolved_source_class", evidence="")
            elif b_n in {norm(x) for x in entry_a["extends"]} or \
                    b_n in {norm(x) for x in entry_a["implements"]}:
                row.update(status="verified",
                           evidence=f"{Path(entry_a['files'][0]).name}: extends/implements {b_n}")
            else:
                row.update(status="not_found",
                           evidence=f"clause: extends={'|'.join(sorted(entry_a['extends'])) or '-'} "
                                    f"implements={'|'.join(sorted(entry_a['implements'])) or '-'}")
        else:  # assoc
            if entry_a is None and entry_b is None:
                row.update(status="unresolved_source_class", evidence="")
            elif language in ("Java", "PHP", "TypeScript"):
                ev = []
                if entry_a and b_n in {norm(x) for x in entry_a["member_types"]}:
                    ev.append(f"{b_n} member of {a}")
                if entry_b and a_n in {norm(x) for x in entry_b["member_types"]}:
                    ev.append(f"{a_n} member of {b}")
                if ev:
                    row.update(status="verified", evidence="; ".join(ev))
                else:
                    row.update(status="not_found", evidence="no member-type evidence")
            else:
                row.update(status="not_checkable_dynamic_language", evidence="")
        rows.append(row)


def main():
    rows: list[dict] = []
    print("building symbol tables (production scope)...")
    symbols_cache: dict[tuple, dict] = {}
    cache_stats = {}

    def get_symbols(lang, project):
        key = (lang, project)
        if key not in symbols_cache:
            repo = REPOS / lang / project
            table = load_symbols(lang, repo) if repo.exists() else {}
            symbols_cache[key] = table
            cache_stats[f"{lang}/{project}"] = len(table)
            print(f"  {lang}/{project}: {len(table)} classes")
        return symbols_cache[key]

    # 1) archived cohort class diagrams (all 12 repos; mislabeled case flagged)
    for puml in sorted(ARCHIVED.glob("*/*/class/*.puml")):
        lang, project = puml.parts[-4], puml.parts[-3]
        cond = "archived-mislabeled-case" if (lang == "PHP" and project == "PHPMailer") else None
        if cond:
            edges, _ = edges_from_file(puml)
            for a, b, kind, arrow in edges:
                rows.append({"corpus": "archived", "language": lang, "project": project,
                             "file": puml.name, "condition": "excluded_mislabeled",
                             "source_class": a, "to_class": b, "kind": kind, "arrow": arrow,
                             "status": "excluded_mislabeled", "evidence": ""})
            continue
        audit_edges(rows, "archived", lang, project, puml,
                    get_symbols(lang, project))

    # 2) fresh Mindustry block class outputs
    summary = json.loads((ROOT / "results" / "mindustry_compact_block_summary.json")
                         .read_text(encoding="utf-8"))
    for run in summary["runs"]:
        if run["diagram_type"] != "class":
            continue
        out_dir = RUNS / run["run_id"] / "diagrams" / "class"
        for puml in sorted(out_dir.glob("*.puml")):
            audit_edges(rows, "mindustry-block", "Java", "Mindustry", puml,
                        get_symbols("Java", "Mindustry"), condition=run["condition"])
        for entry in symbols_cache[("Java", "Mindustry")].values():
            entry["member_types"].discard("")

    out_csv = ROOT / "results" / "relation_audit_rows.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    def rate(sub, kind):
        sel = [r for r in sub if r["kind"] == kind]
        v = sum(r["status"] == "verified" for r in sel)
        nf = sum(r["status"] == "not_found" for r in sel)
        un = sum(r["status"].startswith("unresolved") for r in sel)
        nc = sum(r["status"] == "not_checkable_dynamic_language" for r in sel)
        return {"edges": len(sel), "verified": v, "not_found": nf,
                "unresolved_target": un, "not_checkable": nc,
                "verified_rate_of_checkable": v / (v + nf) if (v + nf) else None}

    summary_out = {"symbol_tables": cache_stats, "by_corpus": {},
                   "by_language": {}}
    for corpus in ("archived", "mindustry-block"):
        sub = [r for r in rows if r["corpus"] == corpus]
        summary_out["by_corpus"][corpus] = {
            "inherit": rate(sub, "inherit"), "assoc": rate(sub, "assoc")}
    for lang in ("Java", "JavaScript", "PHP", "Python"):
        sub = [r for r in rows if r["language"] == lang and r["corpus"] == "archived"]
        if sub:
            summary_out["by_language"][lang] = {
                "inherit": rate(sub, "inherit"), "assoc": rate(sub, "assoc")}
    (ROOT / "results" / "relation_audit_summary.json").write_text(
        json.dumps(summary_out, indent=2), encoding="utf-8")

    for corpus, data in summary_out["by_corpus"].items():
        for kind in ("inherit", "assoc"):
            r = data[kind]
            vr = f"{r['verified_rate_of_checkable']*100:.1f}%" if r["verified_rate_of_checkable"] is not None else "-"
            print(f"{corpus:16s} {kind:7s} edges={r['edges']:5d} verified={r['verified']:5d} "
                  f"not_found={r['not_found']:5d} unresolved={r['unresolved_target']:4d} "
                  f"n/a={r['not_checkable']:5d}  rate={vr}")
    print(f"\nwrote {out_csv} and relation_audit_summary.json")


if __name__ == "__main__":
    main()
