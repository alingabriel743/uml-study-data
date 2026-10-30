# app/extractors/kotlin_extractor.py

from pathlib import Path
import re
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_kotlin_parser
from app.utils.logger import get_logger

logger = get_logger(__name__)

class KotlinTreeSitterExtractor(BaseIRExtractor):
    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_kotlin_parser()

    def extract_ir(self):
        # Filter to only include actual files, not directories
        kotlin_files = [f for f in Path(self.repo_path).rglob("*.kt") if f.is_file()]
        kotlin_files.extend([f for f in Path(self.repo_path).rglob("*.kts") if f.is_file()])  # Include Kotlin scripts
        logger.info(f"Found {len(kotlin_files)} Kotlin files to parse.")

        parsed_files = []
        for file_path in kotlin_files:
            parsed_file = self._parse_kotlin_file(file_path)
            if parsed_file and parsed_file.get("elements"):
                parsed_files.append(parsed_file)

        self.ir["metadata"]["sourceLanguages"].append("kotlin")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in kotlin_files]
        self.ir["files"] = parsed_files

        logger.info(f"Successfully parsed {len(parsed_files)} Kotlin files with {sum(len(f.get('elements', [])) for f in parsed_files)} elements")
        return self.ir

    def _parse_kotlin_file(self, file_path: Path) -> dict:
        """Parse a single Kotlin file with enhanced error handling"""
        try:
            try:
                code = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                code = file_path.read_text(encoding="latin-1")

            tree = self.parser.parse(bytes(code, "utf-8"))
            root = tree.root_node

            elements = self._extract_elements(root, code)
            
            # Filter out elements with empty names
            valid_elements = []
            for element in elements:
                if element and element.get("name") and element.get("name").strip():
                    valid_elements.append(element)
                else:
                    logger.debug(f"Filtered out element with empty name in {file_path}: {element.get('kind', 'unknown') if element else 'None'}")

            if valid_elements:
                return {
                    "file": str(file_path),
                    "language": "kotlin",
                    "elements": valid_elements
                }
            else:
                logger.debug(f"No valid elements found in {file_path}")
                return None

        except Exception as e:
            logger.error(f"Error parsing Kotlin file {file_path}: {e}")
            return None

    def _extract_elements(self, root_node, code: str) -> list:
        """Extract all Kotlin elements from the AST"""
        elements = []
        
        try:
            for node in self._walk(root_node):
                element = None
                
                # Kotlin class declarations
                if node.type == "class_declaration":
                    element = self._extract_class_info(node, code)
                # Kotlin object declarations (singletons)
                elif node.type == "object_declaration":
                    element = self._extract_object_info(node, code)
                # Kotlin interface declarations
                elif node.type == "interface_declaration":
                    element = self._extract_interface_info(node, code)
                # Top-level functions
                elif node.type == "function_declaration":
                    # Only extract top-level functions (not inside classes)
                    if self._is_top_level_function(node):
                        element = self._extract_function_info(node, code)
                        if element:
                            element["kind"] = "function"  # Mark as top-level function
                
                if element:
                    elements.append(element)
                    
        except Exception as e:
            logger.error(f"Error extracting elements: {e}")
            
        return elements

    def _extract_class_info(self, node, code: str) -> dict:
        """Extract class information with robust name parsing"""
        name = ""
        attributes = []
        methods = []
        is_data_class = False
        is_sealed_class = False
        is_enum_class = False
        primary_constructor_params = []

        try:
            # First pass: extract basic info using TreeSitter
            for child in node.children:
                if child.type == "modifiers":
                    modifiers_text = self._get_code_segment(child, code)
                    is_data_class = "data" in modifiers_text
                    is_sealed_class = "sealed" in modifiers_text
                    is_enum_class = "enum" in modifiers_text
                elif child.type == "simple_identifier":
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                        logger.debug(f"Extracted class name via TreeSitter: '{name}'")
                elif child.type == "primary_constructor":
                    primary_constructor_params = self._extract_primary_constructor(child, code)
                elif child.type == "class_body":
                    for item in child.children:
                        if item.type == "property_declaration":
                            prop = self._extract_property(item, code)
                            if prop:
                                attributes.append(prop)
                        elif item.type == "function_declaration":
                            method = self._extract_function_info(item, code)
                            if method:
                                methods.append(method)
                        elif item.type == "init_block":
                            # Kotlin init blocks
                            init_info = {
                                "name": "init", 
                                "type": "initializer", 
                                "declaration": self._get_code_segment(item, code),
                                "visibility": "public",
                                "is_suspend": False,
                                "is_inline": False
                            }
                            methods.append(init_info)

            # CRITICAL: Fallback name extraction if TreeSitter failed
            if not name or not name.strip():
                name = self._extract_name_from_declaration(node, code, "class")
                if name:
                    logger.info(f"Extracted class name via regex: '{name}'")

            # Final fallback: use line number if still no name
            if not name or not name.strip():
                start_line = getattr(node, 'start_point', [0])[0] if hasattr(node, 'start_point') else 0
                name = f"UnnamedClass_Line{start_line}"
                logger.warning(f"Using fallback class name: '{name}'")

            # For data classes, primary constructor parameters are also properties
            if is_data_class and primary_constructor_params:
                attributes.extend(primary_constructor_params)

            # Determine class type
            class_type = "class"
            if is_data_class:
                class_type = "data_class"
            elif is_sealed_class:
                class_type = "sealed_class"
            elif is_enum_class:
                class_type = "enum_class"

            return {
                "kind": class_type,
                "name": name.strip(),
                "attributes": attributes,
                "methods": methods,
                "primary_constructor": primary_constructor_params if not is_data_class else []
            }

        except Exception as e:
            logger.error(f"Error extracting class info: {e}")
            return None

    def _extract_object_info(self, node, code: str) -> dict:
        """Extract object (singleton) information"""
        name = ""
        attributes = []
        methods = []

        try:
            for child in node.children:
                if child.type == "simple_identifier":
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                elif child.type == "class_body":
                    for item in child.children:
                        if item.type == "property_declaration":
                            prop = self._extract_property(item, code)
                            if prop:
                                attributes.append(prop)
                        elif item.type == "function_declaration":
                            method = self._extract_function_info(item, code)
                            if method:
                                methods.append(method)

            # Fallback name extraction
            if not name or not name.strip():
                name = self._extract_name_from_declaration(node, code, "object")
                if name:
                    logger.info(f"Extracted object name via regex: '{name}'")

            if not name or not name.strip():
                start_line = getattr(node, 'start_point', [0])[0] if hasattr(node, 'start_point') else 0
                name = f"UnnamedObject_Line{start_line}"
                logger.warning(f"Using fallback object name: '{name}'")

            return {
                "kind": "object",
                "name": name.strip(),
                "attributes": attributes,
                "methods": methods
            }

        except Exception as e:
            logger.error(f"Error extracting object info: {e}")
            return None

    def _extract_interface_info(self, node, code: str) -> dict:
        """Extract interface information"""
        name = ""
        methods = []

        try:
            for child in node.children:
                if child.type == "simple_identifier":
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                elif child.type == "class_body":
                    for item in child.children:
                        if item.type == "function_declaration":
                            method = self._extract_function_info(item, code)
                            if method:
                                methods.append(method)

            # Fallback name extraction
            if not name or not name.strip():
                name = self._extract_name_from_declaration(node, code, "interface")
                if name:
                    logger.info(f"Extracted interface name via regex: '{name}'")

            if not name or not name.strip():
                start_line = getattr(node, 'start_point', [0])[0] if hasattr(node, 'start_point') else 0
                name = f"UnnamedInterface_Line{start_line}"
                logger.warning(f"Using fallback interface name: '{name}'")

            return {
                "kind": "interface",
                "name": name.strip(),
                "attributes": [],
                "methods": methods
            }

        except Exception as e:
            logger.error(f"Error extracting interface info: {e}")
            return None

    def _extract_name_from_declaration(self, node, code: str, element_type: str) -> str:
        """Extract name using regex as fallback when TreeSitter fails"""
        try:
            declaration_text = self._get_code_segment(node, code)
            
            # Pattern to match Kotlin declarations
            patterns = {
                "class": r'(?:data\s+|sealed\s+|enum\s+|inner\s+|abstract\s+)*class\s+([A-Za-z_][A-Za-z0-9_]*)',
                "object": r'object\s+([A-Za-z_][A-Za-z0-9_]*)',
                "interface": r'interface\s+([A-Za-z_][A-Za-z0-9_]*)',
                "function": r'fun\s+([A-Za-z_][A-Za-z0-9_]*)\s*\('
            }
            
            pattern = patterns.get(element_type)
            if pattern:
                match = re.search(pattern, declaration_text, re.MULTILINE)
                if match:
                    return match.group(1).strip()
            
        except Exception as e:
            logger.debug(f"Regex name extraction failed for {element_type}: {e}")
            
        return ""

    def _extract_primary_constructor(self, node, code: str) -> list:
        """Extract primary constructor parameters"""
        params = []
        try:
            for child in node.children:
                if child.type == "class_parameters":
                    for param_node in child.children:
                        if param_node.type == "class_parameter":
                            param_info = self._extract_class_parameter(param_node, code)
                            if param_info:
                                params.append(param_info)
        except Exception as e:
            logger.debug(f"Error extracting primary constructor: {e}")
        return params

    def _extract_class_parameter(self, node, code: str) -> dict:
        """Extract class parameter (constructor parameter)"""
        name = ""
        param_type = ""
        is_val = False
        is_var = False
        
        try:
            for child in node.children:
                if child.type == "modifiers":
                    modifier_text = self._get_code_segment(child, code)
                    is_val = "val" in modifier_text
                    is_var = "var" in modifier_text
                elif child.type == "simple_identifier":
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                elif child.type in ["user_type", "type_identifier"]:
                    candidate_type = self._get_code_segment(child, code)
                    if candidate_type and candidate_type.strip():
                        param_type = candidate_type.strip()

            # Only include as property if it's val or var
            if (is_val or is_var) and name:
                return {
                    "name": name,
                    "type": param_type,
                    "declaration": f"{'val' if is_val else 'var'} {name}: {param_type}",
                    "is_constructor_property": True,
                    "is_mutable": is_var,
                    "visibility": "public"
                }
                
        except Exception as e:
            logger.debug(f"Error extracting class parameter: {e}")
            
        return None

    def _extract_property(self, node, code: str) -> dict:
        """Extract property with robust name and type parsing"""
        name = ""
        prop_type = ""
        is_val = True  # Default to val
        visibility = "public"  # Default visibility
        
        try:
            declaration_text = self._get_code_segment(node, code)
            
            # Try TreeSitter first
            for child in node.children:
                if child.type == "modifiers":
                    modifier_text = self._get_code_segment(child, code)
                    is_val = "val" in modifier_text or "var" not in modifier_text  # Default to val
                    if "private" in modifier_text:
                        visibility = "private"
                    elif "protected" in modifier_text:
                        visibility = "protected"
                    elif "internal" in modifier_text:
                        visibility = "internal"
                elif child.type == "simple_identifier":
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                elif child.type in ["user_type", "type_identifier"]:
                    candidate_type = self._get_code_segment(child, code)
                    if candidate_type and candidate_type.strip():
                        prop_type = candidate_type.strip()

            # Fallback: parse declaration text with regex
            if not name or not name.strip():
                prop_match = re.search(r'(?:private\s+|protected\s+|internal\s+)?(?:lateinit\s+)?(val|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^=\n]+))?', declaration_text)
                if prop_match:
                    is_val = prop_match.group(1) == "val"
                    name = prop_match.group(2).strip()
                    if prop_match.group(3):
                        prop_type = prop_match.group(3).strip()
                    
                    # Extract visibility from full text
                    if "private" in declaration_text:
                        visibility = "private"
                    elif "protected" in declaration_text:
                        visibility = "protected"
                    elif "internal" in declaration_text:
                        visibility = "internal"
                        
                    logger.debug(f"Extracted property via regex: name='{name}', type='{prop_type}'")

            # Only return if we have a valid name
            if name and name.strip():
                return {
                    "name": name.strip(),
                    "type": prop_type.strip(),
                    "declaration": declaration_text,
                    "is_mutable": not is_val,
                    "visibility": visibility
                }
            else:
                logger.debug(f"Skipping property with empty name: {declaration_text[:50]}...")
                
        except Exception as e:
            logger.debug(f"Error extracting property: {e}")
            
        return None

    def _extract_function_info(self, node, code: str) -> dict:
        """Extract function with robust name parsing"""
        name = ""
        parameters = []
        return_type = ""
        visibility = "public"  # Default visibility
        is_suspend = False
        is_inline = False
        
        try:
            declaration_text = self._get_code_segment(node, code)
            
            # Try TreeSitter first
            for child in node.children:
                if child.type == "modifiers":
                    modifier_text = self._get_code_segment(child, code)
                    if "private" in modifier_text:
                        visibility = "private"
                    elif "protected" in modifier_text:
                        visibility = "protected"
                    elif "internal" in modifier_text:
                        visibility = "internal"
                    is_suspend = "suspend" in modifier_text
                    is_inline = "inline" in modifier_text
                elif child.type == "simple_identifier":
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                elif child.type == "function_value_parameters":
                    parameters = self._extract_function_parameters(child, code)
                elif child.type in ["user_type", "type_identifier"]:
                    candidate_type = self._get_code_segment(child, code)
                    if candidate_type and candidate_type.strip():
                        return_type = candidate_type.strip()

            # Fallback: extract name with regex
            if not name or not name.strip():
                func_match = re.search(r'(?:private\s+|protected\s+|internal\s+)?(?:suspend\s+)?(?:inline\s+)?fun\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', declaration_text)
                if func_match:
                    name = func_match.group(1).strip()
                    logger.debug(f"Extracted function name via regex: '{name}'")

            # Extract modifiers from declaration text as fallback
            if "private" in declaration_text:
                visibility = "private"
            elif "protected" in declaration_text:
                visibility = "protected"
            elif "internal" in declaration_text:
                visibility = "internal"
            
            is_suspend = is_suspend or "suspend" in declaration_text
            is_inline = is_inline or "inline" in declaration_text

            # Only return if we have a valid name
            if name and name.strip():
                return {
                    "name": name.strip(),
                    "parameters": parameters,
                    "return_type": return_type.strip(),
                    "visibility": visibility,
                    "is_suspend": is_suspend,
                    "is_inline": is_inline,
                    "declaration": declaration_text
                }
            else:
                logger.debug(f"Skipping function with empty name: {declaration_text[:50]}...")
                
        except Exception as e:
            logger.debug(f"Error extracting function: {e}")
            
        return None

    def _extract_function_parameters(self, node, code: str) -> list:
        """Extract function parameters"""
        parameters = []
        try:
            for child in node.children:
                if child.type == "parameter":
                    param_info = self._extract_parameter(child, code)
                    if param_info:
                        parameters.append(param_info)
        except Exception as e:
            logger.debug(f"Error extracting function parameters: {e}")
        return parameters

    def _extract_parameter(self, node, code: str) -> dict:
        """Extract individual parameter"""
        name = ""
        param_type = ""
        has_default = False
        
        try:
            for child in node.children:
                if child.type == "simple_identifier":
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                elif child.type in ["user_type", "type_identifier"]:
                    candidate_type = self._get_code_segment(child, code)
                    if candidate_type and candidate_type.strip():
                        param_type = candidate_type.strip()
                elif child.type == "default_value":
                    has_default = True

            if name:
                return {
                    "name": name,
                    "type": param_type,
                    "has_default": has_default,
                    "declaration": self._get_code_segment(node, code)
                }
                
        except Exception as e:
            logger.debug(f"Error extracting parameter: {e}")
            
        return None

    def _is_top_level_function(self, node) -> bool:
        """Check if a function is at the top level (not inside a class)"""
        try:
            parent = node.parent
            while parent:
                if parent.type in ["class_declaration", "object_declaration", "interface_declaration"]:
                    return False
                parent = parent.parent
            return True
        except Exception:
            return False

    def _walk(self, node):
        """Walk the AST tree"""
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _get_code_segment(self, node, code: str) -> str:
        """Get code segment for a node"""
        try:
            return code[node.start_byte:node.end_byte].strip()
        except Exception as e:
            logger.debug(f"Error getting code segment: {e}")
            return ""

    def debug_node_structure(self, node, code: str, depth=0, max_depth=3):
        """Debug helper to see actual Kotlin AST structure"""
        if depth > max_depth:
            return
            
        try:
            indent = "  " * depth
            node_text = self._get_code_segment(node, code)
            # Limit text length for readability
            display_text = node_text[:80] + "..." if len(node_text) > 80 else node_text
            display_text = display_text.replace('\n', '\\n')
            
            print(f"{indent}{node.type}: '{display_text}'")
            
            for child in node.children:
                self.debug_node_structure(child, code, depth + 1, max_depth)
        except Exception as e:
            print(f"{indent}Error debugging node: {e}")
