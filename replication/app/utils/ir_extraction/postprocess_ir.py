# app/utils/ir_extraction/postprocess_ir.py
# FIXED VERSION - Properly handles arrow functions and other JavaScript elements

import json
import re
from pathlib import Path
from typing import Any
from app.utils.logger import get_logger

logger = get_logger(__name__)

def postprocess_ir(ir_path: str):
    ir_file = Path(ir_path)
    if not ir_file.exists():
        logger.error(f"IR file not found: {ir_path}")
        return

    logger.info(f"Loading IR from {ir_path}")
    try:
        with ir_file.open("r", encoding="utf-8") as f:
            raw_ir = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load IR: {e}")
        return

    logger.info("Postprocessing IR content")
    try:
        all_elements = []
        for file_entry in raw_ir.get("files", []):
            file_path = file_entry.get("file", "")
            for el in file_entry.get("elements", []):
                el["source_file"] = file_path
                all_elements.append(el)

        languages = [lang.lower() for lang in raw_ir.get("metadata", {}).get("sourceLanguages", [])]
        logger.info(f"Detected source languages: {languages}")

        if "java" in languages:
            grouped = clean_java_elements(all_elements)
        elif "csharp" in languages or "c#" in languages:
            grouped = clean_csharp_elements(all_elements)
        elif "javascript" in languages or "js" in languages:
            grouped = clean_javascript_elements(all_elements)
        elif "cpp" in languages or "c++" in languages:
            grouped = clean_cpp_elements(all_elements)
        elif "python" in languages or "py" in languages:
            grouped = clean_python_elements(all_elements)
        elif "kotlin" in languages:
            grouped = clean_kotlin_elements(all_elements)
        elif "cobol" in languages:
            grouped = clean_cobol_elements(all_elements)
        elif "perl" in languages or "pl" in languages:
            grouped = clean_perl_elements(all_elements)
        elif "php" in languages:
            grouped = clean_php_elements(all_elements)
        elif any(l in languages for l in ["sql", "plsql", "pl/sql"]):
            grouped = clean_sql_elements(all_elements)
        else:
            logger.warning(f"Unsupported language(s) in IR metadata: {languages}")
            grouped = empty_group()

        processed_ir = {
            "metadata": {
                "projectName": raw_ir.get("metadata", {}).get("projectName"),
                "sourceLanguages": raw_ir.get("metadata", {}).get("sourceLanguages", []),
                "sourceFiles": raw_ir.get("metadata", {}).get("sourceFiles", []),
                "elementCounts": summarize_elements(grouped)
            },
            **grouped
        }

        output_path = ir_file.parent / "ir_postprocessed.json"
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(processed_ir, f, indent=2, ensure_ascii=False)

        logger.info(f"Postprocessed IR saved to {output_path}")
        return str(output_path)

    except Exception as e:
        logger.exception(f"Error during IR postprocessing: {e}")


def is_anonymous_or_trivial(element: dict, language: str = "unknown") -> bool:
    """
    Check if an element should be filtered out as anonymous or trivial
    FIXED: Much more conservative for JavaScript/TypeScript
    """
    name = element.get("name", "").strip()
    
    if not name:
        return True  # Empty names are always trivial
    
    # Universal anonymous patterns (very conservative)
    truly_anonymous_patterns = [
        "anonymous", "Anonymous", "ANONYMOUS", "", "unknown", 
        "__generated__", "__auto__", "_anonymous", "(anonymous)", "<anonymous>"
    ]
    
    if name in truly_anonymous_patterns:
        return True
    
    # Language-specific patterns
    if language.lower() in ["javascript", "typescript"]:
        # CRITICAL FIX: Only filter truly anonymous JS elements
        js_anonymous_patterns = ["(anonymous)", "<anonymous>", "AnonymousFunction"]
        if any(pattern in name for pattern in js_anonymous_patterns):
            return True
        
        # Only filter if it's literally just symbols with no meaningful name
        if name in ["=>", "function()", "() =>", "function"]:
            return True
            
        # KEEP ALL NAMED ELEMENTS - React components, event handlers, API functions are all important
        # Check if element has any meaningful content
        methods = element.get("methods", [])
        properties = element.get("properties", [])
        parameters = element.get("parameters", [])
        body = element.get("body", "")
        
        # If it has ANY meaningful content, definitely keep it
        if methods or properties or parameters or (body and len(body.strip()) > 5):
            return False
            
        # Only filter extremely generic names with no content
        if name.lower() in ["temp", "tmp", "temporary"] and not any([methods, properties, parameters, body]):
            return True
    
    elif language.lower() == "java":
        # Java anonymous inner classes: $1, $2, etc.
        if name.startswith("$") and name[1:].isdigit():
            return True
        if "lambda$" in name:
            return True
    
    elif language.lower() == "csharp":
        # C# anonymous types and methods
        if any(pattern in name for pattern in ["<>c", "<>f__AnonymousType", "DisplayClass"]):
            return True
    
    elif language.lower() == "sql":
        # SQL temporary objects - be very conservative
        if any(pattern in name.upper() for pattern in ["TEMP_", "TMP_", "SYS_"]):
            return True
        return False  # Keep all named SQL elements
    
    # For other languages, be conservative
    # Only filter if element has NO meaningful content AND a suspicious name
    if language.lower() != "sql":
        methods = element.get("methods", [])
        properties = element.get("properties", [])
        attributes = element.get("attributes", [])
        functions = element.get("functions", [])
        classes = element.get("classes", [])
        
        # If element has ANY structural content, keep it
        if methods or properties or attributes or functions or classes:
            return False
        
        # Only filter if name is very short AND no content
        if len(name) <= 1:
            return True
        
        # Filter common auto-generated patterns only
        auto_generated_patterns = ["temp", "tmp", "temporary"]
        if name.lower() in auto_generated_patterns and not any([methods, properties, attributes]):
            return True
    
    return False  # Default: keep the element (conservative approach)


def clean_javascript_elements(elements: list[dict[str, Any]]) -> dict:
    """
    Clean and organize JavaScript/TypeScript IR elements
    FIXED: Properly handle arrow functions and be much less aggressive
    """
    
    original_count = len(elements)
    
    # CRITICAL FIX: Increase threshold for small projects and be more conservative
    if original_count <= 200:  # Increased from 100 to 200
        logger.info(f"Small/Medium JS/TS project detected ({original_count} elements) - using minimal filtering")
        
        # For small/medium projects, only filter truly anonymous elements
        filtered_elements = []
        for el in elements:
            name = el.get("name", "").strip()
            # Only filter if truly anonymous, empty, or just symbols
            if name and name not in ["(anonymous)", "<anonymous>", "", "function()", "() =>", "=>"]:
                filtered_elements.append(el)
        
        filtered_count = len(filtered_elements)
        logger.info(f"Small/Medium project JS/TS filtering: {original_count} -> {filtered_count} elements "
                   f"({((original_count - filtered_count) / original_count * 100):.1f}% reduction)")
    else:
        # For large projects, use more conservative filtering
        filtered_elements = [el for el in elements if not is_anonymous_or_trivial(el, "javascript")]
        filtered_count = len(filtered_elements)
        
        if original_count > filtered_count:
            logger.info(f"Large project JS/TS filtering: {original_count} -> {filtered_count} elements "
                       f"({((original_count - filtered_count) / original_count * 100):.1f}% reduction)")
    
    output = empty_group()
    
    # Process ALL filtered elements
    for element in filtered_elements:
        kind = element.get("kind", "").strip().lower()
        element["kind"] = kind
        
        if "name" in element and isinstance(element["name"], str):
            element["name"] = element["name"].strip()
        
        # Clean references
        if "references" in element and isinstance(element["references"], list):
            element["references"] = sorted(set(element["references"]))
        
        # Clean parameters
        if "parameters" in element and isinstance(element["parameters"], list):
            element["parameters"] = [p.strip() for p in element["parameters"] if p.strip()]
        
        source = element.get("source_file")
        
        match kind:
            case "class":
                # JavaScript/TypeScript classes
                js_class = {
                    "kind": "class",
                    "name": element.get("name"),
                    "methods": element.get("methods", []),
                    "properties": element.get("properties", []),
                    "extends": element.get("extends"),
                    "exported": element.get("exported", False),
                    "source_file": source
                }
                output["classes"].append(js_class)
                
            case "function" | "arrow_function":  # CRITICAL FIX: Handle arrow functions
                # JavaScript/TypeScript functions (including arrow functions)
                js_function = {
                    "kind": "function",
                    "name": element.get("name"),
                    "parameters": element.get("parameters", []),
                    "body": element.get("body", "")[:200],  # Truncate for size
                    "is_async": element.get("is_async", False),
                    "function_type": element.get("function_type", "arrow" if kind == "arrow_function" else "regular"),
                    "is_exported": element.get("exported", False),
                    "source_file": source
                }
                output["functions"].append(js_function)
                
            case "variable":
                # Variables and constants
                if element.get("is_constant") or element.get("is_exported"):
                    js_var = {
                        "kind": "variable",
                        "name": element.get("name"),
                        "value": element.get("value", ""),
                        "is_constant": element.get("is_constant", False),
                        "is_exported": element.get("exported", False),
                        "source_file": source
                    }
                    output["types"].append(js_var)
                    
            case "import":
                # Import statements
                js_import = {
                    "kind": "import",
                    "name": element.get("name"),
                    "source": element.get("source", ""),
                    "imports": element.get("imports", []),
                    "import_type": element.get("import_type", "named"),
                    "source_file": source
                }
                output["types"].append(js_import)
                
            case "export":
                # Export statements
                js_export = {
                    "kind": "export",
                    "name": element.get("name"),
                    "local_name": element.get("local_name"),
                    "export_type": element.get("export_type", "named"),
                    "source_file": source
                }
                output["types"].append(js_export)
                
            case _:  # fallback - CRITICAL FIX: Handle other kinds for small projects
                # For small/medium projects, keep unhandled elements as functions/types
                if original_count <= 200:
                    fallback_element = {
                        "kind": "function",  # Treat unknown kinds as functions
                        "name": element.get("name"),
                        "parameters": element.get("parameters", []),
                        "body": element.get("body", "")[:200],
                        "is_async": element.get("is_async", False),
                        "function_type": kind,  # Keep original kind as function_type
                        "source_file": source
                    }
                    output["functions"].append(fallback_element)
                else:
                    logger.warning(f"Unhandled JavaScript element kind: {kind} ({element.get('name')})")
    
    # Convert JavaScript elements to unified class structure with minimal filtering
    _convert_js_to_unified_structure_minimal_filtering(output, original_count)
    
    return output


def _convert_js_to_unified_structure_minimal_filtering(output: dict, original_count: int):
    """
    Convert JavaScript-specific elements to unified class structure
    FIXED: Minimal filtering for small/medium projects
    """
    
    functions_to_add = []
    filtered_functions = []
    
    # CRITICAL FIX: For small/medium projects, keep ALL functions
    if original_count <= 200:  # Increased threshold
        logger.info("Small/Medium project: keeping all functions in conversion")
        for func in output["functions"]:
            func_name = func.get("name", "")
            # Keep ALL named functions for small/medium projects
            if func_name and func_name.strip():
                func_class = {
                    "kind": "function",
                    "name": func_name,
                    "parameters": func.get("parameters", []),
                    "body": func.get("body", "")[:200],
                    "is_async": func.get("is_async", False),
                    "function_type": func.get("function_type", "regular"),
                    "source_file": func.get("source_file")
                }
                functions_to_add.append(func_class)
                filtered_functions.append(func)
    else:
        # For large projects, apply conservative filtering
        for func in output["functions"]:
            func_name = func.get("name", "")
            # Even for large projects, be more conservative
            if func_name and func_name.strip() and not is_anonymous_or_trivial({"name": func_name}, "javascript"):
                func_class = {
                    "kind": "function",
                    "name": func_name,
                    "parameters": func.get("parameters", []),
                    "body": func.get("body", "")[:200],
                    "is_async": func.get("is_async", False),
                    "function_type": func.get("function_type", "regular"),
                    "source_file": func.get("source_file")
                }
                functions_to_add.append(func_class)
                filtered_functions.append(func)
    
    # Process imports/exports with minimal filtering
    modules = {}
    filtered_types = []
    
    for type_el in output["types"]:
        if type_el.get("kind") == "import":
            source = type_el.get("source", "")
            # For small/medium projects, keep ALL imports
            if original_count <= 200 or (source and len(source) > 2):
                module_name = source.split("/")[-1] if "/" in source else source
                if source not in modules:
                    modules[source] = {
                        "kind": "module",
                        "name": module_name,
                        "imports": [],
                        "exports": [],
                        "source_file": type_el.get("source_file")
                    }
                modules[source]["imports"].extend(type_el.get("imports", []))
                filtered_types.append(type_el)
        else:
            # For small/medium projects, keep ALL other types
            type_name = type_el.get("name", "")
            if original_count <= 200 or not is_anonymous_or_trivial({"name": type_name}, "javascript"):
                filtered_types.append(type_el)
    
    # Add modules as classes
    for module in modules.values():
        functions_to_add.append(module)
    
    output["classes"].extend(functions_to_add)
    output["functions"] = filtered_functions  
    output["types"] = filtered_types         
    
    # Log final counts
    total_final = len(output["classes"]) + len(output["functions"]) + len(output["types"])
    logger.info(f"After filtering conversion: {len(output['classes'])} classes, "
               f"{len(output['functions'])} functions, {len(output['types'])} types "
               f"(total: {total_final})")


def empty_group() -> dict:
    """Return empty group structure for IR elements"""
    return {
        "classes": [],
        "tables": [],
        "views": [],
        "statements": [],
        "selects": [],
        "functions": [],
        "procedures": [],
        "types": []
    }


def extract_view_name(sql: str) -> str:
    """Extract view name from CREATE VIEW SQL statement"""
    match = re.search(r"create\s+view\s+([\w\.]+)", sql, re.IGNORECASE)
    return match.group(1) if match else "unknown_view"


def deduplicate_by_name(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate items by name, keeping first occurrence"""
    seen = {}
    for item in items:
        name = item.get("name")
        if name and name not in seen:
            seen[name] = item
    return list(seen.values())


def summarize_elements(grouped: dict[str, list[dict]]) -> dict:
    """Create summary count of elements by type"""
    return {k: len(v) for k, v in grouped.items()}


def _canonical_type_names(names: list[str]) -> list[str]:
    """`java.util.List<Foo>` → `List`; keeps first-seen order, drops duplicates."""
    seen: list[str] = []
    for n in names or []:
        simple = re.sub(r"<[^<>]*>", "", n) if "<" in n else n
        while "<" in simple:  # nested generics
            simple = re.sub(r"<[^<>]*>", "", simple)
        simple = simple.strip().split(".")[-1]
        if simple and simple not in seen:
            seen.append(simple)
    return seen


def clean_java_elements(elements: list[dict[str, Any]]) -> dict:
    """Small project awareness for Java"""
    original_count = len(elements)
    
    # Small project protection
    if original_count <= 50:
        logger.info(f"Small Java project detected ({original_count} elements) - using minimal filtering")
        filtered_elements = [el for el in elements if el.get("name", "").strip()]
    else:
        filtered_elements = [el for el in elements if not is_anonymous_or_trivial(el, "java")]
    
    filtered_count = len(filtered_elements)
    
    if original_count > filtered_count:
        logger.info(f"Java filtering: {original_count} -> {filtered_count} elements "
                   f"({((original_count - filtered_count) / original_count * 100):.1f}% reduction)")
        
    java_type_kinds = {"class", "abstract", "interface", "enum", "record", "annotation"}
    classes = []
    for el in filtered_elements:
        if el.get("kind") in java_type_kinds:
            java_class = {
                "kind": el.get("kind"),
                "name": el.get("name"),
                "package": el.get("package", ""),
                "visibility": el.get("visibility", "package"),
                "modifiers": el.get("modifiers", []),
                # Inheritance canonicalization: simple names, generics stripped, de-duplicated
                "extends": _canonical_type_names(el.get("extends", [])),
                "implements": _canonical_type_names(el.get("implements", [])),
                "attributes": el.get("attributes", []),
                "methods": el.get("methods", []),
                "source_file": el.get("source_file")
            }
            if el.get("enum_constants"):
                java_class["enum_constants"] = el["enum_constants"]
            classes.append(java_class)
    return {
        "classes": classes,
        "tables": [],
        "views": [],
        "statements": [],
        "selects": [],
        "functions": [],
        "procedures": [],
        "types": []
    }


def clean_csharp_elements(elements: list[dict[str, Any]]) -> dict:
    """Small project awareness for C#"""
    original_count = len(elements)
    
    # Small project protection
    if original_count <= 50:
        logger.info(f"Small C# project detected ({original_count} elements) - using minimal filtering")
        filtered_elements = [el for el in elements if el.get("name", "").strip()]
    else:
        filtered_elements = [el for el in elements if not is_anonymous_or_trivial(el, "csharp")]
    
    filtered_count = len(filtered_elements)
    
    if original_count > filtered_count:
        logger.info(f"C# filtering: {original_count} -> {filtered_count} elements "
                   f"({((original_count - filtered_count) / original_count * 100):.1f}% reduction)")
        
    classes = []
    for el in filtered_elements:
        if el.get("kind") == "class":
            classes.append({
                "kind": "class",
                "name": el.get("name"),
                "attributes": el.get("attributes", []),
                "methods": el.get("methods", []),
                "source_file": el.get("source_file")
            })
    return {
        "classes": classes,
        "tables": [],
        "views": [],
        "statements": [],
        "selects": [],
        "functions": [],
        "procedures": [],
        "types": []
    }


def clean_cpp_elements(elements: list[dict[str, Any]]) -> dict:
    """Clean and organize C++ IR elements with small project awareness"""
    
    original_count = len(elements)
    
    if original_count <= 50:
        logger.info(f"Small C++ project detected ({original_count} elements) - using minimal filtering")
        filtered_elements = [el for el in elements if el.get("name", "").strip()]
    else:
        filtered_elements = [el for el in elements if not is_anonymous_or_trivial(el, "cpp")]
    
    filtered_count = len(filtered_elements)
    
    if original_count > filtered_count:
        logger.info(f"C++ filtering: {original_count} -> {filtered_count} elements "
                   f"({((original_count - filtered_count) / original_count * 100):.1f}% reduction)")
        
    output = empty_group()
    
    for element in filtered_elements:
        kind = element.get("kind", "").strip().lower()
        element["kind"] = kind
        
        if "name" in element and isinstance(element["name"], str):
            element["name"] = element["name"].strip()
        
        source = element.get("source_file")
        
        match kind:
            case "class":
                output["classes"].append({
                    "kind": "class",
                    "name": element.get("name"),
                    "attributes": element.get("attributes", []),
                    "methods": element.get("methods", []),
                    "base_classes": element.get("base_classes", []),
                    "access_levels": element.get("access_levels", []),
                    "source_file": source
                })
            case "struct":
                output["classes"].append({
                    "kind": "struct", 
                    "name": element.get("name"),
                    "attributes": element.get("attributes", []),
                    "methods": element.get("methods", []),
                    "source_file": source
                })
            case "function":
                output["functions"].append({
                    "kind": "function",
                    "name": element.get("name"),
                    "parameters": element.get("parameters", []),
                    "return_type": element.get("return_type", ""),
                    "source_file": source
                })
            case "namespace":
                output["classes"].append({
                    "kind": "namespace",
                    "name": element.get("name"),
                    "source_file": source
                })
            case "enum":
                output["types"].append({
                    "kind": "enum",
                    "name": element.get("name"),
                    "values": element.get("values", []),
                    "source_file": source
                })
            case _:
                logger.warning(f"Unhandled C++ element kind: {kind} ({element.get('name')})")
    
    # Convert functions to class-like structures for consistency
    for func in output["functions"]:
        output["classes"].append({
            "kind": "function",
            "name": func["name"],
            "parameters": func.get("parameters", []),
            "return_type": func.get("return_type", ""),
            "source_file": func.get("source_file")
        })
    
    # Convert types to class-like structures  
    for t in output["types"]:
        output["classes"].append({
            "kind": "enum",
            "name": t.get("name"),
            "values": t.get("values", []),
            "source_file": t.get("source_file")
        })
    
    return output


def clean_sql_elements(elements: list[dict[str, Any]]) -> dict:
    """Clean SQL elements with small project awareness"""
    
    original_count = len(elements)
    logger.info(f"SQL Processing: Starting with {original_count} raw elements")
    
    # Small project protection for SQL
    if original_count <= 50:
        logger.info(f"Small SQL project detected ({original_count} elements) - using minimal filtering")
        filtered_elements = [el for el in elements if el.get("name", "").strip()]
    else:
        filtered_elements = [el for el in elements if not is_anonymous_or_trivial(el, "sql")]
    
    filtered_count = len(filtered_elements)
    logger.info(f"SQL filtering: {original_count} -> {filtered_count} elements")
    
    output = empty_group()

    for element in filtered_elements:
        kind = element.get("kind", "").strip().lower()
        element["kind"] = kind

        if "name" in element and isinstance(element["name"], str):
            element["name"] = element["name"].strip()

        if "return_type" in element and isinstance(element["return_type"], str):
            element["return_type"] = element["return_type"].strip()

        if "references" in element and isinstance(element["references"], list):
            element["references"] = sorted(set(map(str.upper, element["references"])))

        if "parameters" in element and isinstance(element["parameters"], list):
            element["parameters"] = [p.strip() for p in element["parameters"] if p.strip()]

        if "attributes" in element and isinstance(element["attributes"], list):
            element["attributes"].sort(key=lambda x: x.get("name", "").lower())

        source = element.get("source_file")

        match kind:
            case "function":
                output["functions"].append(element)
            case "procedure":
                output["procedures"].append(element)
            case "type" | "object_type":
                output["types"].append(element)
            case "table":
                output["tables"].append(element)
            case "select":
                output["selects"].append(element)
            case "statement":
                raw = element.get("raw_sql", "").lower()
                if "create view" in raw:
                    view_name = extract_view_name(element.get("raw_sql", ""))
                    output["views"].append({
                        "kind": "view",
                        "name": view_name,
                        "definition": element.get("raw_sql", ""),
                        "references": element.get("references", []),
                        "source_file": source
                    })
                else:
                    output["statements"].append(element)
            case "package_spec":
                pkg = {
                    "kind": "package",
                    "name": element.get("name"),
                    "procedures": deduplicate_by_name(element.get("procedures", [])),
                    "functions": [],
                    "attributes": [],
                    "raw": element.get("raw", ""),
                    "source_file": source
                }
                output["classes"].append(pkg)
            case _:
                logger.warning(f"Unhandled element kind: {kind} ({element.get('name')})")

    # Expand to unified class structure
    for t in output["types"]:
        output["classes"].append({
            "kind": "object_type",
            "name": t.get("name"),
            "attributes": t.get("attributes", []),
            "source_file": t.get("source_file")
        })

    for proc in output["procedures"]:
        pkg = proc.get("package")
        if pkg:
            matched = next((c for c in output["classes"] if c["kind"] == "package" and c["name"] == pkg), None)
            if matched:
                matched.setdefault("procedures", []).append(proc)
                matched["procedures"] = deduplicate_by_name(matched["procedures"])
            else:
                output["classes"].append({
                    "kind": "package",
                    "name": pkg,
                    "procedures": [proc],
                    "functions": [],
                    "attributes": [],
                    "source_file": proc.get("source_file")
                })
        else:
            output["classes"].append({
                "kind": "procedure",
                "name": proc["name"],
                "in_parameters": proc.get("in_parameters", []),
                "out_parameters": proc.get("out_parameters", []),
                "inout_parameters": proc.get("inout_parameters", []),
                "return_type": proc.get("return_type"),
                "body": proc.get("body"),
                "references": proc.get("references", []),
                "source_file": proc.get("source_file")
            })

    for fn in output["functions"]:
        output["classes"].append({
            "kind": "function",
            "name": fn["name"],
            "parameters": fn.get("parameters", []),
            "return_type": fn.get("return_type"),
            "body": fn.get("body"),
            "references": fn.get("references", []),
            "source_file": fn.get("source_file")
        })

    for table in output["tables"]:
        output["classes"].append({
            "kind": "table",
            "name": table.get("name"),
            "columns": table.get("columns", []),
            "source_file": table.get("source_file")
        })

    return output

def clean_kotlin_elements(elements: list[dict[str, Any]]) -> dict:
    """Clean and organize Kotlin IR elements"""
    classes = []
    objects = []
    interfaces = []
    functions = []
    
    for el in elements:
        kind = el.get("kind", "")
        
        if kind in ["class", "data_class", "sealed_class", "enum_class"]:
            classes.append({
                "kind": kind,
                "name": el.get("name"),
                "attributes": el.get("attributes", []),
                "methods": el.get("methods", []),
                "primary_constructor": el.get("primary_constructor", []),
                "source_file": el.get("source_file")
            })
        elif kind == "object":
            objects.append({
                "kind": "object",
                "name": el.get("name"),
                "attributes": el.get("attributes", []),
                "methods": el.get("methods", []),
                "source_file": el.get("source_file")
            })
        elif kind == "interface":
            interfaces.append({
                "kind": "interface", 
                "name": el.get("name"),
                "methods": el.get("methods", []),
                "source_file": el.get("source_file")
            })
        elif kind == "function":
            functions.append({
                "kind": "function",
                "name": el.get("name"),
                "parameters": el.get("parameters", []),
                "return_type": el.get("return_type", ""),
                "visibility": el.get("visibility", "public"),
                "is_suspend": el.get("is_suspend", False),
                "is_inline": el.get("is_inline", False),
                "source_file": el.get("source_file")
            })
    
    return {
        "classes": classes,
        "objects": objects,  # Kotlin-specific: singleton objects
        "interfaces": interfaces,
        "functions": functions,  # Top-level functions
        "tables": [],
        "views": [],
        "statements": [],
        "selects": [],
        "procedures": [],
        "types": []
    }


def clean_cobol_elements(elements: list[dict[str, Any]]) -> dict:
    """Clean COBOL elements with mainframe project awareness"""
    
    original_count = len(elements)
    logger.info(f"COBOL Processing: Starting with {original_count} raw elements")
    
    # COBOL projects often have many similar elements (data items, copybooks)
    # Use minimal filtering for smaller projects to preserve structure
    if original_count <= 100:
        logger.info(f"Small COBOL project detected ({original_count} elements) - using minimal filtering")
        filtered_elements = [el for el in elements if el.get("name", "").strip()]
    else:
        filtered_elements = [el for el in elements if not is_anonymous_or_trivial(el, "cobol")]
    
    filtered_count = len(filtered_elements)
    logger.info(f"COBOL filtering: {original_count} -> {filtered_count} elements")
    
    output = empty_group()
    programs = []
    procedures = []  # COBOL paragraphs and sections
    data_items = []  # COBOL data structures
    
    for element in filtered_elements:
        kind = element.get("kind", "").strip().lower()
        element["kind"] = kind

        if "name" in element and isinstance(element["name"], str):
            element["name"] = element["name"].strip()

        # COBOL programs
        if kind == "program":
            programs.append({
                "kind": "program",
                "name": element.get("name"),
                "attributes": element.get("attributes", []),
                "methods": element.get("methods", []),
                "source_file": element.get("source_file")
            })
        
        # COBOL paragraphs and sections (treat as procedures)
        elif kind in ["paragraph", "section"]:
            procedures.append({
                "kind": kind,
                "name": element.get("name"),
                "parameters": [],  # COBOL paragraphs don't have formal parameters
                "source_file": element.get("source_file")
            })
        
        # COBOL data items (records, elementary items, conditions)
        elif kind in ["record", "data_item", "elementary_item", "condition", "rename_item"]:
            data_items.append({
                "kind": kind,
                "name": element.get("name"),
                "attributes": element.get("attributes", []),
                "level": element.get("attributes", [{}])[0].get("level", "") if element.get("attributes") else "",
                "source_file": element.get("source_file")
            })
    
    logger.info(f"COBOL categorization: {len(programs)} programs, {len(procedures)} procedures, {len(data_items)} data items")
    
    return {
        "classes": programs,  # COBOL programs as classes
        "procedures": procedures,  # Paragraphs and sections
        "tables": data_items,  # Data structures as tables
        "functions": [],
        "views": [],
        "statements": [],
        "selects": [],
        "interfaces": [],
        "types": []
    }


def clean_php_elements(elements: list[dict[str, Any]]) -> dict:
    """Clean and organize PHP IR elements with project size awareness"""
    
    original_count = len(elements)
    logger.info(f"PHP Processing: Starting with {original_count} raw elements")
    
    # Small project protection for PHP
    if original_count <= 100:
        logger.info(f"Small PHP project detected ({original_count} elements) - using minimal filtering")
        filtered_elements = [el for el in elements if el.get("name", "").strip()]
    else:
        filtered_elements = [el for el in elements if not is_anonymous_or_trivial(el, "php")]
    
    filtered_count = len(filtered_elements)
    
    if original_count > filtered_count:
        logger.info(f"PHP filtering: {original_count} -> {filtered_count} elements "
                   f"({((original_count - filtered_count) / original_count * 100):.1f}% reduction)")
    
    output = empty_group()
    
    for element in filtered_elements:
        kind = element.get("kind", "").strip().lower()
        element["kind"] = kind
        
        if "name" in element and isinstance(element["name"], str):
            element["name"] = element["name"].strip()
        
        source = element.get("source_file")
        
        match kind:
            case "class":
                # PHP classes
                php_class = {
                    "kind": "class",
                    "name": element.get("name"),
                    "attributes": element.get("attributes", []),
                    "methods": element.get("methods", []),
                    "extends": element.get("extends"),
                    "implements": element.get("implements", []),
                    "source_file": source
                }
                output["classes"].append(php_class)
                
            case "interface":
                # PHP interfaces
                php_interface = {
                    "kind": "interface",
                    "name": element.get("name"),
                    "methods": element.get("methods", []),
                    "extends": element.get("extends", []),
                    "source_file": source
                }
                output["classes"].append(php_interface)
                
            case "trait":
                # PHP traits
                php_trait = {
                    "kind": "trait",
                    "name": element.get("name"),
                    "attributes": element.get("attributes", []),
                    "methods": element.get("methods", []),
                    "source_file": source
                }
                output["classes"].append(php_trait)
                
            case "function":
                # PHP functions
                php_function = {
                    "kind": "function",
                    "name": element.get("name"),
                    "parameters": element.get("parameters", []),
                    "return_type": element.get("return_type", ""),
                    "source_file": source
                }
                output["functions"].append(php_function)
                
            case "file":
                # PHP procedural files (new case for procedural PHP)
                php_file = {
                    "kind": "file",
                    "name": element.get("name", "procedural_code"),
                    "code_snippet": element.get("code_snippet", ""),
                    "source_file": source
                }
                output["classes"].append(php_file)
                
            case _:
                # Handle other PHP elements as classes for consistency
                logger.debug(f"Handling PHP element kind '{kind}' as class: {element.get('name')}")
                php_element = {
                    "kind": kind,
                    "name": element.get("name"),
                    "attributes": element.get("attributes", []),
                    "methods": element.get("methods", []),
                    "source_file": source
                }
                output["classes"].append(php_element)
    
    # Convert functions to class-like structures for consistency with other languages
    for func in output["functions"]:
        output["classes"].append({
            "kind": "function",
            "name": func["name"],
            "parameters": func.get("parameters", []),
            "return_type": func.get("return_type", ""),
            "source_file": func.get("source_file")
        })
    
    logger.info(f"PHP categorization: {len(output['classes'])} total elements (classes, interfaces, traits, functions)")
    
    return output


def clean_python_elements(elements: list[dict[str, Any]]) -> dict:
    """Clean and organize Python IR elements with project size awareness"""
    
    original_count = len(elements)
    logger.info(f"Python Processing: Starting with {original_count} raw elements")
    
    # Small project protection for Python
    if original_count <= 100:
        logger.info(f"Small Python project detected ({original_count} elements) - using minimal filtering")
        filtered_elements = [el for el in elements if el.get("name", "").strip()]
    else:
        filtered_elements = [el for el in elements if not is_anonymous_or_trivial(el, "python")]
    
    filtered_count = len(filtered_elements)
    
    if original_count > filtered_count:
        logger.info(f"Python filtering: {original_count} -> {filtered_count} elements "
                   f"({((original_count - filtered_count) / original_count * 100):.1f}% reduction)")
    
    output = empty_group()
    
    for element in filtered_elements:
        kind = element.get("kind", "").strip().lower()
        element["kind"] = kind
        
        if "name" in element and isinstance(element["name"], str):
            element["name"] = element["name"].strip()
        
        source = element.get("source_file")
        
        match kind:
            case "class":
                # Python classes
                python_class = {
                    "kind": "class",
                    "name": element.get("name"),
                    "attributes": element.get("attributes", []),
                    "methods": element.get("methods", []),
                    "base_classes": element.get("base_classes", []),
                    "source_file": source
                }
                output["classes"].append(python_class)
                
            case "function":
                # Python functions (top-level)
                python_function = {
                    "kind": "function",
                    "name": element.get("name"),
                    "parameters": element.get("parameters", []),
                    "return_type": element.get("return_type", ""),
                    "source_file": source
                }
                output["functions"].append(python_function)
                
            case _:
                # Handle other Python elements as classes for consistency
                logger.debug(f"Handling Python element kind '{kind}' as class: {element.get('name')}")
                python_element = {
                    "kind": kind,
                    "name": element.get("name"),
                    "attributes": element.get("attributes", []),
                    "methods": element.get("methods", []),
                    "source_file": source
                }
                output["classes"].append(python_element)
    
    # Convert functions to class-like structures for consistency with other languages
    for func in output["functions"]:
        output["classes"].append({
            "kind": "function",
            "name": func["name"],
            "parameters": func.get("parameters", []),
            "return_type": func.get("return_type", ""),
            "source_file": func.get("source_file")
        })
    
    logger.info(f"Python categorization: {len(output['classes'])} total elements (classes and functions)")
    
    return output


def clean_perl_elements(elements: list[dict[str, Any]]) -> dict:
    """Clean and organize Perl IR elements with project size awareness.

    Maps Perl `package` elements to classes and `function_definition` / standalone
    subroutines to functions, following the same conventions as the PHP cleaner.
    """
    original_count = len(elements)
    logger.info(f"Perl Processing: Starting with {original_count} raw elements")

    # Minimal filtering for small/medium projects
    if original_count <= 200:
        logger.info(f"Small/Medium Perl project ({original_count} elements) - using minimal filtering")
        filtered_elements = [el for el in elements if el.get("name", "").strip()]
    else:
        filtered_elements = [el for el in elements if not is_anonymous_or_trivial(el, "perl")]

    filtered_count = len(filtered_elements)
    if original_count > filtered_count:
        logger.info(
            f"Perl filtering: {original_count} -> {filtered_count} elements "
            f"({((original_count - filtered_count) / original_count * 100):.1f}% reduction)"
        )

    output = empty_group()

    for element in filtered_elements:
        kind = element.get("kind", "").strip().lower()
        element["kind"] = kind

        if "name" in element and isinstance(element["name"], str):
            element["name"] = element["name"].strip()

        source = element.get("source_file")

        match kind:
            case "package":
                # Perl package (module) -> class
                perl_package = {
                    "kind": "class",
                    "name": element.get("name"),
                    "methods": element.get("methods", []),
                    "attributes": element.get("attributes", []),
                    "source_file": source,
                }
                output["classes"].append(perl_package)

            case "function":
                # Named subroutine
                perl_function = {
                    "kind": "function",
                    "name": element.get("name"),
                    "parameters": element.get("parameters", []),
                    "source_file": source,
                }
                output["functions"].append(perl_function)

            case "file":
                # Procedural script with no explicit package
                perl_file = {
                    "kind": "file",
                    "name": element.get("name", "procedural_code"),
                    "code_snippet": element.get("code_snippet", ""),
                    "source_file": source,
                }
                output["classes"].append(perl_file)

            case _:
                # Treat anything else as a class
                logger.debug(f"Handling Perl element kind '{kind}' as class: {element.get('name')}")
                output["classes"].append({
                    "kind": kind,
                    "name": element.get("name"),
                    "attributes": element.get("attributes", []),
                    "methods": element.get("methods", []),
                    "source_file": source,
                })

    # Promote standalone functions to class-like entries for diagram consistency
    for func in output["functions"]:
        output["classes"].append({
            "kind": "function",
            "name": func["name"],
            "parameters": func.get("parameters", []),
            "source_file": func.get("source_file"),
        })

    logger.info(
        f"Perl categorization: {len(output['classes'])} total elements "
        f"(packages, functions, procedural files)"
    )

    return output
