from pathlib import Path
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_java_parser
from app.utils.logger import get_logger
from app.extractors.source_discovery import discover_source_files

logger = get_logger(__name__)

class JavaTreeSitterExtractor(BaseIRExtractor):
    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_java_parser()

    def extract_ir(self):
        # Filter to only include actual files, not directories
        java_files = discover_source_files(Path(self.repo_path), ("*.java",))
        logger.info(f"Found {len(java_files)} .java files to parse.")

        parsed_files = [self._parse_java_file(file_path) for file_path in java_files]
        parsed_files = [f for f in parsed_files if f and f.get("elements")]

        self.ir["metadata"]["sourceLanguages"].append("java")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in java_files]
        self.ir["files"] = parsed_files

        return self.ir

    def _parse_java_file(self, file_path: Path) -> dict:
        code = file_path.read_bytes()
        tree = self.parser.parse(code)
        root = tree.root_node

        elements = self._extract_elements(root, code)

        return {
            "file": str(file_path),
            "language": "java",
            "elements": elements
        }

    def _extract_elements(self, root_node, code: bytes) -> list:
        elements = []
        kinds = {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "record",
        }
        for node in self._walk(root_node):
            if node.type in kinds:
                class_info = self._extract_class_info(node, code, kinds[node.type])
                if class_info:
                    elements.append(class_info)
        return elements

    def _extract_class_info(self, node, code: bytes, kind: str = "class") -> dict:
        name_node = node.child_by_field_name("name")
        name = self._get_code_segment(name_node, code) if name_node is not None else ""
        attributes = []
        methods = []
        extends = ""
        implements = []

        for child in node.children:
            if child.type in {"identifier", "type_identifier"} and not name:
                name = self._get_code_segment(child, code)
            elif child.type in {"superclass", "extends_interfaces"}:
                names = [
                    self._get_code_segment(item, code)
                    for item in self._walk(child)
                    if item.type in {"type_identifier", "scoped_type_identifier"}
                ]
                if names:
                    extends = names[0] if kind == "class" else names
            elif child.type == "super_interfaces":
                implements = [
                    self._get_code_segment(item, code)
                    for item in self._walk(child)
                    if item.type in {"type_identifier", "scoped_type_identifier"}
                ]
            elif child.type in {"class_body", "interface_body", "enum_body", "record_body"}:
                for item in child.children:
                    if item.type == "field_declaration":
                        attributes.append(self._extract_field(item, code))
                    elif item.type == "enum_constant":
                        attributes.append({"declaration": self._get_code_segment(item, code)})
                    elif item.type in {"method_declaration", "constructor_declaration", "compact_constructor_declaration"}:
                        methods.append(self._extract_method(item, code))

        result = {
            "kind": kind,
            "name": name,
            "attributes": attributes,
            "methods": methods
        }
        if extends:
            result["extends"] = extends
        if implements:
            result["implements"] = sorted(set(implements))
        return result

    def _extract_field(self, node, code: bytes) -> dict:
        text = self._get_code_segment(node, code)
        return {"declaration": text}

    def _extract_method(self, node, code: bytes) -> dict:
        name = ""
        parameters = []

        for child in node.children:
            if child.type == "identifier":
                name = self._get_code_segment(child, code)
            elif child.type == "formal_parameters":
                param_text = self._get_code_segment(child, code)
                parameters = [p.strip() for p in param_text.strip("()").split(",") if p.strip()]

        return {
            "name": name,
            "parameters": parameters
        }

    def _walk(self, node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _get_code_segment(self, node, code: bytes) -> str:
        return code[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
