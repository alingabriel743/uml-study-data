# app/extractors/cobol_extractor.py

from pathlib import Path
import re
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_cobol_parser
from app.utils.logger import get_logger

logger = get_logger(__name__)

class CobolTreeSitterExtractor(BaseIRExtractor):
    def __init__(self, repo_path):
        super().__init__(repo_path)
        try:
            self.parser = get_cobol_parser()
        except Exception as e:
            logger.warning(f"Tree-sitter COBOL parser not available: {e}. Using regex fallback.")
            self.parser = None

    def extract_ir(self):
        # Filter to only include actual files, not directories
        cobol_files = [f for f in Path(self.repo_path).rglob("*.cob") if f.is_file()]
        cobol_files.extend([f for f in Path(self.repo_path).rglob("*.cbl") if f.is_file()])
        cobol_files.extend([f for f in Path(self.repo_path).rglob("*.cobol") if f.is_file()])
        cobol_files.extend([f for f in Path(self.repo_path).rglob("*.CBL") if f.is_file()])
        cobol_files.extend([f for f in Path(self.repo_path).rglob("*.COB") if f.is_file()])
        cobol_files.extend([f for f in Path(self.repo_path).rglob("*.COBOL") if f.is_file()])
        logger.info(f"Found {len(cobol_files)} COBOL files to parse.")

        parsed_files = []
        for file_path in cobol_files:
            parsed_file = self._parse_cobol_file(file_path)
            if parsed_file and parsed_file.get("elements"):
                parsed_files.append(parsed_file)

        self.ir["metadata"]["sourceLanguages"].append("cobol")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in cobol_files]
        self.ir["files"] = parsed_files

        logger.info(f"Successfully parsed {len(parsed_files)} COBOL files with {sum(len(f.get('elements', [])) for f in parsed_files)} elements")
        return self.ir

    def _parse_cobol_file(self, file_path: Path) -> dict:
        """Parse a single COBOL file with enhanced error handling"""
        try:
            try:
                code = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    code = file_path.read_text(encoding="latin-1")
                except UnicodeDecodeError:
                    code = file_path.read_text(encoding="cp1252")

            # Try tree-sitter first, fallback to regex
            if self.parser:
                try:
                    tree = self.parser.parse(bytes(code, "utf-8"))
                    root = tree.root_node
                    elements = self._extract_elements_tree_sitter(root, code)
                except Exception as e:
                    logger.warning(f"Tree-sitter parsing failed for {file_path}: {e}. Using regex fallback.")
                    elements = self._extract_elements_regex(code)
            else:
                elements = self._extract_elements_regex(code)
            
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
                    "language": "cobol",
                    "elements": valid_elements
                }
            else:
                logger.debug(f"No valid elements found in {file_path}")
                return None

        except Exception as e:
            logger.error(f"Error parsing COBOL file {file_path}: {e}")
            return None

    def _extract_elements_tree_sitter(self, root_node, code: str) -> list:
        """Extract COBOL elements using Tree-sitter (if available)"""
        elements = []
        
        try:
            for node in self._walk(root_node):
                element = None
                
                # COBOL program/subprogram
                if node.type in ["program_definition", "program"]:
                    element = self._extract_program_info(node, code)
                # COBOL paragraphs
                elif node.type in ["paragraph_definition", "paragraph"]:
                    element = self._extract_paragraph_info(node, code)
                # COBOL sections
                elif node.type in ["section_definition", "section"]:
                    element = self._extract_section_info(node, code)
                # COBOL data items
                elif node.type in ["data_description_entry", "data_item"]:
                    element = self._extract_data_item_info(node, code)
                
                if element:
                    elements.append(element)
                    
        except Exception as e:
            logger.error(f"Error extracting elements with tree-sitter: {e}")
            
        return elements

    def _extract_elements_regex(self, code: str) -> list:
        """Extract COBOL elements using regex patterns as fallback"""
        elements = []
        
        try:
            # Extract PROGRAM-ID
            program_match = self._find_program_id(code)
            if program_match:
                elements.append({
                    "kind": "program",
                    "name": program_match,
                    "attributes": [],
                    "methods": [],
                    "line_start": self._find_line_number(code, f"PROGRAM-ID.*{program_match}")
                })
            
            # Extract paragraphs
            paragraph_elements = self._extract_paragraphs_regex(code)
            elements.extend(paragraph_elements)
            
            # Extract sections
            section_elements = self._extract_sections_regex(code)
            elements.extend(section_elements)
            
            # Extract data items (working storage, file section, etc.)
            data_elements = self._extract_data_items_regex(code)
            elements.extend(data_elements)
            
        except Exception as e:
            logger.error(f"Error extracting elements with regex: {e}")
            
        return elements

    def _find_program_id(self, code: str) -> str:
        """Extract PROGRAM-ID from COBOL code"""
        patterns = [
            r'PROGRAM-ID\.\s+([A-Za-z0-9\-_]+)',
            r'PROGRAM-ID\s+([A-Za-z0-9\-_]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, code, re.IGNORECASE | re.MULTILINE)
            if match:
                return match.group(1).strip()
        
        return None

    def _extract_paragraphs_regex(self, code: str) -> list:
        """Extract COBOL paragraphs using regex"""
        elements = []
        
        # Match paragraph names (labels followed by a period)
        # COBOL paragraphs are typically alphanumeric names followed by a period
        paragraph_pattern = r'^[ ]{0,6}([A-Za-z][A-Za-z0-9\-]*)\s*\.\s*$'
        
        lines = code.split('\n')
        for i, line in enumerate(lines, 1):
            match = re.match(paragraph_pattern, line)
            if match:
                para_name = match.group(1)
                
                # Skip common COBOL reserved words that aren't paragraphs
                reserved_words = {
                    'IDENTIFICATION', 'ENVIRONMENT', 'DATA', 'PROCEDURE',
                    'WORKING-STORAGE', 'FILE', 'LINKAGE', 'LOCAL-STORAGE'
                }
                
                if para_name.upper() not in reserved_words:
                    elements.append({
                        "kind": "paragraph",
                        "name": para_name,
                        "attributes": [],
                        "methods": [],
                        "line_start": i
                    })
        
        return elements

    def _extract_sections_regex(self, code: str) -> list:
        """Extract COBOL sections using regex"""
        elements = []
        
        # Match section definitions
        section_patterns = [
            r'^[ ]{0,6}([A-Za-z][A-Za-z0-9\-]*)\s+SECTION\s*\.',
            r'^[ ]{0,6}(WORKING-STORAGE|FILE|LINKAGE|LOCAL-STORAGE)\s+SECTION\s*\.'
        ]
        
        for pattern in section_patterns:
            matches = re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                section_name = match.group(1)
                line_num = self._find_line_number(code, match.group(0))
                
                elements.append({
                    "kind": "section",
                    "name": section_name,
                    "attributes": [],
                    "methods": [],
                    "line_start": line_num
                })
        
        return elements

    def _extract_data_items_regex(self, code: str) -> list:
        """Extract COBOL data items using regex"""
        elements = []
        
        # Match data item definitions (level numbers 01-49, 66, 77, 88)
        data_patterns = [
            r'^[ ]{0,6}(0[1-9]|[1-4][0-9]|66|77|88)\s+([A-Za-z][A-Za-z0-9\-]*)',
        ]
        
        for pattern in data_patterns:
            matches = re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                level_num = match.group(1)
                data_name = match.group(2)
                line_num = self._find_line_number(code, match.group(0))
                
                # Determine data item type based on level
                if level_num == '01':
                    kind = "record"
                elif level_num == '77':
                    kind = "elementary_item"
                elif level_num == '88':
                    kind = "condition"
                elif level_num == '66':
                    kind = "rename_item"
                else:
                    kind = "data_item"
                
                elements.append({
                    "kind": kind,
                    "name": data_name,
                    "attributes": [{"declaration": f"{level_num} {data_name}", "level": level_num}],
                    "methods": [],
                    "line_start": line_num
                })
        
        return elements

    def _extract_program_info(self, node, code: str) -> dict:
        """Extract program information using tree-sitter"""
        name = ""
        try:
            # Try to find program name from children
            for child in node.children:
                if child.type in ["identifier", "program_name"]:
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                        break
            
            if not name:
                # Fallback to regex
                name = self._find_program_id(code) or "UnknownProgram"
            
            return {
                "kind": "program",
                "name": name,
                "attributes": [],
                "methods": []
            }
        except Exception as e:
            logger.debug(f"Error extracting program info: {e}")
            return None

    def _extract_paragraph_info(self, node, code: str) -> dict:
        """Extract paragraph information using tree-sitter"""
        name = ""
        try:
            for child in node.children:
                if child.type in ["identifier", "paragraph_name"]:
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                        break
            
            if name:
                return {
                    "kind": "paragraph",
                    "name": name,
                    "attributes": [],
                    "methods": []
                }
        except Exception as e:
            logger.debug(f"Error extracting paragraph info: {e}")
        return None

    def _extract_section_info(self, node, code: str) -> dict:
        """Extract section information using tree-sitter"""
        name = ""
        try:
            for child in node.children:
                if child.type in ["identifier", "section_name"]:
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                        break
            
            if name:
                return {
                    "kind": "section",
                    "name": name,
                    "attributes": [],
                    "methods": []
                }
        except Exception as e:
            logger.debug(f"Error extracting section info: {e}")
        return None

    def _extract_data_item_info(self, node, code: str) -> dict:
        """Extract data item information using tree-sitter"""
        name = ""
        level = ""
        try:
            declaration = self._get_code_segment(node, code)
            
            for child in node.children:
                if child.type in ["identifier", "data_name"]:
                    candidate_name = self._get_code_segment(child, code)
                    if candidate_name and candidate_name.strip():
                        name = candidate_name.strip()
                elif child.type in ["level_number"]:
                    level = self._get_code_segment(child, code).strip()
            
            if name:
                # Determine kind based on level
                kind = "data_item"
                if level == "01":
                    kind = "record"
                elif level == "77":
                    kind = "elementary_item"
                elif level == "88":
                    kind = "condition"
                elif level == "66":
                    kind = "rename_item"
                
                return {
                    "kind": kind,
                    "name": name,
                    "attributes": [{"declaration": declaration, "level": level}],
                    "methods": []
                }
        except Exception as e:
            logger.debug(f"Error extracting data item info: {e}")
        return None

    def _find_line_number(self, code: str, search_text: str) -> int:
        """Find line number for a given text pattern"""
        try:
            lines = code.split('\n')
            for i, line in enumerate(lines, 1):
                if re.search(search_text, line, re.IGNORECASE):
                    return i
        except Exception:
            pass
        return 1

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
