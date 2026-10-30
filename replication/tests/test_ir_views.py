"""Pytest-compatible tests; also runnable without third-party dependencies.

Run: python3 -B -m unittest discover -s tests -p test_ir_views.py -v
Measured telemetry necessarily varies; byte determinism uses a fixed clock.
"""

import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from app.agent import ir_views


TYPES = tuple(ir_views.ENTRY_KEYS)


def sample_ir() -> dict:
    return {
        "classes": [
            {"name": "ATestHelper", "source_file": "aaa/tests/helper.py", "methods": [{"name": "test"}]},
            {"name": "OrderController", "source_file": "app/api/controller.py",
             "methods": [{"name": "submit", "calls": ["OrderService.save"]}], "extends": "BaseModel"},
            {"name": "OrderService", "source_file": "app/core/service.py",
             "methods": [{"name": "save", "references": ["db.save", "notify"]}], "attributes": [{"name": "db"}]},
            {"name": "BaseModel", "source_file": "app/core/model.py"},
            {"name": "EmailGateway", "source_file": "app/io/email.py"},
            {"name": "CheckoutWorkflow", "source_file": "app/core/workflow.py"},
        ],
        "functions": [{"name": "process", "source_file": "app/run.py", "parameters": ["request"],
                       "invocations": ["load", "save", "notify"]}],
        "metadata": {"projectName": "Orders", "sourceLanguages": ["Python", "JavaScript"],
                     "sourceFiles": ["aaa/tests/helper.py", "app/core/service.py", "ops/docker-compose.yml", "app/run.py"]},
    }


class IRViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.huge_dir = tempfile.TemporaryDirectory()
        cls.huge_path = Path(cls.huge_dir.name) / "huge_ir.json"
        # Long Unicode names exercise UTF-8 byte accounting, not character counts.
        methods = [{"name": "execute", "parameters": ["request:" + "T" * 180],
                    "calls": ["load", "save"]}, {"name": "notify", "references": ["email.send"]}]
        huge = {
            "classes": [{"name": f"ServiceControllerWorkflowGateway{i:05d}_" + "界" * 120,
                         "source_file": f"service{i:05d}/" + "directory/" * 8 + "model.py",
                         "methods": methods, "attributes": [{"name": "id"}, {"name": "state"}],
                         "base_classes": ["BaseModel"]} for i in range(20_000)],
            "functions": [{"name": f"process{i:05d}_" + "界" * 100, "source_file": f"app/{i}/run.py",
                           "parameters": ["request"], "invocations": ["load", "save"]} for i in range(2_000)],
            "metadata": {"projectName": "Huge", "sourceLanguages": ["Python"],
                         "sourceFiles": [f"service{i:05d}/kubernetes/docker-compose-" + "x" * 120 + ".yml" for i in range(20_000)]},
        }
        cls.huge_path.write_text(json.dumps(huge, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.huge_dir.cleanup()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.ir_path = self.root / "ir.json"
        self.ir_path.write_text(json.dumps(sample_ir()), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def generate(self, ir: dict, diagram_type: str = "class", **kwargs) -> tuple[bytes, dict]:
        self.ir_path.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
        path = ir_views.generate_ir_view(self.ir_path, diagram_type, self.root / "out", **kwargs)
        raw = path.read_bytes()
        return raw, json.loads(raw)

    def assert_bound(self, raw: bytes, view: dict, budget: int, maximum: int) -> None:
        metadata = view["_metadata"]
        self.assertLessEqual(len(raw), budget)
        self.assertEqual(metadata["final_bytes"], len(raw))
        self.assertEqual(metadata["budget_bytes"], budget)
        self.assertLessEqual(metadata["elements_retained"], metadata["final_max_elements"])
        self.assertLessEqual(metadata["final_max_elements"], maximum)
        self.assertGreaterEqual(metadata["iterations"], 1)
        self.assertGreaterEqual(metadata["compaction_ms"], 0)

    def test_identical_bytes_with_fixed_clock(self) -> None:
        for diagram_type in TYPES:
            with self.subTest(diagram_type=diagram_type), patch.object(ir_views.time, "perf_counter", return_value=25.0):
                first, _ = self.generate(sample_ir(), diagram_type)
                second, _ = self.generate(sample_ir(), diagram_type)
                self.assertEqual(first, second)

    def test_duration_is_the_only_variable_field(self) -> None:
        with patch.object(ir_views.time, "perf_counter", side_effect=[1.0, 1.01, 2.0, 2.02]):
            first_raw, first = self.generate(sample_ir())
            second_raw, second = self.generate(sample_ir())
        self.assertEqual(first["_metadata"].pop("compaction_ms"), 10.0)
        self.assertEqual(second["_metadata"].pop("compaction_ms"), 20.0)
        self.assertEqual(first, second)
        self.assertEqual(len(first_raw), len(second_raw))

    def test_huge_ir_fits_every_diagram_budget(self) -> None:
        for diagram_type in TYPES:
            budget = ir_views.DEEP_VIEW_BYTES if diagram_type in ir_views.DEEP_TYPES else ir_views.SINGLE_VIEW_BYTES
            with self.subTest(diagram_type=diagram_type):
                path = ir_views.generate_ir_view(self.huge_path, diagram_type, self.root, 120, budget)
                raw = path.read_bytes()
                view = json.loads(raw)
                self.assert_bound(raw, view, budget, 120)
                self.assertEqual(view["_metadata"]["elements_total_in_ir"], 22_000)
                self.assertEqual(view["_metadata"]["elements_retained"], ir_views._retained_count(view, diagram_type))

    def test_oversized_single_names_and_metadata_fit_every_type(self) -> None:
        long_name = "ControllerServiceWorkflowGateway" + "界" * 30_000
        ir = {"classes": [{"name": long_name, "source_file": long_name + "/model.py"}],
              "functions": [{"name": long_name, "calls": ["run"]}],
              "metadata": {"projectName": long_name, "sourceLanguages": [long_name],
                           "sourceFiles": [long_name + "/docker-compose.yml"]}}
        for diagram_type in TYPES:
            with self.subTest(diagram_type=diagram_type):
                raw, view = self.generate(ir, diagram_type, budget_bytes=1_024)
                self.assert_bound(raw, view, 1_024, 60)
                self.assertEqual(view["_metadata"]["elements_retained"], 0)
                self.assertEqual(view["_metadata"]["final_max_elements"], 0)

    def test_names_only_fallback_preserves_important_name(self) -> None:
        ir = {"classes": [{"name": "ImportantService", "methods": [
            {"name": "execute", "parameters": ["X" * 100_000]}]}]}
        raw, view = self.generate(ir, max_elements=1, budget_bytes=1_024)
        self.assert_bound(raw, view, 1_024, 1)
        self.assertEqual(view["classes"], [{"name": "ImportantService"}])
        self.assertEqual(view["_metadata"]["elements_retained"], 1)

    def test_halving_can_exceed_four_attempts(self) -> None:
        ir = {"classes": [{"name": "Controller" + "x" * 5_000 + str(i)} for i in range(20)]}
        raw, view = self.generate(ir, max_elements=1_024, budget_bytes=1_024)
        self.assert_bound(raw, view, 1_024, 1_024)
        self.assertGreater(view["_metadata"]["iterations"], 4)

    def test_original_101651_byte_use_case_regression(self) -> None:
        # Exact input from revision_work/codex_audit.log:4453. The original
        # _use_case_view yielded 101,651 bytes at every max_elements setting.
        ir = {"classes": [{"name": "Controller" + "X" * 5_000 + str(i), "kind": "class"} for i in range(20)]}
        for budget in (60_000, 100_000):
            with self.subTest(budget=budget):
                raw, view = self.generate(ir, "use_case", budget_bytes=budget)
                self.assert_bound(raw, view, budget, 60)
                self.assertGreater(view["_metadata"]["elements_retained"], 0)

    def test_all_generators_respect_shared_element_limit(self) -> None:
        for diagram_type in TYPES:
            generator = getattr(ir_views, f"_{diagram_type}_view")
            for maximum in (0, 1, 2, 5):
                with self.subTest(diagram_type=diagram_type, maximum=maximum):
                    view = generator(sample_ir(), maximum)
                    self.assertLessEqual(ir_views._retained_count(view, diagram_type), maximum)
                    if diagram_type == "sequence":
                        self.assertLessEqual(sum(map(len, view["file_modules"].values())), maximum)
                    if diagram_type == "component":
                        self.assertTrue(all(len(package["classes"]) <= maximum for package in view["packages"]))
                    if diagram_type == "system_context":
                        self.assertLessEqual(len(view["external_integrations"]) + len(view["api_boundary"]), maximum)

    def test_zero_limit_writes_empty_selection_for_every_type(self) -> None:
        for diagram_type in TYPES:
            with self.subTest(diagram_type=diagram_type):
                raw, view = self.generate(sample_ir(), diagram_type, max_elements=0)
                self.assertEqual(view["_metadata"]["elements_retained"], 0)
                self.assert_bound(raw, view, view["_metadata"]["budget_bytes"], 0)

    def test_important_classes_survive_prefix_noise(self) -> None:
        ir = {"classes": [
            {"name": "AAAHelperTest", "methods": [{"name": "check"}]},
            {"name": "AAAMockException"},
            {"name": "AAAPlainDTO", "attributes": [{"name": "id"}]},
            {"name": "OrderService", "methods": [{"name": "execute"}] * 5,
             "attributes": [{"name": "repository"}], "implements": ["OrderRepository"]},
        ]}
        _, view = self.generate(ir, max_elements=1)
        self.assertEqual([cls["name"] for cls in view["classes"]], ["OrderService"])

    def test_exact_class_scoring_formula(self) -> None:
        cls = {"name": "OrderService", "methods": [{"name": "one"}, {"name": "two"}],
               "attributes": [{"name": "a"}] * 3, "base_classes": ["Base"]}
        # 2*2 + 3 + 8 + 6*2 + 12 = 39.
        self.assertEqual(ir_views._class_score(cls, Counter(OrderService=2), "class"), 39)
        for name in ir_views.IMPORTANT_NAMES:
            with self.subTest(positive=name):
                self.assertEqual(ir_views._class_score({"name": name}, Counter(), "class"), 12)
        for name in ir_views.NOISE_NAMES:
            with self.subTest(negative=name):
                self.assertEqual(ir_views._class_score({"name": name}, Counter(), "class"), -20)
        self.assertEqual(ir_views._class_score({"name": "OrderDTO"}, Counter(), "class"), -15)
        self.assertEqual(ir_views._class_score({"name": "OrderDTO", "methods": [{"name": "execute"}]}, Counter(), "class"), 2)

    def test_inheritance_in_degree_and_normalization(self) -> None:
        ir = {"classes": [{"name": "AOther", "methods": [{"name": "m"}] * 5},
                          {"name": "ZParent"},
                          {"name": "Child1", "extends": "pkg.ZParent"},
                          {"name": "Child2", "implements": ["pkg::ZParent<T>"]},
                          {"name": "Child3", "base_classes": ["ZParent", "pkg.ZParent"]}]}
        _, view = self.generate(ir, max_elements=1)
        self.assertEqual(view["classes"][0]["name"], "ZParent")

    def test_function_call_hints_win_behavioral_selection(self) -> None:
        for diagram_type in ("sequence", "use_case", "activity"):
            for field in ("calls", "invocations", "references"):
                with self.subTest(diagram_type=diagram_type, field=field):
                    ir = {"classes": [{"name": "APlain", "methods": [{"name": "run"}]}],
                          "functions": [{"name": "APlain"}, {"name": "ZFlow", field: ["load", "save", "notify"]}]}
                    _, view = self.generate(ir, diagram_type, max_elements=1)
                    self.assertEqual(view["functions"][0]["name"], "ZFlow")
                    self.assertEqual(view["functions"][0][field], ["load", "save", "notify"])

    def test_method_call_hints_increase_class_behavioral_importance(self) -> None:
        for diagram_type in ("sequence", "use_case", "activity"):
            with self.subTest(diagram_type=diagram_type):
                ir = {"classes": [{"name": "APlain", "methods": [{"name": "a"}] * 4},
                                  {"name": "ZFlow", "methods": [{"name": "execute", "references": ["a", "b", "c"]}]}]}
                _, view = self.generate(ir, diagram_type, max_elements=1)
                entry = view["class_methods"][0] if diagram_type == "sequence" else view["other_classes"][0]
                self.assertEqual(entry.get("name", entry.get("class")), "ZFlow")

    def test_diagram_specific_role_bonuses(self) -> None:
        for diagram_type, role, collection in (("use_case", "ZRouter", "controllers"),
                                               ("activity", "ZWorkflow", "orchestrators"),
                                               ("system_context", "ZGateway", "external_integrations")):
            with self.subTest(diagram_type=diagram_type):
                ir = {"classes": [{"name": "APlain", "methods": [{"name": "m"}] * 5}, {"name": role}]}
                _, view = self.generate(ir, diagram_type, max_elements=1)
                self.assertEqual(view[collection][0]["name"], role)

    def test_component_package_ranking_uses_member_importance(self) -> None:
        _, view = self.generate(sample_ir(), "component", max_elements=1)
        self.assertEqual(view["packages"][0]["package"], "app/core")
        # BaseModel's incoming inheritance edge makes it score 18, ahead of
        # OrderService's 15; the same class ranking orders package members.
        self.assertEqual(view["packages"][0]["classes"][0], "BaseModel")

    def test_deployment_ranks_infrastructure_hints(self) -> None:
        ir = {"metadata": {"sourceFiles": ["aaa/a.py", "aaa/b.py", "z/kubernetes/docker-compose.yml"]}}
        _, view = self.generate(ir, "deployment", max_elements=1)
        # z directory: 1 source file + 30 infrastructure-hint points.
        self.assertEqual(view["services"], [{"directory": "z", "file_count": 1}])
        self.assertEqual(ir_views._infra_score("z/kubernetes/docker-compose.yml"), 30)

    def test_ties_use_name_then_full_source_file(self) -> None:
        ir = {"classes": [{"name": "B", "source_file": "b.py"},
                          {"name": "A", "source_file": "z.py"}, {"name": "A", "source_file": "a.py"}]}
        first = ir_views._class_view(ir, 2)["classes"]
        self.assertEqual([(cls["name"], cls["source_file"]) for cls in first], [("A", "a.py"), ("A", "z.py")])
        ir["classes"].reverse()
        self.assertEqual(first, ir_views._class_view(ir, 2)["classes"])

    def test_detail_scales_with_original_size_and_budget(self) -> None:
        for size, expected in ((10, 20), (20, 15), (40, 10), (41, 5)):
            ir = {"classes": [{"name": "ImportantService", "methods": [{"name": f"m{i}"} for i in range(30)],
                               "attributes": [{"name": f"a{i}"} for i in range(30)]}]
                             + [{"name": f"C{i}"} for i in range(size - 1)]}
            with self.subTest(size=size):
                # Shrinking to one class must not increase detail for large IRs.
                cls = ir_views._class_view(ir, 1, ir_views.DEEP_VIEW_BYTES)["classes"][0]
                self.assertEqual(len(cls["methods"]), expected)
                self.assertEqual(len(cls["attributes"]), expected)
        ir = {"classes": [{"name": str(i)} for i in range(10)]}
        # Halving the budget doubles the effective size, so detail drops a tier.
        self.assertEqual(ir_views._detail_limit(ir, ir_views.DEEP_VIEW_BYTES // 2), 15)
        self.assertEqual(ir_views._detail_limit(ir, ir_views.DEEP_VIEW_BYTES), 20)

    def test_original_signature_and_per_path_defaults(self) -> None:
        for diagram_type in TYPES:
            with self.subTest(diagram_type=diagram_type):
                path = ir_views.generate_ir_view(self.ir_path, diagram_type, self.root, 5)
                raw = path.read_bytes()
                budget = ir_views.DEEP_VIEW_BYTES if diagram_type in ir_views.DEEP_TYPES else ir_views.SINGLE_VIEW_BYTES
                self.assert_bound(raw, json.loads(raw), budget, 5)
        raw, view = self.generate(sample_ir(), "unknown")
        self.assertIn("classes", view)
        self.assert_bound(raw, view, 60_000, 60)

    def test_invalid_limits_fail_before_writing(self) -> None:
        for kwargs in ({"max_elements": -1}, {"budget_bytes": 1_023}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ir_views.generate_ir_view(self.ir_path, "class", self.root / "out", **kwargs)
        self.assertFalse((self.root / "out").exists())

    def test_generators_do_not_mutate_source_ir(self) -> None:
        ir = sample_ir()
        original = copy.deepcopy(ir)
        for diagram_type in TYPES:
            getattr(ir_views, f"_{diagram_type}_view")(ir, 2)
        self.assertEqual(ir, original)


if __name__ == "__main__":
    unittest.main()
