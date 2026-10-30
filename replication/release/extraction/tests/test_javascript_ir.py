from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


IMPLEMENTATION = Path(__file__).resolve().parents[1] / "implementation"
sys.path.insert(0, str(IMPLEMENTATION))

from app.extractors.javascript_extractor import JavaScriptTreeSitterExtractor
from app.utils.ir_extraction.postprocess_ir import postprocess_ir


class JavaScriptIRTests(unittest.TestCase):
    def test_unicode_offsets_exports_and_arrow_functions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.js").write_text(
                "// café — UTF-8 prefix\n"
                "import value from './dep.js';\n"
                "export class CaféService { run(input) { return value(input); } }\n"
                "export const transform = (item) => item + 1;\n",
                encoding="utf-8",
            )
            extractor = JavaScriptTreeSitterExtractor(str(root))
            raw = extractor.extract_ir()
            raw_path = root / "ir.json"
            extractor.save_ir(str(raw_path))
            processed_path = Path(postprocess_ir(str(raw_path)))
            processed = json.loads(processed_path.read_text(encoding="utf-8"))

            raw_elements = [element for file in raw["files"] for element in file["elements"]]
            self.assertEqual([e["name"] for e in raw_elements if e["kind"] == "class"], ["CaféService"])
            self.assertIn("transform", [e["name"] for e in raw_elements if e["kind"] == "variable_function"])
            self.assertEqual([e["name"] for e in processed["classes"]], ["CaféService"])
            self.assertEqual([e["name"] for e in processed["functions"]], ["transform"])

    def test_typescript_interface_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "contract.ts").write_text(
                "export interface Repository<T> { find(id: string): Promise<T>; }\n",
                encoding="utf-8",
            )
            extractor = JavaScriptTreeSitterExtractor(str(root))
            raw = extractor.extract_ir()
            elements = [element for file in raw["files"] for element in file["elements"]]
            interfaces = [e for e in elements if e["kind"] == "interface"]
            self.assertEqual(len(interfaces), 1)
            self.assertEqual(interfaces[0]["name"], "Repository")


if __name__ == "__main__":
    unittest.main()
