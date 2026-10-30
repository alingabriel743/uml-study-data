"""
Offline evaluator for generated diagrams.

Every metric is computed from files on disk against the IR; the generated diagrams
are never used to validate themselves. Usage:

    python -m app.evaluation.evaluate --root data/<project_id>/diagrams --ir data/<project_id>/ir_postprocessed.json --out results/
    python -m app.evaluation.evaluate --root uml-study-data-main --layout study --out results/

Outputs per-file and per-batch (project × diagram type) CSV files plus a JSON summary.
"""

import argparse
import asyncio
import csv
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from app.evaluation.puml_parser import Member, parse_members, parse_puml
from app.utils.plantuml_validator import validate_puml

IR_ENTITY_KEYS = ["classes", "functions", "tables", "procedures", "types", "interfaces", "enums", "structs"]

# Diagram types whose elements are expected to name source-code entities.
# For the others (use cases, deployment nodes, external systems, activity actions)
# entity-level metrics are not applicable by construction and are reported as None.
CODE_GROUNDED_TYPES = {"class", "sequence"}

# Element kinds that never correspond to code entities
NON_CODE_KINDS = {"actor", "usecase", "database", "cloud", "queue", "node", "artifact", "person", "storage", "file"}
CLASS_KINDS = {"class", "abstract class", "abstract", "interface", "enum", "annotation",
               "entity", "record", "struct", "trait"}


def normalize(name: str) -> str:
    name = re.sub(r"<[^>]*>", "", name)          # generics
    name = name.replace("\\n", " ")
    name = re.split(r"[.:/\\]", name.strip())[-1] if re.search(r"[.:/\\]\w", name) else name
    return re.sub(r"[^a-z0-9]", "", name.lower())


def normalize_type(type_name: str) -> str:
    """Compare simple type names, ignoring generic arguments and nullable/array marks.

    Union/intersection alternatives remain separate, sorted tokens. This is a
    name-agreement measure, not type equivalence or generic-argument checking.
    """
    while re.search(r'<[^<>]*>', type_name):
        type_name = re.sub(r'<[^<>]*>', '', type_name)
    alternatives = []
    for union_part in type_name.split('|'):
        names = {normalize(t.strip().strip('?[] ')) for t in union_part.split('&') if t.strip()}
        alternatives.append('&'.join(sorted(names)))
    return '|'.join(sorted(set(alternatives)))


def ir_members(declaration: dict) -> tuple[list[Member], int]:
    """Recover member names/types from dictionary and declaration-text IR schemas."""
    members: list[Member] = []
    unparsed = 0
    for key, kind in (("attributes", "attribute"), ("properties", "attribute"), ("methods", "method")):
        for item in declaration.get(key, []) or []:
            parsed: list[Member] = []
            if isinstance(item, dict) and item.get('name'):
                name = str(item['name']).lstrip('$')
                parsed = [Member(name, kind, str(item.get('type') or '') if kind == 'attribute' else '')]
                if kind == 'attribute' and not parsed[0].type and item.get('declaration'):
                    typed = parse_members(item['declaration'])
                    parsed[0].type = next((m.type for m in typed if m.name == name), '')
            else:
                text = item.get('declaration', '') if isinstance(item, dict) else str(item)
                parsed = parse_members(text)
                if kind == 'method':
                    # Some IR views retain only method names.
                    for member in parsed:
                        member.kind = 'method'
            if parsed:
                members.extend(parsed)
            else:
                unparsed += 1
    return members, unparsed


# ── IR loading ──────────────────────────────────────────────────

@dataclass
class IRIndex:
    entities: set[str]                      # normalized entity names
    parents: dict[str, set[str]]            # normalized child -> normalized parents
    references: dict[str, set[str]]         # normalized entity -> identifiers in its attribute/method signatures
    source: str                             # path used
    is_full_ir: bool
    declarations: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def declaration_count(self) -> int:
        return sum(len(items) for items in self.declarations.values())

    @property
    def ambiguous_names(self) -> set[str]:
        return {name for name, items in self.declarations.items() if len(items) > 1}

    @classmethod
    def load(cls, path: Path) -> "IRIndex":
        ir = json.loads(path.read_text(encoding="utf-8"))
        entities: set[str] = set()
        parents: dict[str, set[str]] = {}
        references: dict[str, set[str]] = {}
        declarations: dict[str, list[dict]] = {}

        def signature_tokens(item: dict) -> set[str]:
            """Identifiers in attribute types/declarations and method parameters/return types."""
            texts = []
            for attr in item.get("attributes", []) or item.get("properties", []) or []:
                texts.append(json.dumps(attr) if isinstance(attr, dict) else str(attr))
            for m in item.get("methods", []) or []:
                if isinstance(m, dict):
                    texts += [str(m.get("parameters", "")), str(m.get("return_type", "")),
                              " ".join(m.get("referenced_types", []) or [])]
            return {normalize(t) for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", " ".join(texts))}

        def visit(items: list, collection: str):
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                raw_name = item.get("name") or item.get("class")  # sequence views key classes by "class"
                if not raw_name:
                    continue
                n = normalize(raw_name)
                if not n:
                    continue
                entities.add(n)
                declarations.setdefault(n, []).append({**item, "_collection": collection,
                                                        "source_file": item.get("source_file", "")})
                ps = set()
                for key in ("extends", "implements", "base_classes"):
                    val = item.get(key)
                    vals = val if isinstance(val, list) else [val] if val else []
                    ps.update(normalize(str(v)) for v in vals if v)
                if ps:
                    parents.setdefault(n, set()).update(ps)
                refs = signature_tokens(item)
                if refs:
                    references.setdefault(n, set()).update(refs)

        for key in IR_ENTITY_KEYS:
            visit(ir.get(key, []), key)
        # Collections that only exist in IR *views* (used when no full IR is available)
        for key in ("other_elements", "class_methods", "controllers", "services", "other_classes",
                    "orchestrators", "external_integrations", "api_boundary"):
            visit(ir.get(key, []), key)
        for refs in references.values():
            refs.intersection_update(entities)
        return cls(entities=entities, parents=parents, references=references,
                   source=str(path), is_full_ir="ir_view_" not in path.name, declarations=declarations)

    def evidences(self, a: str, b: str) -> bool:
        """True if the IR shows a structural link between entities a and b (either direction)."""
        return (b in self.parents.get(a, ()) or a in self.parents.get(b, ())
                or b in self.references.get(a, ()) or a in self.references.get(b, ()))


# ── Per-file metrics ────────────────────────────────────────────
#
# Metric definitions (as reported in the paper):
#   compile_validity                   share of final .puml files accepted by the PlantUML parser
#   compile_validity_before_correction same, for the raw generator output (before CorrectorAgent)
#   entity_recall                      |distinct normalized IR names named in a batch| / |distinct normalized IR names|
#   grounded_element_rate              share of code-level diagram elements whose name matches an IR entity
#   endpoint_grounding                 share of relationships whose BOTH endpoints are IR entities;
#                                      this bounds hallucinated endpoints but does not verify the edge
#   relationship_precision_ir          class diagrams: share of relationships confirmed by IR evidence
#                                      (inheritance, or attribute/parameter/return/body-referenced types naming the other class)
#   generalization_precision           share of generalization/realization edges confirmed by IR extends/implements
# Ambiguous names still ground elements/endpoints. Edges involving them are
# removed from BOTH numerator and denominator of relationship/generalization
# precision. Unmatched edges remain failures. Members are evaluated only for
# unambiguously matched class-like declarations. Member names are case-sensitive
# (PHP's leading $ is removed), overloads and duplicate listings collapse by name.
# Batch member ratios pool counts over (file, drawn IR class) occurrences, rather
# than averaging per-class ratios or deduplicating classes across diagram files.

@dataclass
class FileResult:
    file: str
    diagram_type: str
    compiles: Optional[bool] = None
    compile_error: str = ""
    compiles_before_correction: Optional[bool] = None
    elements: int = 0
    relationships: int = 0
    sci: Optional[float] = 0.0  # None when the activity control-flow graph could not be fully reconstructed
    parse_warnings: str = ""
    code_elements: int = 0
    grounded_elements: int = 0
    grounded_relationships: int = 0  # both endpoints are IR entities (does NOT verify the edge itself)
    evidenced_relationships: int = 0  # ... and the IR shows a structural link between them
    generalizations: int = 0
    correct_generalizations: int = 0
    ir_entities: int = 0
    ir_distinct_names: int = 0
    ir_ambiguous_names: int = 0
    ambiguous_elements: int = 0
    relationship_precision_excluded_edges: int = 0
    generalization_precision_excluded_edges: int = 0
    member_excluded_elements: int = 0
    eligible_classes: int = 0
    listed_members: int = 0
    matched_members: int = 0
    ir_members: int = 0
    captured_members: int = 0
    typed_comparisons: int = 0
    matching_attribute_types: int = 0
    unparsed_member_lines: int = 0
    ir_unparsed_member_entries: int = 0
    member_precision: Optional[float] = None
    member_recall: Optional[float] = None
    attribute_type_agreement: Optional[float] = None
    matched_entities: list[str] = field(default_factory=list)


def sci(elements: int, relationships: int) -> float:
    """Structural Complexity Index: E × log2(1 + 2R/E)."""
    if elements <= 0:
        return 0.0
    return elements * math.log2(1 + 2 * relationships / elements)


def evaluate_file(path: Path, diagram_type: str, ir: IRIndex) -> FileResult:
    diagram = parse_puml(path.read_text(encoding="utf-8", errors="replace"), diagram_type)
    res = FileResult(file=str(path), diagram_type=diagram_type,
                     elements=diagram.element_count, relationships=diagram.relationship_count,
                     ir_entities=ir.declaration_count, ir_distinct_names=len(ir.entities),
                     ir_ambiguous_names=len(ir.ambiguous_names))
    complete = getattr(diagram, "activity_graph_complete", True)
    res.sci = round(sci(res.elements, res.relationships), 3) if complete else None
    res.parse_warnings = "; ".join(getattr(diagram, "parse_warnings", []) or [])

    matched: dict[str, str] = {}  # element key -> IR entity
    ambiguous_names = ir.ambiguous_names
    for key, el in diagram.elements.items():
        if el.kind in NON_CODE_KINDS:
            continue
        res.code_elements += 1
        # Prefer the displayed declaration's name over an incidental alias. The
        # previous set iteration made conflicting aliases nondeterministic.
        for candidate in (el.name, el.qualified_name, el.label, el.alias, *sorted(el.names())):
            n = normalize(candidate)
            if n in ir.entities:
                matched[key] = n
                break
    res.grounded_elements = len(matched)
    res.ambiguous_elements = sum(n in ambiguous_names for n in matched.values())
    res.matched_entities = sorted(set(matched.values()))

    for rel in diagram.relationships:
        ambiguous_edge = any(matched.get(endpoint) in ambiguous_names for endpoint in (rel.src, rel.dst))
        if diagram_type == 'class' and ambiguous_edge:
            res.relationship_precision_excluded_edges += 1
        if rel.src in matched and rel.dst in matched:
            res.grounded_relationships += 1
            if not ambiguous_edge and ir.evidences(matched[rel.src], matched[rel.dst]):
                res.evidenced_relationships += 1
        if rel.is_generalization:
            res.generalizations += 1
            child, parent = (rel.dst, rel.src) if rel.arrow.startswith("<|") else (rel.src, rel.dst)
            # The child must be a project entity; the parent may be a library type (Serializable, HttpServlet)
            if parent in matched:
                parent_names = {matched[parent]}
            else:
                parent_names = ({normalize(n) for n in diagram.elements[parent].names()}
                                if parent in diagram.elements else {normalize(parent)})
            if ambiguous_edge or parent_names & ambiguous_names:
                res.generalization_precision_excluded_edges += 1
            elif child in matched and parent_names & ir.parents.get(matched[child], set()):
                res.correct_generalizations += 1
    if diagram_type == 'class':
        # Merge duplicate aliases for a class within one file. The same class in
        # another file remains a separate conditional-coverage observation.
        drawn: dict[str, list[Member]] = {}
        for key, element in diagram.elements.items():
            if element.kind not in CLASS_KINDS:
                continue
            res.unparsed_member_lines += len(element.unparsed_members)
            name = matched.get(key)
            if name in ambiguous_names:
                res.member_excluded_elements += 1
                continue
            declarations = ir.declarations.get(name, [])
            if len(declarations) != 1:
                continue
            declaration = declarations[0]
            kind = declaration.get('kind', declaration.get('_collection', ''))
            if kind not in CLASS_KINDS and not (
                    'kind' not in declaration and declaration.get('_collection') in
                    {'classes', 'interfaces', 'enums', 'structs', 'types'}):
                continue
            drawn.setdefault(name, []).extend(element.members)
        for name, members in drawn.items():
            reference, unparsed = ir_members(ir.declarations[name][0])
            res.ir_unparsed_member_entries += unparsed
            res.eligible_classes += 1
            expected_names = {m.name for m in reference}
            listed_names = {m.name for m in members}
            captured = len(expected_names & listed_names)
            res.listed_members += len(listed_names)
            res.matched_members += captured
            res.ir_members += len(expected_names)
            res.captured_members += captured
            reference_types = {m.name: normalize_type(m.type) for m in reference
                               if m.kind == 'attribute' and m.type and normalize_type(m.type)}
            typed = {(m.name, normalize_type(m.type)) for m in members
                     if m.kind == 'attribute' and m.type and normalize_type(m.type)
                     and m.name in reference_types}
            res.typed_comparisons += len(typed)
            res.matching_attribute_types += sum(reference_types[name] == type_name for name, type_name in typed)
        res.member_precision = _ratio(res.matched_members, res.listed_members)
        res.member_recall = _ratio(res.captured_members, res.ir_members)
        res.attribute_type_agreement = _ratio(res.matching_attribute_types, res.typed_comparisons)
    return res


# ── Batch aggregation ───────────────────────────────────────────

def _ratio(num: float, den: float) -> Optional[float]:
    return round(num / den, 4) if den else None


def aggregate(files: list[FileResult], ir: IRIndex, diagram_type: str) -> dict:
    n = len(files)
    applicable = diagram_type in CODE_GROUNDED_TYPES
    compiled = [f for f in files if f.compiles is not None]
    union = set().union(*(f.matched_entities for f in files)) if files else set()
    total_rels = sum(f.relationships for f in files)  # explicit edges only for code-grounded types
    return {
        "files": n,
        "compile_validity": _ratio(sum(f.compiles for f in compiled), len(compiled)),
        "compile_validity_before_correction": _ratio(
            sum(f.compiles_before_correction for f in files if f.compiles_before_correction is not None),
            sum(1 for f in files if f.compiles_before_correction is not None)),
        "ir_entities": ir.declaration_count,
        "ir_distinct_names": len(ir.entities),
        "ir_ambiguous_names": len(ir.ambiguous_names),
        "ir_source_is_full_ir": ir.is_full_ir,
        "entity_recall": _ratio(len(union), len(ir.entities)) if applicable else None,
        "grounded_element_rate": _ratio(sum(f.grounded_elements for f in files), sum(f.code_elements for f in files)) if applicable else None,
        "endpoint_grounding": _ratio(sum(f.grounded_relationships for f in files), total_rels) if applicable else None,
        # Class diagrams only: edges whose link is confirmed by IR inheritance or signature types
        "relationship_precision_ir": _ratio(sum(f.evidenced_relationships for f in files), total_rels - sum(f.relationship_precision_excluded_edges for f in files)) if diagram_type == "class" else None,
        "generalization_precision": _ratio(sum(f.correct_generalizations for f in files), sum(f.generalizations - f.generalization_precision_excluded_edges for f in files)) if diagram_type == "class" else None,
        **{key: sum(getattr(f, key) for f in files) for key in (
            "ambiguous_elements", "relationship_precision_excluded_edges", "generalization_precision_excluded_edges",
            "member_excluded_elements", "eligible_classes", "listed_members", "matched_members", "ir_members",
            "captured_members", "typed_comparisons", "matching_attribute_types", "unparsed_member_lines",
            "ir_unparsed_member_entries")},
        "member_precision": _ratio(sum(f.matched_members for f in files), sum(f.listed_members for f in files)) if diagram_type == "class" else None,
        "member_recall": _ratio(sum(f.captured_members for f in files), sum(f.ir_members for f in files)) if diagram_type == "class" else None,
        "attribute_type_agreement": _ratio(sum(f.matching_attribute_types for f in files), sum(f.typed_comparisons for f in files)) if diagram_type == "class" else None,
        "mean_elements": round(sum(f.elements for f in files) / n, 2) if n else None,
        "mean_relationships": round(sum(f.relationships for f in files) / n, 2) if n else None,
        "mean_sci": round(sum(scis) / len(scis), 2) if (scis := [f.sci for f in files if f.sci is not None]) else None,
        "sci_files_excluded": sum(1 for f in files if f.sci is None),
    }


# ── Discovery ───────────────────────────────────────────────────

def discover_batches(root: Path, layout: str, ir_override: Optional[Path]) -> list[dict]:
    """Return [{language, project, diagram_type, dir, ir_path}] for every folder with .puml files."""
    batches = []
    skip = {"_original", "_previous_runs"}
    for d in sorted({p.parent for p in root.rglob("*.puml") if not skip.intersection(p.parts)}):
        diagram_type = d.name
        rel = d.relative_to(root).parts
        language, project = (rel[0], rel[1]) if layout == "study" and len(rel) >= 3 else ("", root.parent.name)
        ir_path = ir_override
        if ir_path is None:
            for cand in [d.parent.parent / "ir_postprocessed.json", d.parent / "ir_postprocessed.json", d / f"ir_view_{diagram_type}.json"]:
                if cand.exists():
                    ir_path = cand
                    break
        batches.append({"language": language, "project": project, "diagram_type": diagram_type, "dir": d, "ir_path": ir_path})
    return batches


def _manifest_validity(files: list[FileResult]) -> dict[str, bool]:
    """valid_before per file from run_manifest.json files next to the diagrams (if any)."""
    before: dict[str, bool] = {}
    for d in {Path(f.file).parent for f in files}:
        manifest = d / "run_manifest.json"
        if manifest.exists():
            for c in json.loads(manifest.read_text()).get("corrections", []):
                if c.get("file") and c.get("valid_before") is not None:
                    before[str(Path(c["file"]).resolve())] = c["valid_before"]
    return before


async def _validate_all(files: list[FileResult], concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)
    manifest_before = _manifest_validity(files)

    async def one(fr: FileResult):
        async with sem:
            path = Path(fr.file)
            result = await validate_puml(path)
            fr.compiles, fr.compile_error = result.valid, "" if result.valid else result.message
            # Pre-correction validity: the manifest is authoritative; otherwise a preserved
            # original means the raw output was corrected, and an untouched file IS the raw output.
            manifest_entry = manifest_before.get(str(path.resolve()))
            original = path.parent / "_original" / path.name
            if manifest_entry is not None:
                fr.compiles_before_correction = manifest_entry
            elif original.exists():
                fr.compiles_before_correction = (await validate_puml(original)).valid
            else:
                fr.compiles_before_correction = fr.compiles

    await asyncio.gather(*(one(f) for f in files))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--layout", choices=["study", "data"], default="data",
                    help="study = <lang>/<project>/<type>/ ; data = data/<id>/diagrams/<type>/")
    ap.add_argument("--ir", type=Path, default=None, help="IR file to use for all batches (overrides discovery)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-validate", action="store_true", help="skip PlantUML compilation")
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    batches = discover_batches(args.root, args.layout, args.ir)
    all_files: list[FileResult] = []
    per_batch = []
    for b in batches:
        if b["ir_path"] is None:
            print(f"!! no IR found for {b['dir']} — skipped")
            continue
        ir = IRIndex.load(b["ir_path"])
        files = [evaluate_file(p, b["diagram_type"], ir) for p in sorted(b["dir"].glob("*.puml"))]
        per_batch.append((b, ir, files))
        all_files.extend(files)

    if not args.no_validate:
        asyncio.run(_validate_all(all_files, args.concurrency))

    args.out.mkdir(parents=True, exist_ok=True)
    if not all_files:  # e.g. a failed run that produced no diagrams: record it, don't crash
        (args.out / "summary.json").write_text(json.dumps({"batches": 0, "files": 0, "note": "no .puml files"}, indent=2))
        print(f"No diagrams under {args.root} — nothing to evaluate")
        return
    batch_rows = []
    for b, ir, files in per_batch:
        row = {"language": b["language"], "project": b["project"], "diagram_type": b["diagram_type"], "ir_path": str(b["ir_path"])}
        row.update(aggregate(files, ir, b["diagram_type"]))
        batch_rows.append(row)

    with (args.out / "batches.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(batch_rows[0].keys()))
        w.writeheader()
        w.writerows(batch_rows)
    with (args.out / "files.csv").open("w", newline="") as f:
        rows = [{k: v for k, v in asdict(fr).items() if k != "matched_entities"} for fr in all_files]
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (args.out / "summary.json").write_text(json.dumps({"batches": len(batch_rows), "files": len(all_files)}, indent=2))
    print(f"Evaluated {len(all_files)} files in {len(batch_rows)} batches → {args.out}")


if __name__ == "__main__":
    main()
