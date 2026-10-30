from pathlib import Path
from app.extractors.base_extractor import BaseIRExtractor
from tree_sitter_language_pack import get_parser

from app.utils.ir_extraction.tree_sitter_helper import get_javascript_parser, get_typescript_parser
from app.utils.logger import get_logger
from app.extractors.source_discovery import discover_source_files

logger = get_logger(__name__)


class JavaScriptTreeSitterExtractor(BaseIRExtractor):
    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_javascript_parser()

    def extract_ir(self):
        # Filter to only include actual files, not directories (e.g., zone.js/ directory)
        # rglob will still search inside those directories recursively
        root = Path(self.repo_path)
        js_files = discover_source_files(root, ("*.js",))
        ts_files = discover_source_files(root, ("*.ts",))
        jsx_files = discover_source_files(root, ("*.jsx",))
        tsx_files = discover_source_files(root, ("*.tsx",))
        
        all_files = js_files + ts_files + jsx_files + tsx_files
        logger.info(f"Found {len(all_files)} JavaScript/TypeScript files to parse.")

        parsed_files = [self._parse_js_file(file_path) for file_path in all_files]
        parsed_files = [f for f in parsed_files if f and f.get("elements")]

        self.ir["metadata"]["sourceLanguages"].append("javascript")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in all_files]
        self.ir["files"] = parsed_files

        return self.ir

    def _parse_js_file(self, file_path: Path) -> dict:
        code = file_path.read_bytes()
        if file_path.suffix.lower() == ".ts":
            parser = get_typescript_parser()
        elif file_path.suffix.lower() == ".tsx":
            parser = get_parser("tsx")
        else:
            parser = self.parser

        tree = parser.parse(code)
        root = tree.root_node
        elements = self._extract_elements(root, code)

        return {
            "file": str(file_path),
            "language": "javascript",
            "elements": elements
        }

    def _extract_elements(self, root_node, code: bytes) -> list:
        elements = []
        
        for node in self._walk(root_node):
            ancestors = self._ancestor_types(node)
            if node.type in {"class_declaration", "abstract_class_declaration"}:
                if "export_statement" in ancestors:
                    continue
                class_info = self._extract_class_info(node, code)
                if class_info:
                    elements.append(class_info)
            elif node.type == "interface_declaration":
                if "export_statement" in ancestors:
                    continue
                interface_info = self._extract_interface_info(node, code)
                if interface_info:
                    elements.append(interface_info)
            elif node.type == "function_declaration":
                if "export_statement" in ancestors:
                    continue
                func_info = self._extract_function_info(node, code)
                if func_info:
                    elements.append(func_info)
            elif node.type == "arrow_function":
                # Assigned arrow functions are captured by their variable declarator. Anonymous
                # callbacks have no stable declaration identity and are not IR entities.
                continue
            elif node.type == "method_definition":
                # Methods are captured inside their containing class declaration.
                continue
            elif node.type in {"variable_declaration", "lexical_declaration"}:
                if {"variable_declaration", "lexical_declaration"}.intersection(ancestors):
                    continue
                var_info = self._extract_variable_info(node, code)
                if var_info:
                    elements.extend(var_info)
            elif node.type == "export_statement":
                export_info = self._extract_export_info(node, code)
                if export_info:
                    elements.extend(export_info)
            elif node.type == "import_statement":
                import_info = self._extract_import_info(node, code)
                if import_info:
                    elements.append(import_info)
                    
        return elements

    def _extract_class_info(self, node, code: bytes) -> dict:
        name = ""
        methods = []
        properties = []
        extends = ""
        
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            name = self._get_code_segment(name_node, code)

        for child in node.children:
            if child.type in {"identifier", "type_identifier"} and not name:
                name = self._get_code_segment(child, code)
            elif child.type == "class_heritage":
                # Look for extends clause
                for heritage_child in child.children:
                    if heritage_child.type == "extends_clause":
                        for extends_child in heritage_child.children:
                            if extends_child.type == "identifier":
                                extends = self._get_code_segment(extends_child, code)
            elif child.type == "class_body":
                for member in child.children:
                    if member.type == "method_definition":
                        methods.append(self._extract_method_from_class(member, code))
                    elif member.type == "field_definition":
                        properties.append(self._extract_property(member, code))

        result = {
            "kind": "class",
            "name": name,
            "methods": methods,
            "properties": properties
        }
        
        if extends:
            result["extends"] = extends
            
        return result

    def _extract_interface_info(self, node, code: bytes) -> dict:
        name_node = node.child_by_field_name("name")
        name = self._get_code_segment(name_node, code) if name_node is not None else ""
        methods = []
        properties = []
        extends = []
        for descendant in self._walk(node):
            if descendant is node:
                continue
            if descendant.type in {"method_signature", "abstract_method_signature"}:
                methods.append(self._extract_method_info(descendant, code))
            elif descendant.type in {"property_signature", "public_field_definition"}:
                properties.append(self._extract_property(descendant, code))
            elif descendant.type in {"extends_type_clause", "extends_clause"}:
                for item in descendant.children:
                    if item.type in {"identifier", "type_identifier", "nested_type_identifier"}:
                        extends.append(self._get_code_segment(item, code))
        return {
            "kind": "interface",
            "name": name,
            "methods": methods,
            "properties": properties,
            "extends": extends,
        }

    def _extract_function_info(self, node, code: bytes) -> dict:
        name = ""
        parameters = []
        body = ""
        is_async = False
        
        for child in node.children:
            if child.type == "identifier":
                name = self._get_code_segment(child, code)
            elif child.type == "formal_parameters":
                parameters = self._extract_parameters(child, code)
            elif child.type == "statement_block":
                body = self._get_code_segment(child, code)
            elif child.type == "async":
                is_async = True

        return {
            "kind": "function",
            "name": name,
            "parameters": parameters,
            "body": body,
            "is_async": is_async,
            "references": self._extract_references_from_body(body)
        }

    def _extract_arrow_function_info(self, node, code: bytes) -> dict:
        parameters = []
        body = ""
        
        # Arrow functions are often assigned to variables, try to get parent context
        parent_name = self._get_parent_variable_name(node, code)
        
        for child in node.children:
            if child.type == "formal_parameters":
                parameters = self._extract_parameters(child, code)
            elif child.type == "identifier":
                # Single parameter without parentheses
                parameters = [self._get_code_segment(child, code)]
            elif child.type == "statement_block" or child.type == "expression_statement":
                body = self._get_code_segment(child, code)

        return {
            "kind": "arrow_function",
            "name": parent_name or "anonymous",
            "parameters": parameters,
            "body": body,
            "references": self._extract_references_from_body(body)
        }

    def _extract_method_info(self, node, code: bytes) -> dict:
        name = ""
        parameters = []
        body = ""
        is_static = False
        is_async = False
        visibility = "public"
        
        for child in node.children:
            if child.type == "property_identifier":
                name = self._get_code_segment(child, code)
            elif child.type == "formal_parameters":
                parameters = self._extract_parameters(child, code)
            elif child.type == "statement_block":
                body = self._get_code_segment(child, code)
            elif child.type == "static":
                is_static = True
            elif child.type == "async":
                is_async = True
            elif child.type == "private":
                visibility = "private"

        return {
            "kind": "method",
            "name": name,
            "parameters": parameters,
            "body": body,
            "is_static": is_static,
            "is_async": is_async,
            "visibility": visibility,
            "references": self._extract_references_from_body(body)
        }

    def _extract_method_from_class(self, node, code: bytes) -> dict:
        method = self._extract_method_info(node, code)
        # Remove body for class overview, keep just signature
        method["body"] = ""
        return method

    def _extract_property(self, node, code: bytes) -> dict:
        name = ""
        type_annotation = ""
        initial_value = ""
        
        for child in node.children:
            if child.type == "property_identifier":
                name = self._get_code_segment(child, code)
            elif child.type == "type_annotation":
                type_annotation = self._get_code_segment(child, code)
            elif child.type in ["number", "string", "true", "false", "null", "identifier"]:
                initial_value = self._get_code_segment(child, code)

        return {
            "name": name,
            "type": type_annotation,
            "initial_value": initial_value
        }

    def _extract_variable_info(self, node, code: bytes) -> list:
        variables = []
        
        for child in node.children:
            if child.type == "variable_declarator":
                var_info = self._extract_variable_declarator(child, code)
                if var_info:
                    variables.append(var_info)
                    
        return variables

    def _extract_variable_declarator(self, node, code: bytes) -> dict:
        name = ""
        value = ""
        
        for child in node.children:
            if child.type == "identifier":
                name = self._get_code_segment(child, code)
            elif child.type in ["arrow_function", "function_expression", "class_expression"]:
                # Variable holds a function or class
                if child.type == "arrow_function":
                    details = self._extract_arrow_function_info(child, code)
                    return {
                        "kind": "variable_function",
                        "name": name,
                        "function_type": "arrow",
                        "parameters": details.get("parameters", []),
                        "body": details.get("body", "")[:200],
                        "references": details.get("references", []),
                    }
                elif child.type == "function_expression":
                    return {
                        "kind": "variable_function", 
                        "name": name,
                        "function_type": "expression",
                        "definition": self._get_code_segment(child, code)[:200]
                    }
                elif child.type == "class_expression":
                    return {
                        "kind": "variable_class",
                        "name": name,
                        "definition": self._get_code_segment(child, code)[:200]
                    }
            else:
                value = self._get_code_segment(child, code)

        if name:
            return {
                "kind": "variable",
                "name": name,
                "value": value[:100] if value else ""  # Truncate value
            }
        return None

    def _extract_export_info(self, node, code: bytes) -> list:
        exports = []
        
        for child in node.children:
            if child.type == "export_specifier":
                export_name = ""
                local_name = ""
                for spec_child in child.children:
                    if spec_child.type == "identifier":
                        if not local_name:
                            local_name = self._get_code_segment(spec_child, code)
                        else:
                            export_name = self._get_code_segment(spec_child, code)
                            
                exports.append({
                    "kind": "export",
                    "name": export_name or local_name,
                    "local_name": local_name,
                    "export_type": "named"
                })
            elif child.type in {"class_declaration", "abstract_class_declaration"}:
                class_info = self._extract_class_info(child, code)
                if class_info:
                    class_info["exported"] = True
                    exports.append(class_info)
            elif child.type == "interface_declaration":
                interface_info = self._extract_interface_info(child, code)
                if interface_info:
                    interface_info["exported"] = True
                    exports.append(interface_info)
            elif child.type == "function_declaration":
                func_info = self._extract_function_info(child, code)
                if func_info:
                    func_info["exported"] = True
                    exports.append(func_info)
                    
        return exports

    def _extract_import_info(self, node, code: bytes) -> dict:
        source = ""
        imports = []
        import_type = "named"
        
        for child in node.children:
            if child.type == "string":
                source = self._get_code_segment(child, code).strip('"\'')
            elif child.type == "import_specifier":
                import_name = ""
                local_name = ""
                for spec_child in child.children:
                    if spec_child.type == "identifier":
                        if not import_name:
                            import_name = self._get_code_segment(spec_child, code)
                        else:
                            local_name = self._get_code_segment(spec_child, code)
                imports.append({
                    "name": import_name,
                    "local_name": local_name or import_name
                })
            elif child.type == "namespace_import":
                import_type = "namespace"
                for spec_child in child.children:
                    if spec_child.type == "identifier":
                        imports.append({
                            "name": "*",
                            "local_name": self._get_code_segment(spec_child, code)
                        })
            elif child.type == "identifier":
                # Default import
                import_type = "default"
                imports.append({
                    "name": "default",
                    "local_name": self._get_code_segment(child, code)
                })

        name = ",".join(item.get("local_name", "") for item in imports if item.get("local_name")) or source
        return {
            "kind": "import",
            "name": name,
            "source": source,
            "imports": imports,
            "import_type": import_type
        }

    def _extract_parameters(self, node, code: bytes) -> list:
        parameters = []
        
        for child in node.children:
            if child.type == "identifier":
                parameters.append(self._get_code_segment(child, code))
            elif child.type == "formal_parameter":
                param_name = ""
                param_type = ""
                default_value = ""
                
                for param_child in child.children:
                    if param_child.type == "identifier":
                        param_name = self._get_code_segment(param_child, code)
                    elif param_child.type == "type_annotation":
                        param_type = self._get_code_segment(param_child, code)
                    elif param_child.type == "assignment_pattern":
                        # Parameter with default value
                        for assign_child in param_child.children:
                            if assign_child.type == "identifier":
                                param_name = self._get_code_segment(assign_child, code)
                            else:
                                default_value = self._get_code_segment(assign_child, code)
                
                param_str = param_name
                if param_type:
                    param_str += f": {param_type}"
                if default_value:
                    param_str += f" = {default_value}"
                    
                parameters.append(param_str)
                
        return parameters

    def _extract_references_from_body(self, body: str) -> list:
        """Extract function calls and variable references from function body"""
        references = []
        
        # Simple regex-based extraction for common patterns
        import re
        
        # Function calls: functionName(
        function_calls = re.findall(r'(\w+)\s*\(', body)
        references.extend(function_calls)
        
        # Method calls: object.method(
        method_calls = re.findall(r'(\w+)\.(\w+)\s*\(', body)
        references.extend([f"{obj}.{method}" for obj, method in method_calls])
        
        # React hooks: use*
        hook_calls = re.findall(r'(use[A-Z]\w*)\s*\(', body)
        references.extend(hook_calls)
        
        # Common async patterns
        async_patterns = re.findall(r'(await|\.then|\.catch|Promise\.)', body)
        references.extend(async_patterns)
        
        # Remove duplicates and common keywords
        references = list(set(references))
        js_keywords = {
            'if', 'else', 'for', 'while', 'function', 'var', 'let', 'const', 
            'return', 'console', 'this', 'new', 'class', 'extends', 'import', 
            'export', 'default', 'async', 'await', 'try', 'catch', 'finally'
        }
        references = [ref for ref in references if ref.lower() not in js_keywords]
        
        return references[:15]  # Limit to 15 references

    def _get_parent_variable_name(self, node, code: bytes) -> str:
        """Try to get the variable name that an arrow function is assigned to"""
        # This is a simplified approach - in a real implementation,
        # you'd walk up the AST to find the variable declarator
        
        # Try to find parent variable declarator by looking at surrounding context
        start_byte = max(0, node.start_byte - 100)
        context = code[start_byte:node.start_byte].decode("utf-8", errors="replace")
        
        # Look for patterns like "const variableName = " or "let variableName = "
        import re
        match = re.search(r'(?:const|let|var)\s+(\w+)\s*=\s*$', context)
        if match:
            return match.group(1)
        
        # Look for object property assignment like "obj.method = "
        match = re.search(r'(\w+)\s*=\s*$', context)
        if match:
            return match.group(1)
            
        return "anonymous"

    def _walk(self, node):
        """Walk the AST tree"""
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _ancestor_types(self, node) -> set[str]:
        result: set[str] = set()
        parent = node.parent
        while parent is not None:
            result.add(parent.type)
            parent = parent.parent
        return result

    def _get_code_segment(self, node, code: bytes) -> str:
        """Get the text content of a node"""
        return code[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
