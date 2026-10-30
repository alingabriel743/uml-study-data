from pathlib import Path
import re
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_sql_parser
from app.utils.logger import get_logger
import hashlib


logger = get_logger(__name__)


class SqlTreeSitterExtractor(BaseIRExtractor):
    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_sql_parser()

    def extract_ir(self):
        # Filter to only include actual files, not directories
        sql_files = [f for f in Path(self.repo_path).rglob("*.sql") if f.is_file()]
        logger.info(f"Found {len(sql_files)} .sql files to parse.")

        seen_hashes = set()
        unique_files = []

        for file_path in sql_files:
            try:
                raw_code = self._read_file(file_path)
                file_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
                if file_hash not in seen_hashes:
                    seen_hashes.add(file_hash)
                    unique_files.append(file_path)
                else:
                    logger.warning(f"Duplicate SQL file (by content) skipped: {file_path}")
            except Exception as e:
                logger.error(f"Failed to read file {file_path}: {e}")

        logger.info(f"{len(unique_files)} unique SQL files retained after deduplication.")

        parsed_files = [self._parse_sql_file(file_path) for file_path in unique_files]
        parsed_files = [f for f in parsed_files if f and f.get("elements")]

        self.ir["metadata"]["sourceLanguages"].append("plsql")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in unique_files]
        self.ir["files"] = parsed_files

        return self.ir
    
    def _extract_type_definitions(self, raw_code: str) -> list:
        pattern = re.compile(
            r"create\s+or\s+replace\s+type\s+(\w+)\s+as\s+object\s*\((.*?)\)\s*;",
            re.IGNORECASE | re.DOTALL
        )

        types = []
        for match in pattern.finditer(raw_code):
            name, body = match.groups()
            attributes = []
            for line in body.split(','):
                line = re.sub(r'--.*', '', line).strip()
                if line:
                    parts = line.split()
                    if len(parts) >= 2:
                        attributes.append({
                            "name": parts[0],
                            "type": " ".join(parts[1:])
                        })

            types.append({
                "kind": "object_type",
                "name": name,
                "attributes": attributes
            })

        return types

    def _parse_sql_file(self, file_path: Path) -> dict:
        try:
            try:
                code = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                code = file_path.read_text(encoding="latin-1")

            elements = []

            if "create or replace package body" in code.lower():
                body_match = re.search(r"create\s+or\s+replace\s+package\s+body\s+(\w+)", code, re.IGNORECASE)
                package_name = body_match.group(1) if body_match else None
                elements.extend(self._extract_multiple_procedures(code, package_name=package_name))

            if "create or replace type" in code.lower():
                elements.extend(self._extract_type_definitions(code))
            
            if "create table" in code.lower():
                elements.extend(self._extract_create_table_definitions(code))

            tree = self.parser.parse(bytes(code, "utf-8"))
            root = tree.root_node
            elements.extend(self._extract_elements(root, code))

            return {
                "file": str(file_path),
                "language": "sql",
                "elements": elements
            }

        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
            return None


    def _read_file(self, file_path: Path) -> str:
        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return file_path.read_text(encoding="latin-1")

    def _extract_elements(self, root_node, code: str) -> list:
        elements = []
        for node in self._walk(root_node):
            if node.type == "create_function":
                func = self._extract_function_info(node, code)
                if func:
                    elements.append(func)

            elif node.type == "statement":
                statement_text = self._get_code_segment(node, code).strip().lower()

                # Fallback regex for procedures inside package bodies
                if "procedure" in statement_text and "as" in statement_text:
                    extracted = self._extract_multiple_procedures(statement_text)
                    elements.extend(extracted)

                elif any(kw in statement_text for kw in ("select", "insert", "update", "delete")):
                    stmt = self._extract_statement_info(node, code)
                    if stmt:
                        elements.append(stmt)
        return elements

    def _extract_function_info(self, node, code: str) -> dict:
        name = None
        parameters = []
        body = ""
        return_type = None
        collect_return = False

        for child in node.children:
            if child.type == "object_reference" and not name:
                name = self._get_code_segment(child, code)
            elif child.type == "function_arguments":
                param_text = self._get_code_segment(child, code)
                parameters = [p.strip() for p in param_text.strip("()").split(",") if p.strip()]
            elif child.type.lower() == "keyword_returns":
                collect_return = True
            elif collect_return:
                return_type = self._get_code_segment(child, code)
                collect_return = False
            elif child.type == "block" or child.type == "statement":
                body = self._get_code_segment(child, code)

        return {
            "kind": "function",
            "name": name,
            "parameters": parameters,
            "return_type": return_type,
            "body": body,
            "references": self._extract_table_references(body),
        }
    
    def _walk(self, node):
        if node.type.startswith("keyword_") or node.type in {";", ",", ".", "ERROR"}:
            return
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _extract_multiple_procedures(self, raw_code: str, package_name: str = None) -> list:
        pattern = re.compile(
            r"procedure\s+(\w+)\s*\((.*?)\)\s+as(.*?)end\s+\1\s*;",
            re.IGNORECASE | re.DOTALL
        )
        matches = pattern.findall(raw_code)
        procedures = []

        def classify_parameter(param: str) -> tuple[str, str]:
            param = param.strip()
            lower = param.lower()
            if " in out " in lower or lower.startswith("in out "):
                return "inout", param
            elif " out " in lower or lower.startswith("out "):
                return "out", param
            elif " in " in lower or lower.startswith("in "):
                return "in", param
            else:
                return "in", param

        for name, param_block, body in matches:
            in_params, out_params, inout_params = [], [], []

            param_block_clean = re.sub(r"--.*|/\*.*?\*/", "", param_block)
            param_lines = [p.strip() for p in param_block_clean.split(",") if p.strip()]
            for param in param_lines:
                mode, cleaned = classify_parameter(param)
                if mode == "in":
                    in_params.append(cleaned)
                elif mode == "out":
                    out_params.append(cleaned)
                elif mode == "inout":
                    inout_params.append(cleaned)

            full_body = f"procedure {name}({', '.join(param_lines)}) as {body.strip()} end {name};"

            procedure = {
                "kind": "procedure",
                "name": name,
                "in_parameters": in_params,
                "out_parameters": out_params,
                "inout_parameters": inout_params,
                "return_type": None,
                "body": full_body,
                "references": self._extract_table_references(full_body)
            }

            if package_name:
                procedure["package"] = package_name

            procedures.append(procedure)

        return procedures


    def _extract_type_info(self, node, code: str) -> dict:
        name = ""
        attributes = []

        for child in node.children:
            if child.type == "identifier" and not name:
                name = self._get_code_segment(child, code)

            elif child.type == "object_type_def":
                for attr in child.children:
                    if attr.type == "attribute_definition":
                        attr_name = ""
                        attr_type = ""

                        for sub in attr.children:
                            if sub.type == "identifier" and not attr_name:
                                attr_name = self._get_code_segment(sub, code)

                            elif sub.type == "keyword_type":
                                attr_type = self._get_code_segment(sub, code)

                            elif sub.type == "identifier" and attr_name:
                                attr_type += " " + self._get_code_segment(sub, code)

                        if attr_name and attr_type:
                            attributes.append({
                                "name": attr_name,
                                "type": attr_type.strip()
                            })

        return {
            "kind": "object_type",
            "name": name,
            "attributes": attributes
        }

    def _extract_statement_info(self, node, code: str) -> dict:
        raw_sql = self._get_code_segment(node, code).strip()
        if not raw_sql or len(raw_sql) < 5:
            return {}

        kind = "statement"
        for child in node.children:
            if child.type in {"select", "insert", "update", "delete"}:
                kind = child.type
                break

        return {
            "kind": kind,
            "raw_sql": raw_sql,
            "references": self._extract_table_references(raw_sql)
        }

    def _extract_package_spec(self, code: str) -> list:
        pattern = re.compile(
            r"create\s+or\s+replace\s+package\s+(\w+)\s+as(.*?)end\s+\1\s*;",
            re.IGNORECASE | re.DOTALL
        )
        matches = pattern.findall(code)
        packages = []

        for name, body in matches:
            procedure_sigs = re.findall(r"procedure\s+(\w+)\s*\((.*?)\);", body, re.IGNORECASE | re.DOTALL)
            procedures = []
            for proc_name, param_block in procedure_sigs:
                parameters = [re.sub(r"--.*|/\*.*?\*/", "", p).strip() for p in param_block.split(",")]
                procedures.append({
                    "name": proc_name,  
                    "parameters": parameters
                })

            packages.append({
                "kind": "package_spec",
                "name": name.strip(),
                "procedures": procedures,
                "raw": f"create or replace package {name} as {body.strip()} end {name};"
            })

        return packages
    
    def _extract_create_table_definitions(self, raw_code: str) -> list:
        pattern = re.compile(
            r"create\s+table\s+([\w\.]+)\s*\((.*?)\)\s*;",
            re.IGNORECASE | re.DOTALL
        )
        tables = []
        for match in pattern.finditer(raw_code):
            name, body = match.groups()
            columns = []
            for line in body.split(','):
                line = re.sub(r'--.*', '', line).strip()
                if line:
                    parts = line.split()
                    if len(parts) >= 2:
                        columns.append({
                            "name": parts[0],
                            "type": " ".join(parts[1:])
                        })
            tables.append({
                "kind": "table",
                "name": name,
                "columns": columns
            })
        return tables
    
    def _extract_table_references(self, sql: str) -> list:
        matches = re.findall(r'\b(from|join|update|into|merge\s+into)\s+([a-zA-Z0-9_\.]+)', sql, re.IGNORECASE)
        tables = [m[1] for m in matches if not m[1].lower().startswith("r_val")]
        return list({tbl.split('.')[-1].upper() for tbl in tables if tbl.upper() not in {"SELECT", "INTO", "FROM"}})

    def _get_code_segment(self, node, code: str) -> str:
        return code[node.start_byte:node.end_byte].strip()
