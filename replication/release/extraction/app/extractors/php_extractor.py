from pathlib import Path
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_php_parser
from app.utils.logger import get_logger
from app.extractors.source_discovery import discover_source_files

logger = get_logger(__name__)

class PHPTreeSitterExtractor(BaseIRExtractor):
    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_php_parser()

    def extract_ir(self):
        # Filter to only include actual files, not directories
        php_files = discover_source_files(Path(self.repo_path), ("*.php",))
        logger.info(f"Found {len(php_files)} .php files to parse.")

        parsed_files = []
        for file_path in php_files:
            try:
                parsed_file = self._parse_php_file(file_path)
                if parsed_file:
                    parsed_files.append(parsed_file)
                    logger.debug(f"Successfully parsed {file_path}: {len(parsed_file.get('elements', []))} elements")
                else:
                    logger.warning(f"Failed to parse {file_path}: returned None")
            except Exception as e:
                logger.error(f"Exception parsing {file_path}: {e}")

        # Filter files that have elements
        filtered_files = [f for f in parsed_files if f and f.get("elements")]
        logger.info(f"Parsed {len(parsed_files)} files, {len(filtered_files)} have elements")

        self.ir["metadata"]["sourceLanguages"].append("php")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in php_files]
        self.ir["files"] = filtered_files

        return self.ir

    def _parse_php_file(self, file_path: Path) -> dict:
        code = file_path.read_bytes()
        tree = self.parser.parse(code)
        root = tree.root_node

        elements = self._extract_elements(root, code)

        return {
            "file": str(file_path),
            "language": "php",
            "elements": elements
        }

    def _extract_elements(self, root_node, code: bytes) -> list:
        elements = []
        for node in self._walk(root_node):
            if node.type == "class_declaration":
                class_info = self._extract_class_info(node, code)
                if class_info:
                    elements.append(class_info)
            elif node.type == "interface_declaration":
                interface_info = self._extract_interface_info(node, code)
                if interface_info:
                    elements.append(interface_info)
            elif node.type == "trait_declaration":
                trait_info = self._extract_trait_info(node, code)
                if trait_info:
                    elements.append(trait_info)
            elif node.type == "function_definition":
                function_info = self._extract_function_info(node, code)
                if function_info:
                    elements.append(function_info)
        
        # If no OOP elements found, create a procedural file element
        if not elements:
            # For procedural PHP files, create a file-level element
            file_element = {
                "kind": "file",
                "name": "procedural_code",
                "attributes": [],
                "methods": [],
                "code_snippet": code[:500].decode("utf-8", errors="replace")
            }
            elements.append(file_element)
        
        return elements

    def _extract_class_info(self, node, code: str) -> dict:
        name = ""
        attributes = []
        methods = []
        extends = ""
        implements = []

        for child in node.children:
            if child.type == "name":
                name = self._get_code_segment(child, code)
            elif child.type == "base_clause":
                extends = self._extract_extends(child, code)
            elif child.type == "class_interface_clause":
                implements = self._extract_implements(child, code)
            elif child.type == "declaration_list":
                for item in child.children:
                    if item.type == "property_declaration":
                        attributes.append(self._extract_property(item, code))
                    elif item.type == "method_declaration":
                        methods.append(self._extract_method(item, code))

        return {
            "kind": "class",
            "name": name,
            "attributes": attributes,
            "methods": methods,
            "extends": extends,
            "implements": implements
        }

    def _extract_interface_info(self, node, code: str) -> dict:
        name = ""
        methods = []
        extends = []

        for child in node.children:
            if child.type == "name":
                name = self._get_code_segment(child, code)
            elif child.type == "base_clause":
                extends = self._extract_interface_extends(child, code)
            elif child.type == "declaration_list":
                for item in child.children:
                    if item.type == "method_declaration":
                        methods.append(self._extract_method(item, code))

        return {
            "kind": "interface",
            "name": name,
            "methods": methods,
            "extends": extends
        }

    def _extract_trait_info(self, node, code: str) -> dict:
        name = ""
        attributes = []
        methods = []

        for child in node.children:
            if child.type == "name":
                name = self._get_code_segment(child, code)
            elif child.type == "declaration_list":
                for item in child.children:
                    if item.type == "property_declaration":
                        attributes.append(self._extract_property(item, code))
                    elif item.type == "method_declaration":
                        methods.append(self._extract_method(item, code))

        return {
            "kind": "trait",
            "name": name,
            "attributes": attributes,
            "methods": methods
        }

    def _extract_function_info(self, node, code: str) -> dict:
        name = ""
        parameters = []

        for child in node.children:
            if child.type == "name":
                name = self._get_code_segment(child, code)
            elif child.type == "formal_parameters":
                parameters = self._extract_parameters(child, code)

        return {
            "kind": "function",
            "name": name,
            "parameters": parameters
        }

    def _extract_property(self, node, code: str) -> dict:
        visibility = "public"
        name = ""
        type_hint = ""
        is_static = False

        for child in node.children:
            if child.type == "visibility_modifier":
                visibility = self._get_code_segment(child, code)
            elif child.type == "static_modifier":
                is_static = True
            elif child.type == "property_element":
                for prop_child in child.children:
                    if prop_child.type == "variable_name":
                        name = self._get_code_segment(prop_child, code)
            elif child.type == "type":
                type_hint = self._get_code_segment(child, code)

        return {
            "name": name,
            "visibility": visibility,
            "type": type_hint,
            "static": is_static
        }

    def _extract_method(self, node, code: str) -> dict:
        name = ""
        parameters = []
        visibility = "public"
        is_static = False
        is_abstract = False
        return_type = ""

        for child in node.children:
            if child.type == "name":
                name = self._get_code_segment(child, code)
            elif child.type == "formal_parameters":
                parameters = self._extract_parameters(child, code)
            elif child.type == "visibility_modifier":
                visibility = self._get_code_segment(child, code)
            elif child.type == "static_modifier":
                is_static = True
            elif child.type == "abstract_modifier":
                is_abstract = True
            elif child.type == "return_type":
                return_type = self._get_code_segment(child, code)

        return {
            "name": name,
            "parameters": parameters,
            "visibility": visibility,
            "static": is_static,
            "abstract": is_abstract,
            "return_type": return_type
        }

    def _extract_parameters(self, node, code: str) -> list:
        parameters = []
        for child in node.children:
            if child.type == "simple_parameter" or child.type == "variadic_parameter":
                param_info = self._extract_parameter(child, code)
                if param_info:
                    parameters.append(param_info)
        return parameters

    def _extract_parameter(self, node, code: str) -> dict:
        name = ""
        type_hint = ""
        default_value = ""
        is_variadic = node.type == "variadic_parameter"

        for child in node.children:
            if child.type == "variable_name":
                name = self._get_code_segment(child, code)
            elif child.type == "type":
                type_hint = self._get_code_segment(child, code)
            elif child.type == "default_argument_specifier":
                for default_child in child.children:
                    if default_child.type != "=":
                        default_value = self._get_code_segment(default_child, code)

        return {
            "name": name,
            "type": type_hint,
            "default": default_value,
            "variadic": is_variadic
        }

    def _extract_extends(self, node, code: str) -> str:
        for child in node.children:
            if child.type == "name":
                return self._get_code_segment(child, code)
        return ""

    def _extract_implements(self, node, code: str) -> list:
        implements = []
        for child in node.children:
            if child.type == "name":
                implements.append(self._get_code_segment(child, code))
        return implements

    def _extract_interface_extends(self, node, code: str) -> list:
        extends = []
        for child in node.children:
            if child.type == "name":
                extends.append(self._get_code_segment(child, code))
        return extends

    def _walk(self, node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _get_code_segment(self, node, code: bytes) -> str:
        return code[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
