from pathlib import Path
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_python_parser
from app.utils.logger import get_logger

logger = get_logger(__name__)

class PythonTreeSitterExtractor(BaseIRExtractor):
    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_python_parser()

    def extract_ir(self):
        # Filter to only include actual files, not directories
        python_files = [f for f in Path(self.repo_path).rglob("*.py") if f.is_file()]
        logger.info(f"Found {len(python_files)} .py files to parse.")

        parsed_files = [self._parse_python_file(file_path) for file_path in python_files]
        parsed_files = [f for f in parsed_files if f and f.get("elements")]

        self.ir["metadata"]["sourceLanguages"].append("python")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in python_files]
        self.ir["files"] = parsed_files

        return self.ir

    def _parse_python_file(self, file_path: Path) -> dict:
        try:
            code = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            code = file_path.read_text(encoding="latin-1")

        tree = self.parser.parse(bytes(code, "utf-8"))
        root = tree.root_node

        elements = self._extract_elements(root, code)

        return {
            "file": str(file_path),
            "language": "python",
            "elements": elements
        }

    def _extract_elements(self, root_node, code: str) -> list:
        elements = []
        for node in self._walk(root_node):
            if node.type == "class_definition":
                class_info = self._extract_class_info(node, code)
                if class_info:
                    elements.append(class_info)
            elif node.type == "function_definition":
                # Top-level functions (not inside a class)
                if not self._is_inside_class(node):
                    func_info = self._extract_function(node, code)
                    if func_info:
                        elements.append({
                            "kind": "function",
                            "name": func_info["name"],
                            "parameters": func_info["parameters"]
                        })
        return elements

    def _is_inside_class(self, node) -> bool:
        """Check if a node is inside a class definition"""
        parent = node.parent
        while parent:
            if parent.type == "class_definition":
                return True
            parent = parent.parent
        return False

    def _extract_class_info(self, node, code: str) -> dict:
        name = ""
        attributes = []
        methods = []
        base_classes = []

        for child in node.children:
            if child.type == "identifier":
                name = self._get_code_segment(child, code)
            elif child.type == "argument_list":
                # Extract base classes from inheritance
                base_classes = self._extract_base_classes(child, code)
            elif child.type == "block":
                # Extract class body
                for item in child.children:
                    if item.type == "expression_statement":
                        # Check for class attributes (assignments at class level)
                        attr = self._extract_class_attribute(item, code)
                        if attr:
                            attributes.append(attr)
                    elif item.type == "function_definition":
                        method = self._extract_function(item, code)
                        if method:
                            methods.append(method)

        return {
            "kind": "class",
            "name": name,
            "base_classes": base_classes,
            "attributes": attributes,
            "methods": methods
        }

    def _extract_base_classes(self, node, code: str) -> list:
        """Extract base classes from argument_list in class definition"""
        base_classes = []
        for child in node.children:
            if child.type == "identifier":
                base_classes.append(self._get_code_segment(child, code))
            elif child.type == "attribute":
                # Handle cases like 'module.ClassName'
                base_classes.append(self._get_code_segment(child, code))
        return base_classes

    def _extract_class_attribute(self, node, code: str) -> dict:
        """Extract class-level attribute assignments"""
        for child in node.children:
            if child.type == "assignment":
                for subchild in child.children:
                    if subchild.type == "identifier":
                        attr_name = self._get_code_segment(subchild, code)
                        return {"name": attr_name, "declaration": self._get_code_segment(child, code)}
        return None

    def _extract_function(self, node, code: str) -> dict:
        name = ""
        parameters = []
        return_type = None

        for child in node.children:
            if child.type == "identifier":
                name = self._get_code_segment(child, code)
            elif child.type == "parameters":
                parameters = self._extract_parameters(child, code)
            elif child.type == "type":
                # Extract return type if present (Python 3.5+ type hints)
                return_type = self._get_code_segment(child, code)

        result = {
            "name": name,
            "parameters": parameters
        }
        
        if return_type:
            result["return_type"] = return_type

        return result

    def _extract_parameters(self, node, code: str) -> list:
        """Extract function parameters"""
        parameters = []
        for child in node.children:
            if child.type == "identifier":
                param_name = self._get_code_segment(child, code)
                # Skip 'self' and 'cls' parameters
                if param_name not in ["self", "cls"]:
                    parameters.append(param_name)
            elif child.type == "typed_parameter":
                # Handle typed parameters (e.g., name: str)
                param_text = self._get_code_segment(child, code)
                param_name = param_text.split(":")[0].strip()
                if param_name not in ["self", "cls"]:
                    parameters.append(param_text)
            elif child.type == "default_parameter":
                # Handle parameters with default values
                param_text = self._get_code_segment(child, code)
                param_name = param_text.split("=")[0].strip()
                if param_name not in ["self", "cls"]:
                    parameters.append(param_text)
            elif child.type == "typed_default_parameter":
                # Handle typed parameters with default values
                param_text = self._get_code_segment(child, code)
                param_name = param_text.split(":")[0].strip()
                if param_name not in ["self", "cls"]:
                    parameters.append(param_text)

        return parameters

    def _walk(self, node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _get_code_segment(self, node, code: str) -> str:
        return code[node.start_byte:node.end_byte].strip()
