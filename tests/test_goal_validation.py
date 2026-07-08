import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestGoalValidation(unittest.TestCase):
    def test_ocr_result_schema_and_quality_are_exposed_through_extract_document(self):
        from PIL import Image
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "扫描公告.png")
            Image.new("RGB", (320, 120), color="white").save(image_path)

            def low_quality_ocr(path):
                return {
                    "status": "success",
                    "engine": "fake-low-quality",
                    "text": "项目名称：低置信扫描项目",
                    "pages": [{"page": 1, "text": "项目名称：低置信扫描项目", "confidence": 0.52}],
                }

            extracted = DataCleaningTools(workspace_dir=td, ocr_adapter=low_quality_ocr).extract_document(image_path)

            self.assertEqual(extracted["status"], "success")
            self.assertEqual(extracted["ocr"]["schema_version"], "ocr.result.v1")
            self.assertEqual(extracted["ocr"]["quality"]["quality"], "poor")
            self.assertTrue(extracted["ocr"]["quality"]["needs_human_review"])
            self.assertIn("low_mean_confidence", extracted["ocr"]["quality"]["reasons"])
            self.assertTrue(extracted["needs_human_review"])

    def test_blocked_ocr_result_uses_standard_schema(self):
        from PIL import Image
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "无OCR扫描公告.png")
            Image.new("RGB", (320, 120), color="white").save(image_path)

            def failed_ocr(path):
                return {
                    "status": "failed",
                    "engine": "fake-missing",
                    "error": "ocr adapter unavailable",
                    "blocked_reason": "ocr_adapter_unavailable",
                }

            extracted = DataCleaningTools(workspace_dir=td, ocr_adapter=failed_ocr).extract_document(image_path)

            self.assertEqual(extracted["status"], "blocked")
            self.assertEqual(extracted["ocr"]["schema_version"], "ocr.result.v1")
            self.assertIn(extracted["blocked_reason"], {"ocr_adapter_unavailable", "ocr_engine_failed"})
            self.assertTrue(extracted["ocr"]["quality"]["needs_human_review"])

    def test_ocr_is_registered_as_explicit_tool(self):
        import main

        registry = main._build_registry_for_skill("data_cleaning_file_organization")
        self.assertIn("run_ocr", registry.list_tools())
        tool = registry.get("run_ocr")
        self.assertIn("file_path", tool.parameters)
        self.assertIn("file_path", tool.required_params)

    def test_scanned_pdf_without_ocr_is_blocked_not_success(self):
        from tools.data_cleaning_tools import DataCleaningTools

        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF not installed")

        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "空白扫描件.pdf")
            doc = fitz.open()
            doc.new_page(width=320, height=160)
            doc.save(pdf_path)
            doc.close()

            def failed_ocr(path):
                return {
                    "status": "failed",
                    "engine": "fake-missing",
                    "error": "ocr adapter unavailable",
                    "blocked_reason": "ocr_adapter_unavailable",
                }

            extracted = DataCleaningTools(workspace_dir=td, ocr_adapter=failed_ocr).extract_document(pdf_path)

            self.assertEqual(extracted["status"], "blocked")
            self.assertIn(extracted["blocked_reason"], {"ocr_adapter_unavailable", "ocr_engine_failed"})
            self.assertEqual(extracted["ocr"]["schema_version"], "ocr.result.v1")

            result = DataCleaningTools(workspace_dir=td, ocr_adapter=failed_ocr).prepare_file_organization_run([pdf_path])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["processed"], 0)
            self.assertEqual(result["failed"], 1)

    def test_business_rule_validation_requires_non_unknown_stage(self):
        from validation.goal_validation import run_goal_validation

        with tempfile.TemporaryDirectory() as td:
            report = run_goal_validation(td)
            stages = [
                judgement["business_stage"]
                for case in report["cases"]
                for judgement in case["module_evidence"]["business_rules"]["business_judgements"]
            ]
            self.assertIn("won_pending_contract", stages)
            self.assertGreaterEqual(report["module_scores"]["business_rule_judgement"]["score"], 85)

    def test_process_documents_to_ledger_preserves_ocr_extract_method_in_evidence(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "扫描补充.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n% scanned fixture placeholder\n")
            with open(f"{pdf_path}.ocr.txt", "w", encoding="utf-8") as f:
                f.write("项目名称：旧入口OCR项目\n采购人：测试客户\n销售负责人：张三")

            result = DataCleaningTools(workspace_dir=td).process_documents_to_ledger([pdf_path])

            self.assertEqual(result["status"], "success")
            with open(result["artifacts"]["project_ledger_json"], "r", encoding="utf-8") as f:
                ledger_state = json.load(f)
            self.assertTrue(ledger_state["evidence_index"])
            self.assertEqual(
                {item["extract_method"] for item in ledger_state["evidence_index"]},
                {"ocr"},
            )

    def test_validation_runner_outputs_three_cases_and_module_scores(self):
        from validation.goal_validation import run_goal_validation

        with tempfile.TemporaryDirectory() as td:
            report = run_goal_validation(td)

            self.assertEqual(report["schema_version"], "project_manager.goal_validation.v1")
            self.assertEqual({case["case"] for case in report["cases"]}, {
                "normal_simulation",
                "failure_simulation",
                "real_case_simulation",
            })
            self.assertTrue(os.path.exists(report["report_path"]))
            self.assertTrue(os.path.exists(report["summary_path"]))

            for case in report["cases"]:
                with self.subTest(case=case["case"]):
                    self.assertTrue(os.path.exists(case["artifacts"]["run_dir"]))
                    self.assertTrue(os.path.exists(case["artifacts"]["input_manifest"]))
                    self.assertTrue(os.path.exists(case["artifacts"]["review_queue"]))
                    self.assertTrue(os.path.exists(case["artifacts"]["planned_archive_actions"]))
                    self.assertTrue(os.path.exists(case["artifacts"]["run_report"]))
                    self.assertTrue(os.path.exists(case["artifacts"]["trace"]))
                    if case["expected_status"] == "success":
                        self.assertIn(case["result"]["status"], {"success", "partial"})
                        self.assertTrue(case["module_evidence"]["file_organization"]["has_run_package"])
                        self.assertTrue(case["module_evidence"]["project_ledger"]["has_ledger_artifacts"])
                        self.assertTrue(case["module_evidence"]["business_rules"]["has_business_judgement"])
                    else:
                        self.assertEqual(case["result"]["status"], "failed")
                        self.assertTrue(case["module_evidence"]["scanned_document_processing"]["blocked_without_ocr"])

            scores = report["module_scores"]
            self.assertEqual(report["final_status"], "pass")
            self.assertEqual(report["optimization_cycles"][0]["status"], "pass")
            self.assertEqual(report["optimization_cycles"][0]["below_threshold"], {})
            self.assertEqual(set(scores), {
                "file_organization_run_package",
                "project_overview_ledger",
                "business_rule_judgement",
                "image_scanned_document_processing",
            })
            for module, score in scores.items():
                with self.subTest(module=module):
                    self.assertGreaterEqual(score["score"], 85)
                    self.assertTrue(score["evidence"])

            with open(report["report_path"], "r", encoding="utf-8") as f:
                persisted = json.load(f)
            self.assertEqual(persisted["module_scores"], report["module_scores"])


if __name__ == "__main__":
    unittest.main()
