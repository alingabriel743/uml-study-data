from pathlib import Path
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_csharp_parser
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CSharpTreeSitterExtractor(BaseIRExtractor):
    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_csharp_parser()

    def extract_ir(self):
        # Filter to only include actual files, not directories
        cs_files = [f for f in Path(self.repo_path).rglob("*.cs") if f.is_file()]
        logger.info(f"Found {len(cs_files)} .cs files to parse.")

        parsed_files = [self._parse_cs_file(file_path) for file_path in cs_files]
        parsed_files = [f for f in parsed_files if f and f.get("elements")]

        self.ir["metadata"]["sourceLanguages"].append("csharp")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in cs_files]
        self.ir["files"] = parsed_files

        return self.ir

    def _parse_cs_file(self, file_path: Path) -> dict:
        try:
            code = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            code = file_path.read_text(encoding="latin-1")

        tree = self.parser.parse(bytes(code, "utf-8"))
        root = tree.root_node
        elements = self._extract_elements(root, code)

        return {
            "file": str(file_path),
            "language": "csharp",
            "elements": elements
        }

    def _extract_elements(self, root_node, code: str) -> list:
        elements = []
        for node in self._walk(root_node):
            if node.type == "class_declaration":
                class_info = self._extract_class_info(node, code)
                if class_info:
                    elements.append(class_info)
        return elements

    def _extract_class_info(self, node, code: str) -> dict:
        name = ""
        attributes = []
        methods = []

        for child in node.children:
            if child.type == "identifier":
                name = self._get_code_segment(child, code)
            elif child.type == "class_body":
                for member in child.children:
                    if member.type == "field_declaration":
                        attributes.append(self._extract_field(member, code))
                    elif member.type == "method_declaration":
                        methods.append(self._extract_method(member, code))

        return {
            "kind": "class",
            "name": name,
            "attributes": attributes,
            "methods": methods
        }

    def _extract_field(self, node, code: str) -> dict:
        return {
            "declaration": self._get_code_segment(node, code)
        }

    def _extract_method(self, node, code: str) -> dict:
        name = ""
        parameters = []

        for child in node.children:
            if child.type == "identifier":
                name = self._get_code_segment(child, code)
            elif child.type == "parameter_list":
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

    def _get_code_segment(self, node, code: str) -> str:
        return code[node.start_byte:node.end_byte].strip()
