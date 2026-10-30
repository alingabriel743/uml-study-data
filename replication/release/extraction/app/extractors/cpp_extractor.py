# app/extractors/cpp_extractor.py - FINAL WORKING VERSION

from pathlib import Path
from typing import List, Dict, Any
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_initialized_cpp_parser
from app.utils.logger import get_logger

logger = get_logger(__name__)

class CppTreeSitterExtractor(BaseIRExtractor):
    """
    FINAL Tree-sitter based C++ code extractor.
    Correctly handles method declarations as field_declaration nodes.
    """
    
    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_initialized_cpp_parser()

    def extract_ir(self):
        """Extract C++ code elements from repository"""
        repo = Path(self.repo_path)
        
        # Find all C++ files - filter to only include actual files, not directories
        cpp_files = []
        cpp_extensions = [".cpp", ".cc", ".cxx", ".c++", ".hpp", ".h", ".hxx", ".h++"]
        
        for ext in cpp_extensions:
            cpp_files.extend([f for f in repo.rglob(f"*{ext}") if f.is_file()])
        
        if not cpp_files:
            logger.warning("No C++ files found in repository")
            return self.ir

        logger.info(f"Found {len(cpp_files)} C++ files")
        
        # Parse each file
        parsed_files = []
        for file_path in cpp_files:
            try:
                parsed_file = self._parse_cpp_file(file_path)
                if parsed_file and parsed_file.get("elements"):
                    parsed_files.append(parsed_file)
            except Exception as e:
                logger.error(f"Error parsing {file_path}: {e}")
                continue

        self.ir["metadata"]["sourceLanguages"].append("cpp")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in cpp_files]
        self.ir["files"] = parsed_files

        return self.ir

    def _parse_cpp_file(self, file_path: Path) -> dict:
        """Parse a single C++ file"""
        try:
            code = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                code = file_path.read_text(encoding="latin-1")
            except Exception as e:
                logger.error(f"Failed to read file {file_path}: {e}")
                return None

        try:
            tree = self.parser.parse(bytes(code, "utf-8"))
            root = tree.root_node
            elements = self._extract_elements(root, code)

            return {
                "file": str(file_path),
                "language": "cpp",
                "elements": elements
            }
        except Exception as e:
            logger.error(f"Failed to parse file {file_path}: {e}")
            return None

    def _extract_elements(self, root_node, code: str) -> list:
        """Extract C++ elements from AST"""
        elements = []
        
        try:
            for node in self._walk(root_node):
                try:
                    if node.type == "class_specifier":
                        class_info = self._extract_class_info(node, code)
                        if class_info and class_info.get("name"):
                            elements.append(class_info)
                    elif node.type == "struct_specifier":
                        struct_info = self._extract_struct_info(node, code)
                        if struct_info and struct_info.get("name"):
                            elements.append(struct_info)
                    elif node.type == "function_definition":
                        func_info = self._extract_function_info(node, code)
                        if func_info and func_info.get("name"):
                            # Skip method definitions (containing ::)
                            if "::" not in func_info.get("name", ""):
                                elements.append(func_info)
                    elif node.type == "namespace_definition":
                        ns_info = self._extract_namespace_info(node, code)
                        if ns_info and ns_info.get("name"):
                            elements.append(ns_info)
                    elif node.type == "enum_specifier":
                        enum_info = self._extract_enum_info(node, code)
                        if enum_info and enum_info.get("name"):
                            elements.append(enum_info)
                except Exception as e:
                    logger.debug(f"Error processing node {node.type}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error in element extraction: {e}")
                    
        return elements

    def _extract_class_info(self, node, code: str) -> dict:
        """Extract C++ class information - FIXED for method declarations"""
        name = ""
        attributes = []
        methods = []
        base_classes = []
        
        try:
            # Get class name
            for child in node.children:
                if child.type == "type_identifier":
                    name = self._get_code_segment(child, code)
                    break
            
            if not name:
                return None
            
            # Extract base class information
            for child in node.children:
                if child.type == "base_class_clause":
                    for base_child in child.children:
                        if base_child.type in ["type_identifier", "qualified_identifier"]:
                            base_classes.append(self._get_code_segment(base_child, code))
            
            # Extract class body
            class_body = None
            for child in node.children:
                if child.type == "field_declaration_list":
                    class_body = child
                    break
            
            if class_body:
                current_access = "private"  # Default for classes
                
                for member in class_body.children:
                    try:
                        if member.type == "access_specifier":
                            access_text = self._get_code_segment(member, code)
                            if "public" in access_text:
                                current_access = "public"
                            elif "protected" in access_text:
                                current_access = "protected" 
                            elif "private" in access_text:
                                current_access = "private"
                                
                        elif member.type == "field_declaration":
                            # FIXED: Check if this is actually a method declaration
                            is_method = any(child.type == "function_declarator" for child in member.children)
                            
                            if is_method:
                                # This is a method declaration, not a field
                                method = self._extract_method_from_field_declaration(member, code, current_access)
                                if method and method.get("name"):
                                    methods.append(method)
                            else:
                                # This is a regular field/attribute
                                attr = self._extract_field(member, code, current_access)
                                if attr and attr.get("name"):
                                    attributes.append(attr)
                                    
                        elif member.type == "declaration":
                            # Constructor/destructor declarations
                            method = self._extract_method_declaration_correct(member, code, current_access)
                            if method and method.get("name"):
                                methods.append(method)
                                
                        elif member.type == "function_definition":
                            # Method implementations within class (rare but possible)
                            method = self._extract_method_definition(member, code, current_access)
                            if method and method.get("name"):
                                methods.append(method)
                                
                    except Exception as e:
                        logger.debug(f"Error processing class member: {e}")
                        continue

            return {
                "kind": "class",
                "name": name,
                "attributes": attributes,
                "methods": methods,
                "base_classes": base_classes,
                "access_levels": []
            }
        except Exception as e:
            logger.error(f"Error extracting class info: {e}")
            return None

    def _extract_method_from_field_declaration(self, node, code: str, access_level: str) -> dict:
        """FIXED: Extract method from field_declaration node"""
        try:
            declaration_text = self._get_code_segment(node, code).strip()
            
            name = ""
            parameters = []
            return_type = ""
            
            # Extract return type and function declarator
            for child in node.children:
                if child.type in ["primitive_type", "type_identifier", "qualified_identifier"]:
                    return_type = self._get_code_segment(child, code)
                elif child.type == "function_declarator":
                    # Extract name and parameters from function_declarator
                    for func_child in child.children:
                        if func_child.type == "field_identifier":  # Note: field_identifier, not identifier!
                            name = self._get_code_segment(func_child, code)
                        elif func_child.type == "parameter_list":
                            parameters = self._extract_parameters(func_child, code)
            
            return {
                "name": name,
                "parameters": parameters,
                "return_type": return_type,
                "access": access_level,
                "declaration": declaration_text
            }
        except Exception as e:
            logger.debug(f"Error extracting method from field_declaration: {e}")
            return None

    def _extract_method_declaration_correct(self, node, code: str, access_level: str) -> dict:
        """Extract method declaration from declaration node (for constructors/destructors)"""
        try:
            declaration_text = self._get_code_segment(node, code).strip()
            
            name = ""
            parameters = []
            return_type = ""
            
            # Navigate the tree-sitter AST structure correctly
            for child in node.children:
                if child.type == "function_declarator":
                    # This contains the function name and parameters
                    name, parameters = self._extract_from_function_declarator(child, code)
                elif child.type in ["primitive_type", "type_identifier", "qualified_identifier"]:
                    if not return_type:
                        return_type = self._get_code_segment(child, code)
            
            return {
                "name": name,
                "parameters": parameters,
                "return_type": return_type,
                "access": access_level,
                "declaration": declaration_text
            }
        except Exception as e:
            logger.debug(f"Error in method declaration extraction: {e}")
            return None

    def _extract_method_definition(self, node, code: str, access_level: str) -> dict:
        """Extract method definition (implementation)"""
        return self._extract_method_declaration_correct(node, code, access_level)

    def _extract_from_function_declarator(self, declarator_node, code: str) -> tuple:
        """Extract name and parameters from function_declarator node"""
        name = ""
        parameters = []
        
        try:
            for child in declarator_node.children:
                if child.type == "identifier":
                    name = self._get_code_segment(child, code)
                elif child.type == "destructor_name":
                    # Handle destructor names specially
                    name = self._get_code_segment(child, code)
                elif child.type == "parameter_list":
                    parameters = self._extract_parameters(child, code)
                elif child.type == "function_declarator":
                    # Handle nested declarators
                    nested_name, nested_params = self._extract_from_function_declarator(child, code)
                    if not name:
                        name = nested_name
                    if not parameters:
                        parameters = nested_params
        except Exception:
            pass
        
        return name, parameters

    def _extract_parameters(self, node, code: str) -> list:
        """Extract function/method parameters"""
        parameters = []
        
        try:
            for child in node.children:
                if child.type == "parameter_declaration":
                    param_text = self._get_code_segment(child, code).strip()
                    if param_text and param_text != ",":
                        parameters.append(param_text)
        except Exception:
            pass
        
        return parameters

    def _extract_field(self, node, code: str, access_level: str = "private") -> dict:
        """Extract field/attribute information"""
        try:
            field_type = ""
            field_name = ""
            declaration_text = self._get_code_segment(node, code)
            
            # Look for type and name
            for child in node.children:
                if child.type in ["primitive_type", "type_identifier", "qualified_identifier"]:
                    field_type = self._get_code_segment(child, code)
                elif child.type == "field_identifier":
                    field_name = self._get_code_segment(child, code)
                elif child.type == "init_declarator":
                    for subchild in child.children:
                        if subchild.type == "identifier":
                            field_name = self._get_code_segment(subchild, code)
            
            # Fallback regex extraction
            if not field_name and declaration_text:
                import re
                match = re.search(r'\b\w+\s+[\*&]*\s*(\w+)\s*[;=]', declaration_text)
                if match:
                    field_name = match.group(1)
            
            return {
                "name": field_name,
                "type": field_type,
                "access": access_level,
                "declaration": declaration_text
            }
        except Exception as e:
            logger.debug(f"Error extracting field: {e}")
            return None

    def _extract_struct_info(self, node, code: str) -> dict:
        """Extract struct information"""
        try:
            name = ""
            for child in node.children:
                if child.type == "type_identifier":
                    name = self._get_code_segment(child, code)
                    break
            
            return {
                "kind": "struct", 
                "name": name,
                "attributes": [],
                "methods": []
            }
        except Exception:
            return None

    def _extract_function_info(self, node, code: str) -> dict:
        """Extract standalone function information"""
        try:
            name = ""
            parameters = []
            return_type = ""
            
            for child in node.children:
                if child.type == "function_declarator":
                    name, parameters = self._extract_from_function_declarator(child, code)
                elif child.type in ["primitive_type", "type_identifier", "auto"]:
                    return_type = self._get_code_segment(child, code)

            return {
                "kind": "function",
                "name": name,
                "parameters": parameters,
                "return_type": return_type
            }
        except Exception:
            return None

    def _extract_namespace_info(self, node, code: str) -> dict:
        """Extract namespace information"""
        try:
            name = ""
            for child in node.children:
                if child.type == "identifier":
                    name = self._get_code_segment(child, code)
                    break
            
            return {
                "kind": "namespace",
                "name": name
            }
        except Exception:
            return None

    def _extract_enum_info(self, node, code: str) -> dict:
        """Extract enum information"""
        try:
            name = ""
            values = []
            
            for child in node.children:
                if child.type == "type_identifier":
                    name = self._get_code_segment(child, code)
                elif child.type == "enumerator_list":
                    for enum_child in child.children:
                        if enum_child.type == "enumerator":
                            for identifier in enum_child.children:
                                if identifier.type == "identifier":
                                    values.append(self._get_code_segment(identifier, code))
            
            return {
                "kind": "enum",
                "name": name,
                "values": values
            }
        except Exception:
            return None

    def _walk(self, node):
        """Walk AST nodes recursively with safety"""
        try:
            yield node
            for child in node.children:
                yield from self._walk(child)
        except Exception as e:
            logger.debug(f"Error in AST walk: {e}")
            return

    def _get_code_segment(self, node, code: str) -> str:
        """Get code segment from node safely"""
        try:
            return code[node.start_byte:node.end_byte].strip()
        except Exception:
            return ""