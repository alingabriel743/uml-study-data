from pathlib import Path
from app.extractors.base_extractor import BaseIRExtractor
from app.utils.ir_extraction.tree_sitter_helper import get_perl_parser
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PerlTreeSitterExtractor(BaseIRExtractor):
    """
    Tree-sitter based extractor for Perl source files (.pl, .pm).
    Extracts packages (modules), subroutines, and package-level variables.
    Ported from v1 with v2 conventions.
    """

    def __init__(self, repo_path):
        super().__init__(repo_path)
        self.parser = get_perl_parser()

    def extract_ir(self):
        perl_files = [
            f for f in Path(self.repo_path).rglob("*.pl") if f.is_file()
        ] + [
            f for f in Path(self.repo_path).rglob("*.pm") if f.is_file()
        ]
        logger.info(f"Found {len(perl_files)} Perl files to parse.")

        parsed_files = []
        for file_path in perl_files:
            try:
                parsed_file = self._parse_perl_file(file_path)
                if parsed_file:
                    parsed_files.append(parsed_file)
                    logger.debug(
                        f"Successfully parsed {file_path}: "
                        f"{len(parsed_file.get('elements', []))} elements"
                    )
                else:
                    logger.warning(f"Failed to parse {file_path}: returned None")
            except Exception as e:
                logger.error(f"Exception parsing {file_path}: {e}")

        filtered_files = [f for f in parsed_files if f and f.get("elements")]
        logger.info(
            f"Parsed {len(parsed_files)} files, {len(filtered_files)} have elements"
        )

        self.ir["metadata"]["sourceLanguages"].append("perl")
        self.ir["metadata"]["sourceFiles"] = [str(f) for f in perl_files]
        self.ir["files"] = filtered_files

        return self.ir

    # ------------------------------------------------------------------
    # File-level parsing
    # ------------------------------------------------------------------

    def _parse_perl_file(self, file_path: Path) -> dict:
        try:
            code = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            code = file_path.read_text(encoding="latin-1")

        tree = self.parser.parse(bytes(code, "utf-8"))
        root = tree.root_node

        elements = self._extract_elements(root, code)

        return {
            "file": str(file_path),
            "language": "perl",
            "elements": elements,
        }

    # ------------------------------------------------------------------
    # Element extraction
    # ------------------------------------------------------------------

    def _extract_elements(self, root_node, code: str) -> list:
        elements = []

        packages = self._collect_packages(root_node, code)

        if packages:
            for pkg in packages:
                elements.append(pkg)
        else:
            subs = self._collect_subroutines(root_node, code, inside_package=None)
            variables = self._collect_package_variables(root_node, code)
            if subs or variables:
                elements.append(
                    {
                        "kind": "package",
                        "name": "main",
                        "methods": subs,
                        "attributes": variables,
                    }
                )

        if not elements:
            elements.append(
                {
                    "kind": "file",
                    "name": "procedural_code",
                    "attributes": [],
                    "methods": [],
                    "code_snippet": code[:500] if len(code) > 500 else code,
                }
            )

        return elements

    # ------------------------------------------------------------------
    # Package collection
    # ------------------------------------------------------------------

    def _collect_packages(self, root_node, code: str) -> list:
        packages = []
        current_pkg_name = None
        current_pkg_subs = []
        current_pkg_vars = []

        for node in self._walk(root_node):
            if node.type == "package_statement":
                if current_pkg_name is not None:
                    packages.append(
                        {
                            "kind": "package",
                            "name": current_pkg_name,
                            "methods": current_pkg_subs,
                            "attributes": current_pkg_vars,
                        }
                    )
                current_pkg_name = self._extract_package_name(node, code)
                current_pkg_subs = []
                current_pkg_vars = []

        if current_pkg_name is not None:
            for node in self._walk(root_node):
                if node.type == "function_definition":
                    sub_info = self._extract_subroutine(node, code)
                    if sub_info and sub_info not in current_pkg_subs:
                        current_pkg_subs.append(sub_info)
                elif node.type in (
                    "variable_declaration",
                    "my_declaration",
                    "our_declaration",
                ):
                    var_info = self._extract_variable(node, code)
                    if var_info and var_info not in current_pkg_vars:
                        current_pkg_vars.append(var_info)

            packages.append(
                {
                    "kind": "package",
                    "name": current_pkg_name,
                    "methods": current_pkg_subs,
                    "attributes": current_pkg_vars,
                }
            )

        return packages

    def _extract_package_name(self, node, code: str) -> str:
        for child in node.children:
            if child.type in ("package_name", "identifier", "name"):
                return self._get_code_segment(child, code)
        raw = self._get_code_segment(node, code)
        parts = raw.split()
        if len(parts) >= 2:
            return parts[1].rstrip(";")
        return "unknown"

    # ------------------------------------------------------------------
    # Subroutine collection
    # ------------------------------------------------------------------

    def _collect_subroutines(self, root_node, code: str, inside_package) -> list:
        subs = []
        for node in self._walk(root_node):
            if node.type == "function_definition":
                sub_info = self._extract_subroutine(node, code)
                if sub_info:
                    subs.append(sub_info)
        return subs

    def _extract_subroutine(self, node, code: str) -> dict:
        name = ""
        parameters = []

        for child in node.children:
            if child.type in ("name", "identifier"):
                name = self._get_code_segment(child, code)
            elif child.type in ("prototype", "signature", "parameter_list"):
                parameters = self._extract_parameters(child, code)

        if not name:
            return None

        return {
            "name": name,
            "parameters": parameters,
        }

    def _extract_parameters(self, node, code: str) -> list:
        params = []
        for child in node.children:
            if child.type in ("scalar", "array", "hash", "identifier", "variable"):
                param_text = self._get_code_segment(child, code)
                if param_text and param_text not in ("(", ")", ","):
                    params.append(param_text)
            elif child.type == "variable_declaration":
                param_text = self._get_code_segment(child, code)
                if param_text:
                    params.append(param_text)
        return params

    # ------------------------------------------------------------------
    # Package-level variable collection
    # ------------------------------------------------------------------

    def _collect_package_variables(self, root_node, code: str) -> list:
        variables = []
        for node in self._walk(root_node):
            if node.type in (
                "variable_declaration",
                "my_declaration",
                "our_declaration",
            ):
                var_info = self._extract_variable(node, code)
                if var_info:
                    variables.append(var_info)
        return variables

    def _extract_variable(self, node, code: str) -> dict:
        declaration = self._get_code_segment(node, code)
        name = ""
        for child in node.children:
            if child.type in ("scalar", "array", "hash", "variable", "identifier"):
                name = self._get_code_segment(child, code)
                break
        if not name:
            name = declaration.split("=")[0].strip()

        return {"name": name, "declaration": declaration}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _walk(self, node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _get_code_segment(self, node, code: str) -> str:
        return code[node.start_byte: node.end_byte].strip()
