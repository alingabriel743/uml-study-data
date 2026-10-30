from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


IMPLEMENTATION = Path(__file__).resolve().parents[1] / "implementation"
sys.path.insert(0, str(IMPLEMENTATION))

from app.extractors.java_extractor import JavaTreeSitterExtractor
from app.extractors.php_extractor import PHPTreeSitterExtractor
from app.extractors.python_extractor import PythonTreeSitterExtractor
from app.extractors.source_discovery import discover_source_files
from app.utils.ir_extraction.postprocess_ir import postprocess_ir


def process(extractor, root: Path) -> tuple[dict, dict]:
    raw = extractor(str(root)).extract_ir()
    raw_path = root / "ir.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    processed_path = Path(postprocess_ir(str(raw_path)))
    return raw, json.loads(processed_path.read_text(encoding="utf-8"))


class MultilanguageIRTests(unittest.TestCase):
    def test_source_discovery_excludes_nonproduction_and_bundled_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / ".agents").mkdir()
            (root / "src" / "main.js").write_text("export const run = () => 1;", encoding="utf-8")
            (root / "src" / "vendor.umd.js").write_text("var bundled = true;", encoding="utf-8")
            (root / "tests" / "main.test.js").write_text("test('x', () => {});", encoding="utf-8")
            (root / ".agents" / "copy.js").write_text("var copy = true;", encoding="utf-8")
            found = [path.relative_to(root).as_posix() for path in discover_source_files(root, ("*.js",))]
            self.assertEqual(found, ["src/main.js"])

    def test_java_unicode_and_declaration_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Sample.java").write_text(
                "// café — prefix\n"
                "interface Contract { void execute(); }\n"
                "enum State { READY, DONE }\n"
                "class Service implements Contract { public void execute() {} }\n",
                encoding="utf-8",
            )
            _, processed = process(JavaTreeSitterExtractor, root)
            found = {(item["kind"], item["name"]) for item in processed["classes"]}
            self.assertIn(("interface", "Contract"), found)
            self.assertIn(("enum", "State"), found)
            self.assertIn(("class", "Service"), found)

    def test_python_functions_are_not_double_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.py").write_text(
                "# café — prefix\nclass Service:\n    def run(self):\n        return 1\n\ndef helper(value: int) -> str:\n    return str(value)\n",
                encoding="utf-8",
            )
            _, processed = process(PythonTreeSitterExtractor, root)
            self.assertEqual([x["name"] for x in processed["classes"]], ["Service"])
            self.assertEqual([x["name"] for x in processed["functions"]], ["helper"])

    def test_php_functions_are_not_double_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.php").write_text(
                "<?php // café — prefix\nclass Service { public function run() {} }\nfunction helper($value) { return $value; }\n",
                encoding="utf-8",
            )
            _, processed = process(PHPTreeSitterExtractor, root)
            self.assertEqual([x["name"] for x in processed["classes"]], ["Service"])
            self.assertEqual([x["name"] for x in processed["functions"]], ["helper"])


if __name__ == "__main__":
    unittest.main()
