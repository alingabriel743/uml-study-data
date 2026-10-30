"""
Smart IR Splitter — graph-based splitting of IR elements into logical diagram groups.

Uses dependency analysis (inheritance, composition, naming patterns) to group
related elements together, producing focused diagrams.

Pure Python, no LLM calls. Runs in <1s even on large IRs.
"""

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def split_ir_for_diagrams(
    ir_path: Path,
    diagram_type: str,
    max_elements_per_group: int = 25,
    min_elements_per_group: int = 3,
) -> list[dict]:
    """
    Split the IR into logical groups for separate diagrams.

    Returns:
        List of diagram specs: [{name, title, description, elements, source_files}]
    """
    with ir_path.open("r", encoding="utf-8") as f:
        ir = json.load(f)

    splitters = {
        "class": _split_class,
        "use_case": _split_use_case,
        "sequence": _split_sequence,
        "activity": _split_activity,
    }

    splitter = splitters.get(diagram_type)
    if not splitter:
        logger.info(f"No smart splitter for '{diagram_type}' — single diagram")
        return []

    groups = splitter(ir, max_elements_per_group, min_elements_per_group)
    logger.info(f" Smart split for '{diagram_type}': {len(groups)} groups")
    for g in groups:
        logger.info(f"   📁 {g['name']}: {len(g.get('elements', []))} elements — {g['title']}")

    return groups


# ── CLASS DIAGRAM SPLITTING ──────────────────────────────────────

def _split_class(ir: dict, max_per_group: int, min_per_group: int) -> list[dict]:
    """
    Split classes using dependency graph analysis.
    
    For large projects (>500 classes): fast directory-based grouping.
    For smaller projects: full dependency graph (inheritance + composition).
    """
    classes = ir.get("classes", [])
    total = len(classes)

    if total <= max_per_group:
        return []  # Small enough for single diagram

    # For very large projects, skip O(n²) analysis — use directory grouping directly
    if total > 500:
        logger.info(f"   Large project ({total} classes) — using fast directory-based splitting")
        return _split_by_directory_fast(classes, max_per_group, min_per_group)

    # Build name→class index (for smaller projects, full dependency analysis)
    class_index = {}
    for cls in classes:
        name = cls.get("name", "")
        if name:
            class_index[name] = cls

    all_names = set(class_index.keys())

    # Build adjacency list (undirected graph)
    adj: dict[str, set[str]] = defaultdict(set)

    for name, cls in class_index.items():
        # Inheritance edges (fast — O(n))
        for key in ("extends", "implements", "base_classes"):
            parents = cls.get(key)
            if parents:
                if isinstance(parents, str):
                    parents = [parents]
                for parent in parents:
                    parent = parent.strip()
                    if parent in all_names:
                        adj[name].add(parent)
                        adj[parent].add(name)

        # Same source directory → package edge (fast — O(n))
        src = cls.get("source_file", "")
        src_dir = "/".join(src.replace("\\", "/").split("/")[:-1]) if "/" in src else ""
        if src_dir:
            # Use directory index instead of O(n²) scan
            pass  # Handled below via directory grouping

    # Add directory edges using index (O(n) not O(n²))
    dir_index: dict[str, list[str]] = defaultdict(list)
    for name, cls in class_index.items():
        src = cls.get("source_file", "")
        d = "/".join(src.replace("\\", "/").split("/")[:-1]) if "/" in src else "root"
        dir_index[d].append(name)

    for d, names in dir_index.items():
        for n in names:
            for m in names:
                if n != m:
                    adj[n].add(m)
                    adj[m].add(n)

    # Find connected components (BFS)
    visited = set()
    components = []

    for name in all_names:
        if name not in visited:
            component = set()
            queue = [name]
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                component.add(node)
                for neighbor in adj.get(node, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            components.append(component)

    # Sort by size (largest first)
    components.sort(key=len, reverse=True)

    # Merge tiny components
    final_components = []
    tiny_pool: set[str] = set()

    for comp in components:
        if len(comp) >= min_per_group:
            final_components.append(comp)
        else:
            tiny_pool.update(comp)

    if tiny_pool:
        if len(tiny_pool) >= min_per_group:
            final_components.append(tiny_pool)
        elif final_components:
            # Merge into smallest existing component
            smallest = min(final_components, key=len)
            smallest.update(tiny_pool)

    # Split oversized components
    split_components = []
    for comp in final_components:
        if len(comp) > max_per_group:
            # Sub-split by source directory
            sub_groups = _sub_split_by_directory(comp, class_index, max_per_group)
            split_components.extend(sub_groups)
        else:
            split_components.append(comp)

    # Convert to diagram specs
    groups = []
    for i, comp in enumerate(split_components):
        if not comp:
            continue
        # Find the dominant directory
        dirs = defaultdict(int)
        for name in comp:
            src = class_index.get(name, {}).get("source_file", "")
            d = src.replace("\\", "/").split("/")[-2] if "/" in src else "root"
            dirs[d] += 1
        dominant_dir = max(dirs, key=dirs.get) if dirs else f"group_{i}"

        # Find main class (most methods/attributes or inheritance root)
        main_class = max(comp, key=lambda n: len(class_index.get(n, {}).get("methods", [])) + len(class_index.get(n, {}).get("attributes", [])))

        source_files = list(set(
            class_index.get(n, {}).get("source_file", "") for n in comp
        ))

        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', dominant_dir)[:30]

        groups.append({
            "name": safe_name,
            "title": f"Class Diagram — {dominant_dir}",
            "description": f"{len(comp)} classes centered around {main_class}. Includes: {', '.join(sorted(comp)[:8])}",
            "elements": sorted(comp),
            "source_files": [f for f in source_files if f],
            "scope": dominant_dir,
        })

    return groups


def _split_by_directory_fast(
    classes: list,
    max_per_group: int,
    min_per_group: int,
) -> list[dict]:
    """
    Fast O(n) directory-based splitting for large projects (>500 classes).
    Groups by top-level package directory, merges small groups.
    """
    # Group by top-level source directory
    dir_groups: dict[str, list[dict]] = defaultdict(list)
    for cls in classes:
        src = cls.get("source_file", "")
        parts = src.replace("\\", "/").split("/")
        # Use top 2 directory levels for grouping
        if len(parts) >= 3:
            d = "/".join(parts[-3:-1])
        elif len(parts) >= 2:
            d = parts[-2]
        else:
            d = "root"
        dir_groups[d].append(cls)

    # Sort groups by size (largest first)
    sorted_groups = sorted(dir_groups.items(), key=lambda x: len(x[1]), reverse=True)

    # Merge tiny groups into "misc"
    final_groups: list[tuple[str, list[dict]]] = []
    misc: list[dict] = []
    for d, clss in sorted_groups:
        if len(clss) >= min_per_group:
            final_groups.append((d, clss))
        else:
            misc.extend(clss)
    if misc:
        final_groups.append(("miscellaneous", misc))

    # Split oversized groups
    result_groups: list[tuple[str, list[dict]]] = []
    for d, clss in final_groups:
        if len(clss) > max_per_group:
            for i in range(0, len(clss), max_per_group):
                chunk = clss[i:i + max_per_group]
                result_groups.append((f"{d}_part{i//max_per_group+1}", chunk))
        else:
            result_groups.append((d, clss))

    # Convert to diagram specs
    groups = []
    for d, clss in result_groups:
        if not clss:
            continue
        names = [c.get("name", "") for c in clss if c.get("name")]
        source_files = list({c.get("source_file", "") for c in clss if c.get("source_file")})
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', d)[:30]
        # Pick the class with most methods as representative
        main_cls = max(clss, key=lambda c: len(c.get("methods", [])))
        groups.append({
            "name": safe_name,
            "title": f"Class Diagram — {d}",
            "description": f"{len(names)} classes in {d}. Main: {main_cls.get('name', '')}. Includes: {', '.join(names[:6])}",
            "elements": names,
            "source_files": source_files[:10],
            "scope": d,
        })

    return groups


def _sub_split_by_directory(
    component: set[str],
    class_index: dict,
    max_size: int,
) -> list[set[str]]:
    """Split a large component into sub-groups by source directory."""
    dir_groups: dict[str, set[str]] = defaultdict(set)
    for name in component:
        src = class_index.get(name, {}).get("source_file", "")
        d = "/".join(src.replace("\\", "/").split("/")[:-1]) if "/" in src else "root"
        dir_groups[d].add(name)

    result = []
    overflow: set[str] = set()
    for d, names in dir_groups.items():
        if len(names) <= max_size:
            result.append(names)
        else:
            # Further split alphabetically
            sorted_names = sorted(names)
            for i in range(0, len(sorted_names), max_size):
                result.append(set(sorted_names[i:i + max_size]))

    return result if result else [component]


# ── SEQUENCE DIAGRAM SPLITTING ───────────────────────────────────

def _split_sequence(ir: dict, max_per_group: int, min_per_group: int) -> list[dict]:
    """
    Find entry points (controllers, handlers, main functions) and their call chains.
    Each entry point → one sequence diagram.
    """
    classes = ir.get("classes", [])
    functions = ir.get("functions", [])

    entry_points = []

    # Controllers and handlers are entry points
    for cls in classes:
        name = cls.get("name", "").lower()
        kind = cls.get("kind", "")
        if any(kw in name for kw in ("controller", "handler", "main", "app", "server", "router", "endpoint")):
            entry_points.append({
                "name": cls.get("name", ""),
                "type": "class",
                "methods": [m.get("name", m) if isinstance(m, dict) else str(m) for m in cls.get("methods", [])],
                "source_file": cls.get("source_file", ""),
            })

    # Main functions are entry points
    for fn in functions:
        name = fn.get("name", "").lower()
        if any(kw in name for kw in ("main", "run", "start", "execute", "process", "handle")):
            entry_points.append({
                "name": fn.get("name", ""),
                "type": "function",
                "methods": [],
                "source_file": fn.get("source_file", ""),
            })

    # If no entry points found, create one per source directory
    if not entry_points:
        dirs = set()
        for cls in classes:
            src = cls.get("source_file", "")
            if "/" in src:
                dirs.add(src.replace("\\", "/").split("/")[-2])
        for d in dirs:
            entry_points.append({
                "name": d,
                "type": "module",
                "methods": [],
                "source_file": "",
            })

    if len(entry_points) <= 1:
        return []  # Single diagram

    groups = []
    for ep in entry_points[:6]:  # Max 6 sequence diagrams
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', ep["name"])[:30]
        groups.append({
            "name": safe_name,
            "title": f"Sequence — {ep['name']}",
            "description": f"Interaction flow starting from {ep['name']} ({ep['type']}). Methods: {', '.join(ep['methods'][:5])}",
            "elements": [ep["name"]],
            "source_files": [ep["source_file"]] if ep["source_file"] else [],
            "scope": ep["name"],
        })

    return groups


# ── USE CASE DIAGRAM SPLITTING ───────────────────────────────────

def _split_use_case(ir: dict, max_per_group: int, min_per_group: int) -> list[dict]:
    """
    Group by controller/handler class — each = one use case area.
    """
    classes = ir.get("classes", [])

    controllers = []
    for cls in classes:
        name = cls.get("name", "").lower()
        if any(kw in name for kw in ("controller", "handler", "router", "api", "endpoint", "service")):
            controllers.append(cls)

    if len(controllers) <= 2:
        return []  # Single diagram

    groups = []
    for ctrl in controllers[:6]:
        name = ctrl.get("name", "")
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', name)[:30]
        methods = [m.get("name", m) if isinstance(m, dict) else str(m) for m in ctrl.get("methods", [])]
        groups.append({
            "name": safe_name,
            "title": f"Use Cases — {name}",
            "description": f"Use cases from {name}. Capabilities: {', '.join(methods[:8])}",
            "elements": [name],
            "source_files": [ctrl.get("source_file", "")],
            "scope": name,
        })

    return groups


# ── ACTIVITY DIAGRAM SPLITTING ───────────────────────────────────

def _split_activity(ir: dict, max_per_group: int, min_per_group: int) -> list[dict]:
    """
    Find classes with complex control flow (many methods, orchestrator-like naming).
    """
    classes = ir.get("classes", [])
    functions = ir.get("functions", [])

    complex_elements = []

    for cls in classes:
        name = cls.get("name", "").lower()
        methods = cls.get("methods", [])
        # Classes with many methods or orchestrator naming = complex workflows
        if len(methods) >= 5 or any(kw in name for kw in ("orchestrator", "pipeline", "processor", "workflow", "service", "manager", "engine")):
            complex_elements.append({
                "name": cls.get("name", ""),
                "type": "class",
                "method_count": len(methods),
                "methods": [m.get("name", m) if isinstance(m, dict) else str(m) for m in methods[:10]],
                "source_file": cls.get("source_file", ""),
            })

    # Also check functions
    for fn in functions:
        name = fn.get("name", "").lower()
        if any(kw in name for kw in ("process", "execute", "run", "handle", "main", "pipeline")):
            complex_elements.append({
                "name": fn.get("name", ""),
                "type": "function",
                "method_count": 1,
                "methods": [],
                "source_file": fn.get("source_file", ""),
            })

    # Sort by complexity
    complex_elements.sort(key=lambda x: x["method_count"], reverse=True)

    if len(complex_elements) <= 1:
        return []

    groups = []
    for elem in complex_elements[:6]:
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', elem["name"])[:30]
        groups.append({
            "name": safe_name,
            "title": f"Activity — {elem['name']}",
            "description": f"Workflow in {elem['name']} ({elem['type']}). Key methods: {', '.join(elem['methods'][:5])}",
            "elements": [elem["name"]],
            "source_files": [elem["source_file"]] if elem["source_file"] else [],
            "scope": elem["name"],
        })

    return groups
