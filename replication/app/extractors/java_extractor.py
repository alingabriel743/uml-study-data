from pathlib import Path
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_java_parser
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Tree-sitter node type → IR kind
TYPE_DECLARATIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}

VISIBILITY_MODIFIERS = ("public", "private", "protected")


class JavaTreeSitterExtractor(BaseIRExtractor):
    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_java_parser()

    def extract_ir(self):
        # Filter to only include actual files, not directories
        java_files = [f for f in Path(self.repo_path).rglob("*.java") if f.is_file()]
        logger.info(f"Found {len(java_files)} .java files to parse.")

        parsed_files = [self._parse_java_file(file_path) for file_path in java_files]
        parsed_files = [f for f in parsed_files if f and f.get("elements")]

        self.ir["metadata"]["sourceLanguages"].append("java")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in java_files]
        self.ir["files"] = parsed_files

        return self.ir

    def _parse_java_file(self, file_path: Path) -> dict:
        try:
            code = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            code = file_path.read_text(encoding="latin-1")

        code_bytes = bytes(code, "utf-8")
        tree = self.parser.parse(code_bytes)
        root = tree.root_node

        package = ""
        for child in root.children:
            if child.type == "package_declaration":
                package = self._text(child, code_bytes).removeprefix("package").rstrip(";").strip()

        elements = self._extract_elements(root, code_bytes, package)

        return {
            "file": str(file_path),
            "language": "java",
            "package": package,
            "elements": elements
        }

    def _extract_elements(self, root_node, code: bytes, package: str) -> list:
        elements = []
        for node in self._walk(root_node):
            kind = TYPE_DECLARATIONS.get(node.type)
            if kind:
                type_info = self._extract_type_info(node, kind, code, package)
                if type_info:
                    elements.append(type_info)
        return elements

    def _extract_type_info(self, node, kind: str, code: bytes, package: str) -> dict:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node, code) if name_node else ""
        modifiers = self._modifiers(node, code)

        extends: list[str] = []
        implements: list[str] = []
        for child in node.children:
            if child.type == "superclass":
                extends.extend(self._type_names(child, code))
            elif child.type == "super_interfaces":
                implements.extend(self._type_names(child, code))
            elif child.type == "extends_interfaces":  # interface Foo extends Bar, Baz
                extends.extend(self._type_names(child, code))

        attributes = []
        methods = []
        enum_constants = []
        body = node.child_by_field_name("body")
        if body is not None:
            for item in body.children:
                if item.type == "enum_body_declarations":
                    for sub in item.children:
                        self._collect_member(sub, code, attributes, methods)
                elif item.type == "enum_constant":
                    const = item.child_by_field_name("name")
                    if const is not None:
                        enum_constants.append(self._text(const, code))
                else:
                    self._collect_member(item, code, attributes, methods)

        if kind == "record":
            params = node.child_by_field_name("parameters")
            if params is not None:
                for p in params.children:
                    if p.type in ("formal_parameter", "spread_parameter"):
                        attributes.append(self._record_component(p, code))

        if kind in ("interface", "annotation"):
            # Interface members are implicitly public
            for member in attributes + methods:
                if member["visibility"] == "package":
                    member["visibility"] = "public"

        if kind == "class" and "abstract" in modifiers:
            kind = "abstract"

        info = {
            "kind": kind,
            "name": name,
            "package": package,
            "visibility": self._visibility(modifiers),
            "modifiers": modifiers,
            "extends": extends,
            "implements": implements,
            "attributes": attributes,
            "methods": methods,
        }
        if enum_constants:
            info["enum_constants"] = enum_constants
        return info

    def _collect_member(self, item, code: bytes, attributes: list, methods: list) -> None:
        if item.type in ("field_declaration", "constant_declaration"):
            attributes.extend(self._extract_fields(item, code))
        elif item.type in ("method_declaration", "constructor_declaration", "compact_constructor_declaration",
                           "annotation_type_element_declaration"):
            methods.append(self._extract_method(item, code))

    def _extract_fields(self, node, code: bytes) -> list[dict]:
        modifiers = self._modifiers(node, code)
        type_node = node.child_by_field_name("type")
        field_type = self._text(type_node, code) if type_node else ""
        fields = []
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                fields.append({
                    "name": self._text(name_node, code) if name_node else "",
                    "type": field_type,
                    "visibility": self._visibility(modifiers),
                    "modifiers": modifiers,
                    "declaration": self._text(node, code),
                })
        return fields

    def _record_component(self, node, code: bytes) -> dict:
        type_node = node.child_by_field_name("type")
        name_node = node.child_by_field_name("name")
        if node.type == "spread_parameter":  # `String... names`: name sits in a variable_declarator
            type_node = next((c for c in node.children if c.type.endswith("type") or c.type == "type_identifier"), None)
            decl = next((c for c in node.children if c.type == "variable_declarator"), None)
            name_node = decl.child_by_field_name("name") if decl is not None else None
        return {
            "name": self._text(name_node, code) if name_node else "",
            "type": self._text(type_node, code) if type_node else "",
            "visibility": "private",
            "modifiers": ["final"],
            "declaration": self._text(node, code),
        }

    def _extract_method(self, node, code: bytes) -> dict:
        name_node = node.child_by_field_name("name")
        type_node = node.child_by_field_name("type")
        params_node = node.child_by_field_name("parameters")
        modifiers = self._modifiers(node, code)

        parameters = []
        if params_node is not None:
            param_text = self._text(params_node, code)
            parameters = [p.strip() for p in param_text.strip("()").split(",") if p.strip()]

        # Types the method body depends on (instantiations, local variable types, static calls):
        # used as structural evidence for dependency relationships
        referenced_types: list[str] = []
        body = node.child_by_field_name("body")
        if body is not None:
            for child in self._walk(body):
                if child.type == "type_identifier":
                    name = self._text(child, code)
                    if name not in referenced_types:
                        referenced_types.append(name)
                elif child.type == "method_invocation":
                    obj = child.child_by_field_name("object")
                    if obj is not None and obj.type == "identifier":
                        name = self._text(obj, code)
                        if name[:1].isupper() and name not in referenced_types:  # static call: Type.method()
                            referenced_types.append(name)

        return {
            "name": self._text(name_node, code) if name_node else "",
            "parameters": parameters,
            "return_type": self._text(type_node, code) if type_node else "",
            "visibility": self._visibility(modifiers),
            "modifiers": modifiers,
            "is_constructor": node.type in ("constructor_declaration", "compact_constructor_declaration"),
            "referenced_types": referenced_types,
        }

    def _modifiers(self, node, code: bytes) -> list[str]:
        for child in node.children:
            if child.type == "modifiers":
                return [self._text(m, code) for m in child.children
                        if m.type not in ("marker_annotation", "annotation", "line_comment", "block_comment")]
        return []

    @staticmethod
    def _visibility(modifiers: list[str]) -> str:
        for m in VISIBILITY_MODIFIERS:
            if m in modifiers:
                return m
        return "package"

    def _type_names(self, node, code: bytes) -> list[str]:
        """Direct supertypes from superclass / super_interfaces / extends_interfaces nodes.

        Only the outermost type of each entry is taken, with generic arguments removed but
        qualified segments kept: `Outer<String>.Base<Integer>` → `Outer.Base`, `java.io.Serializable` stays whole.
        """
        type_nodes = {"type_identifier", "scoped_type_identifier", "generic_type"}
        entries = []
        for child in node.children:
            if child.type == "type_list":
                entries.extend(c for c in child.children if c.type in type_nodes)
            elif child.type in type_nodes:
                entries.append(child)
        return [self._strip_generics(self._text(e, code)) for e in entries]

    @staticmethod
    def _strip_generics(text: str) -> str:
        out, depth = [], 0
        for ch in text:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            elif depth == 0 and not ch.isspace():
                out.append(ch)
        return "".join(out)

    def _walk(self, node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    @staticmethod
    def _text(node, code: bytes) -> str:
        return code[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
