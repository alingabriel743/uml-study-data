"""Deterministic, importance-ranked IR projections with enforced UTF-8 budgets.

Selection and projection require no LLM calls. Runtime telemetry is the only
non-deterministic content: compaction_ms measures loading through compaction,
excluding the final serialization and disk write.
"""

import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# The SDK Read tool rejects files above ~25K tokens; JSON averages ~3.5-4 bytes/token,
# so 60 KB (~15-17K tokens) leaves headroom. Both paths use the same budget by default.
SINGLE_VIEW_BYTES = 60_000
DEEP_VIEW_BYTES = 60_000
# Preserve the former module-level default for external callers.
TARGET_VIEW_BYTES = SINGLE_VIEW_BYTES
DEEP_TYPES = {"class", "sequence", "activity", "use_case"}
MIN_VIEW_BYTES = 1_024  # Enough room for mandatory telemetry, even in an empty view.

IMPORTANT_NAMES = ("service", "controller", "manager", "repository", "handler",
                   "orchestrator", "model", "entity")
NOISE_NAMES = ("test", "mock", "util", "helper", "exception")
API_NAMES = ("controller", "handler", "router", "api", "endpoint", "route", "view")
SERVICE_NAMES = ("service", "manager", "usecase", "interactor")
WORKFLOW_NAMES = ("orchestrator", "pipeline", "workflow", "processor", "handler",
                  "controller", "main", "service", "manager", "executor")
EXTERNAL_NAMES = ("client", "gateway", "proxy", "connector", "adapter", "external",
                  "api", "http", "rest", "grpc", "kafka", "queue", "email", "notification")
INFRA_NAMES = ("docker", "compose", "kubernetes", "k8s", "deploy", "terraform",
               "cloudformation", "helm", "nginx", "config", "makefile", "ci", "pipeline")

# Primary entries count against max_elements across collections, rather than
# receiving an independent quota per collection. Nested members are detail.
ENTRY_KEYS = {
    "class": ("classes", "other_elements"),
    "sequence": ("functions", "class_methods"),
    "use_case": ("controllers", "services", "other_classes", "functions"),
    "activity": ("orchestrators", "services", "other_classes", "functions"),
    "component": ("packages",),
    "deployment": ("infrastructure_files", "services"),
    "system_context": ("all_classes_summary",),
}


def generate_ir_view(
    ir_path: Path,
    diagram_type: str,
    output_dir: Path,
    max_elements: int = 60,
    budget_bytes: int | None = None,
    rank: bool = True,
) -> Path:
    """Write a view no larger than budget_bytes, including all metadata.

    Existing four-argument calls remain valid. Defaults are 100,000 bytes for
    DEEP types and 60,000 for SINGLE/unknown types. Budgets below 1,024 bytes
    are rejected because mandatory metadata itself needs space. Unknown types
    retain the historical class projection fallback.
    """
    started = time.perf_counter()
    if budget_bytes is None:
        budget_bytes = DEEP_VIEW_BYTES if diagram_type in DEEP_TYPES else SINGLE_VIEW_BYTES
    if budget_bytes < MIN_VIEW_BYTES:
        raise ValueError(f"budget_bytes must be at least {MIN_VIEW_BYTES}")
    if max_elements < 0:
        raise ValueError("max_elements must be non-negative")

    with ir_path.open("r", encoding="utf-8") as f:
        ir = json.load(f)

    view_generators = {
        "class": _class_view,
        "sequence": _sequence_view,
        "use_case": _use_case_view,
        "activity": _activity_view,
        "component": _component_view,
        "deployment": _deployment_view,
        "system_context": _system_context_view,
    }
    projection_type = diagram_type if diagram_type in view_generators else "class"
    # rank=False is the prefix-selection ablation: same projection and budget, IR order instead of importance
    ranked = _ranked_ir(ir, projection_type) if rank else _unranked_ir(ir)
    generator = view_generators[projection_type]
    metadata = ir.get("metadata", {})
    total = len(ir.get("classes", [])) + len(ir.get("functions", []))
    current_max = max_elements
    iterations = 0

    def attach_metadata(view: dict, minimal: bool = False) -> None:
        view["_metadata"] = {
            "diagram_type": diagram_type,
            "budget_bytes": budget_bytes,
            "final_bytes": 0,
            "final_max_elements": current_max,
            "iterations": iterations,
            "elements_total_in_ir": total,
            "elements_retained": _retained_count(view, projection_type),
            "compaction_ms": 0.0,
        }
        if not minimal:
            view["_metadata"].update({
                "source_ir": str(ir_path),
                "project_name": metadata.get("projectName", ""),
                "source_languages": sorted(metadata.get("sourceLanguages", [])),
                "source_files_count": len(metadata.get("sourceFiles", [])),
                "source_files_sample": ranked["_source_files"][:min(10, current_max)],
            })

    # Reserve space for the measured duration; its magnitude must not affect
    # selection. Halving continues to one entry, with no attempt cap.
    timing_reserve = 32
    while True:
        iterations += 1
        view = generator(ranked, current_max, budget_bytes)
        attach_metadata(view)
        serialized = _serialize(view)
        if len(serialized) + timing_reserve <= budget_bytes:
            break
        if current_max <= 1:
            # Unbounded names, parameter types, paths or metadata can exceed
            # the budget even with one entry. Strip details and optional
            # metadata, then discard the remaining lowest-ranked entry.
            iterations += 1
            view = _names_only(view, projection_type)
            attach_metadata(view, minimal=True)
            serialized = _serialize(view)
            if len(serialized) + timing_reserve > budget_bytes:
                current_max = 0
                iterations += 1
                view = {key: [] for key in ENTRY_KEYS[projection_type]}
                attach_metadata(view, minimal=True)
                serialized = _serialize(view)
            break
        current_max //= 2

    view["_metadata"]["compaction_ms"] = round((time.perf_counter() - started) * 1_000, 3)
    serialized = _serialize(view)
    assert len(serialized) <= budget_bytes, "IR view exceeded its byte budget"
    output_dir.mkdir(parents=True, exist_ok=True)
    view_path = output_dir / f"ir_view_{diagram_type}.json"
    view_path.write_bytes(serialized)
    assert view_path.stat().st_size <= budget_bytes, "Written IR view exceeded its byte budget"
    logger.info(
        f"IR view: {view_path} ({len(serialized):,}/{budget_bytes:,} bytes, "
        f"max_elements={current_max}, {iterations} iterations, "
        f"{view['_metadata']['compaction_ms']} ms)"
    )
    return view_path


# ── Ranking and compaction helpers ───────────────────────────────

def _serialize(view: dict) -> bytes:
    """Include the byte count's own digits in the reported file size.

    Pad the duration's JSON number with legal whitespace so runtime variation
    changes neither final_bytes nor the selected elements near a byte boundary.
    """
    while True:
        duration = json.dumps(view["_metadata"]["compaction_ms"])
        serialized = json.dumps(view, ensure_ascii=False, sort_keys=True).replace(
            f'"compaction_ms": {duration}', f'"compaction_ms": {duration:>32}', 1,
        ).encode("utf-8")
        if view["_metadata"]["final_bytes"] == len(serialized):
            return serialized
        view["_metadata"]["final_bytes"] = len(serialized)


def _retained_count(view: dict, diagram_type: str) -> int:
    """Classes/functions, packages, or infrastructure files/directories retained."""
    return sum(len(view.get(key, [])) for key in ENTRY_KEYS[diagram_type])


def _names_only(view: dict, diagram_type: str) -> dict:
    compact = {}
    for key in ENTRY_KEYS[diagram_type]:
        compact[key] = []
        for entry in view.get(key, []):
            if isinstance(entry, dict):
                compact[key].append({k: entry[k] for k in ("name", "class", "package", "directory") if k in entry})
            else:
                compact[key].append(entry)
    return compact


def _matches(name: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in name.lower() for keyword in keywords)


def _name_score(name: str) -> int:
    return 12 * _matches(name, IMPORTANT_NAMES) - 20 * _matches(name, NOISE_NAMES)


def _dto_only(cls: dict) -> bool:
    """DTO suffix plus no methods except constructors/accessors."""
    name = cls.get("name", "").lower()
    return name.endswith("dto") and all(
        _item_name(method).lower().startswith(("get", "set", "is"))
        or _item_name(method).lower() in ("__init__", "__new__", "constructor", name)
        for method in cls.get("methods", [])
    )


def _call_count(element: dict) -> int:
    """Count available call hints, not inferred calls or verified call edges.

    Current JS extractors emit references (calls plus variable references);
    SQL also emits references. Other extractors often provide signatures only.
    Accept calls/invocations from enriched IRs without requiring those fields.
    """
    return sum(
        len(value) if isinstance(value, (list, tuple, dict)) else int(bool(value))
        for key in ("calls", "invocations", "references")
        if (value := element.get(key)) is not None
    )


def _parent_names(cls: dict) -> set[str]:
    names = set()
    for key in ("extends", "implements", "base_classes"):
        values = cls.get(key, [])
        if not isinstance(values, list):
            values = [values]
        for value in values:
            name = _item_name(value)
            # Normalize qualified and generic inheritance references to the
            # same simple identifiers used by the extractors for class names.
            name = re.split(r"[<\[(]", name)[0].strip().split()
            if name:
                names.add(re.split(r"[.:/\\]", name[-1])[-1])
    return names


def _class_score(cls: dict, in_degree: Counter, diagram_type: str) -> int:
    """C = 2M + A + 8H + 6D + 12P - 20N - 15T.

    Behavioral types add 3K (class and method call hints); use_case adds
    24 for API/service roles, activity adds 24 for workflow/service roles,
    and system_context adds 24 for external/API roles. Bonuses are binary.
    """
    name = cls.get("name", "")
    methods = cls.get("methods", [])
    score = (2 * len(methods) + len(cls.get("attributes", []))
             + 8 * any(cls.get(k) for k in ("extends", "implements", "base_classes"))
             + 6 * in_degree[_simple_name(name)] + _name_score(name) - 15 * _dto_only(cls))
    if diagram_type in ("sequence", "use_case", "activity"):
        score += 3 * (_call_count(cls) + sum(_call_count(m) for m in methods if isinstance(m, dict)))
    roles = {"use_case": API_NAMES + SERVICE_NAMES, "activity": WORKFLOW_NAMES,
             "system_context": EXTERNAL_NAMES + API_NAMES}
    if diagram_type in roles:
        score += 24 * _matches(name, roles[diagram_type])
    return score


def _function_score(fn: dict, diagram_type: str) -> int:
    score = len(fn.get("parameters", [])) + _name_score(fn.get("name", "")) + 3 * _call_count(fn)
    if diagram_type == "use_case":
        score += 24 * _matches(fn.get("name", ""), API_NAMES + SERVICE_NAMES)
    elif diagram_type == "activity":
        score += 24 * _matches(fn.get("name", ""), WORKFLOW_NAMES)
    return score


def _simple_name(name: str) -> str:
    return re.split(r"[.:/\\]", name)[-1]


def _rank(items: list[dict], score: Callable[[dict], int]) -> list[dict]:
    # Name and full source path break score ties before any shortening.
    return sorted(items, key=lambda item: (-score(item), item.get("name", ""), item.get("source_file", "")))


def _ranked_ir(ir: dict, diagram_type: str) -> dict:
    """Rank once per generation, reusing the order during every shrink step."""
    if ir.get("_ranked_for") == diagram_type or ir.get("_unranked"):
        return ir
    in_degree: Counter = Counter()
    for cls in ir.get("classes", []):
        in_degree.update(_parent_names(cls))
    ranked = dict(ir)
    ranked["_ranked_for"] = diagram_type
    ranked["classes"] = _rank(ir.get("classes", []), lambda cls: _class_score(cls, in_degree, diagram_type))
    ranked["functions"] = _rank(ir.get("functions", []), lambda fn: _function_score(fn, diagram_type))
    candidates = [("class", cls, _class_score(cls, in_degree, diagram_type)) for cls in ranked["classes"]]
    candidates += [("function", fn, _function_score(fn, diagram_type)) for fn in ranked["functions"]]
    ranked["_candidates"] = sorted(candidates, key=lambda item: (
        -item[2], item[1].get("name", ""), item[1].get("source_file", ""), item[0],
    ))
    ranked["_source_files"] = sorted(ir.get("metadata", {}).get("sourceFiles", []),
                                     key=lambda path: (-_infra_score(path), path))
    ranked["_in_degree"] = in_degree
    return ranked


def _unranked_ir(ir: dict) -> dict:
    """Same structure as _ranked_ir but every score is zero, so selection keeps IR (file) order."""
    unranked = dict(ir)
    unranked["_ranked_for"] = "__none__"
    unranked["_unranked"] = True  # generators must not re-rank or score (prefix ablation)
    unranked["classes"] = list(ir.get("classes", []))
    unranked["functions"] = list(ir.get("functions", []))
    unranked["_candidates"] = ([("class", c, 0) for c in unranked["classes"]]
                               + [("function", f, 0) for f in unranked["functions"]])
    unranked["_source_files"] = list(ir.get("metadata", {}).get("sourceFiles", []))
    unranked["_in_degree"] = Counter()
    return unranked


def _detail_limit(ir: dict, budget_bytes: int) -> int:
    """20/15/10/5 members at effective IR counts <=10/20/40/>40.

    Effective count = (classes + functions) * 100,000 / byte budget. It uses
    original project size, so shrinking a large project cannot increase detail.
    """
    effective = (len(ir.get("classes", [])) + len(ir.get("functions", []))) * DEEP_VIEW_BYTES / budget_bytes
    return 20 if effective <= 10 else 15 if effective <= 20 else 10 if effective <= 40 else 5


def _short_path(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    return "/".join(parts[-3:]) if len(parts) > 3 else path


def _item_name(item: Any) -> str:
    return str(item.get("name", item.get("type", ""))) if isinstance(item, dict) else str(item)


def _extract_names(items: list, limit: int = 15) -> list[str]:
    return [_item_name(item)[:60] for item in items[:limit]]


def _extract_method_signatures(methods: list, limit: int = 20, behavioral: bool = False) -> list[dict]:
    def importance(method: dict) -> int:
        return _function_score(method, "sequence") if behavioral else _name_score(method.get("name", ""))

    ordered = _rank([m if isinstance(m, dict) else {"name": str(m)} for m in methods], importance)
    signatures = []
    for method in ordered[:limit]:
        signature: dict[str, Any] = {"name": method.get("name", "")}
        if method.get("parameters"):
            signature["parameters"] = method["parameters"][:8]
        for key in ("return_type", "visibility"):
            if method.get(key):
                signature[key] = method[key]
        if behavioral:
            for key in ("calls", "invocations", "references"):
                if method.get(key):
                    value = method[key]
                    signature[key] = value[:limit] if isinstance(value, list) else value
        signatures.append(signature)
    return signatures


def _extract_declarations(items: list, limit: int = 15) -> list[str]:
    ordered = sorted(items, key=lambda item: (_item_name(item), json.dumps(item, sort_keys=True)))
    return [str(item.get("declaration", item.get("name", "")))[:80]
            if isinstance(item, dict) else str(item)[:80] for item in ordered[:limit]]


def _compact_class(cls: dict, limit: int, behavioral: bool = False) -> dict:
    compact = {"name": cls.get("name", ""), "kind": cls.get("kind", "class"),
               "source_file": cls.get("source_file", "")}
    if cls.get("methods"):
        compact["methods"] = _extract_method_signatures(cls["methods"], limit, behavioral)
    return compact


def _compact_function(fn: dict, limit: int) -> dict:
    compact = _extract_method_signatures([fn], limit, behavioral=True)[0]
    compact["source_file"] = _short_path(fn.get("source_file", ""))
    return compact


# ── Diagram-specific projections ─────────────────────────────────

def _class_view(ir: dict, max_elements: int, budget_bytes: int = DEEP_VIEW_BYTES) -> dict:
    ir = _ranked_ir(ir, "class")
    limit = _detail_limit(ir, budget_bytes)
    classes, other = [], []
    for cls in ir.get("classes", [])[:max_elements]:
        compact = _compact_class(cls, limit)
        if cls.get("attributes"):
            compact["attributes"] = _extract_declarations(cls["attributes"], limit)
        for key in ("extends", "implements", "base_classes"):
            if cls.get(key):
                compact[key] = cls[key]
        target = classes if compact["kind"] in ("class", "interface", "abstract", "enum", "struct", "trait", "record", "annotation") else other
        target.append(compact)
    return {"classes": classes, "other_elements": other,
            "element_counts": {"classes": len(classes), "other": len(other), "total_in_ir": len(ir.get("classes", []))}}


def _sequence_view(ir: dict, max_elements: int, budget_bytes: int = DEEP_VIEW_BYTES) -> dict:
    ir = _ranked_ir(ir, "sequence")
    limit = _detail_limit(ir, budget_bytes)
    functions, class_methods = [], []
    file_modules: dict[str, list[str]] = {}
    for kind, element, _ in ir["_candidates"][:max_elements]:
        if kind == "function":
            functions.append(_compact_function(element, limit))
        else:
            compact = _compact_class(element, limit, behavioral=True)
            compact["class"] = compact.pop("name")
            class_methods.append(compact)
            file_modules.setdefault(_short_path(element.get("source_file", "")), []).append(element.get("name", ""))
    return {"file_modules": file_modules, "functions": functions, "class_methods": class_methods,
            "element_counts": {"modules": len(file_modules), "functions": len(functions), "classes_with_methods": len(class_methods)}}


def _behavioral_view(ir: dict, max_elements: int, budget_bytes: int, diagram_type: str) -> dict:
    ir = _ranked_ir(ir, diagram_type)
    limit = _detail_limit(ir, budget_bytes)
    primary = "controllers" if diagram_type == "use_case" else "orchestrators"
    roles = API_NAMES if diagram_type == "use_case" else WORKFLOW_NAMES[:7]
    view: dict[str, Any] = {primary: [], "services": [], "other_classes": [], "functions": []}
    for kind, element, _ in ir["_candidates"][:max_elements]:
        if kind == "function":
            view["functions"].append(_compact_function(element, limit))
        else:
            name = element.get("name", "")
            key = primary if _matches(name, roles) else "services" if _matches(name, SERVICE_NAMES + ("executor",)) else "other_classes"
            view[key].append(_compact_class(element, limit, behavioral=True))
    view["element_counts"] = {key: len(entries) for key, entries in view.items()}
    view["element_counts"]["total"] = len(ir.get("classes", [])) + len(ir.get("functions", []))
    return view


def _use_case_view(ir: dict, max_elements: int, budget_bytes: int = DEEP_VIEW_BYTES) -> dict:
    return _behavioral_view(ir, max_elements, budget_bytes, "use_case")


def _activity_view(ir: dict, max_elements: int, budget_bytes: int = DEEP_VIEW_BYTES) -> dict:
    return _behavioral_view(ir, max_elements, budget_bytes, "activity")


def _component_view(ir: dict, max_elements: int, budget_bytes: int = SINGLE_VIEW_BYTES) -> dict:
    """Package score = sum(max(0, C)) over its members; members ranked by C."""
    ir = _ranked_ir(ir, "component")
    packages: dict[str, list[dict]] = {}
    for cls in ir.get("classes", []):
        source = cls.get("source_file", "").replace("\\", "/")
        package = source.rsplit("/", 1)[0] if "/" in source else "root"
        packages.setdefault(package, []).append(cls)
    if ir.get("_unranked"):
        ordered = list(packages)  # first-seen (IR) order
    else:
        ordered = sorted(packages, key=lambda package: (
            -sum(max(0, _class_score(cls, ir["_in_degree"], "component")) for cls in packages[package]), package,
        ))
    member_limit = min(max_elements, _detail_limit(ir, budget_bytes))
    return {"packages": [{"package": package, "classes": _extract_names(packages[package], member_limit),
                          "total_classes": len(packages[package])} for package in ordered[:max_elements]],
            "source_files_sample": ir["_source_files"][:min(30, max_elements)],
            "element_counts": {"packages": len(packages), "total_classes": len(ir.get("classes", []))}}


def _infra_score(path: str) -> int:
    """Ten points per distinct infrastructure keyword present in the path."""
    return 10 * sum(keyword in path.lower() for keyword in INFRA_NAMES)


def _deployment_view(ir: dict, max_elements: int, budget_bytes: int = SINGLE_VIEW_BYTES) -> dict:
    """Files score by infra hints; directories by file count plus hint scores."""
    ir = _ranked_ir(ir, "deployment")
    files = ir["_source_files"]
    directories: Counter = Counter()
    hints: Counter = Counter()
    candidates = []
    for path in files:
        score = _infra_score(path)
        if score:
            candidates.append((score, path, "file"))
        parts = path.replace("\\", "/").split("/")
        if len(parts) > 1:
            directories[parts[0]] += 1
            hints[parts[0]] += score
    candidates += [(count + hints[directory], directory, "directory") for directory, count in directories.items()]
    if ir.get("_unranked"):
        selected = candidates[:max_elements]  # IR order: files first as listed, then directories
    else:
        selected = sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))[:max_elements]
    return {"infrastructure_files": [name for _, name, kind in selected if kind == "file"],
            "services": [{"directory": name, "file_count": directories[name]} for _, name, kind in selected if kind == "directory"],
            "languages": sorted(ir.get("metadata", {}).get("sourceLanguages", [])), "total_source_files": len(files),
            "element_counts": {"infra_files": sum(bool(_infra_score(path)) for path in files), "service_dirs": len(directories)}}


def _system_context_view(ir: dict, max_elements: int, budget_bytes: int = SINGLE_VIEW_BYTES) -> dict:
    ir = _ranked_ir(ir, "system_context")
    limit = _detail_limit(ir, budget_bytes)
    selected = ir.get("classes", [])[:max_elements]
    external, api = [], []
    for cls in selected:
        if _matches(cls.get("name", ""), EXTERNAL_NAMES):
            external.append(_compact_class(cls, limit))
        elif _matches(cls.get("name", ""), API_NAMES):
            api.append(_compact_class(cls, limit))
    return {"external_integrations": external, "api_boundary": api,
            "languages": sorted(ir.get("metadata", {}).get("sourceLanguages", [])),
            "project_name": ir.get("metadata", {}).get("projectName", ""),
            "all_classes_summary": [{"name": cls.get("name", ""), "kind": cls.get("kind", "class")} for cls in selected],
            "element_counts": {"external": len(external), "api_classes": len(api), "total_classes": len(ir.get("classes", []))}}
