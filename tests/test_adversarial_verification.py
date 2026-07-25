import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class AdversarialVerificationTests(unittest.TestCase):
    def test_low_ocr_confidence_short_circuits_to_human_review(self):
        from business_rules.adversarial_verification import run_adversarial_verification
        from contracts.review_dimensions import OCR_CONFIDENCE_THRESHOLD, REVIEW_DIMENSIONS

        self.assertIn("field_completeness", REVIEW_DIMENSIONS)
        with tempfile.TemporaryDirectory() as td:
            result = run_adversarial_verification(
                workspace_dir=td,
                run_id="run_low_confidence",
                extracted_items=[
                    {
                        "file": os.path.join(td, "scan.png"),
                        "filename": "scan.png",
                        "document_type": "招标公告",
                        "fields": {"project_name": "合成项目008"},
                        "ocr": {"pages": [{"confidence": OCR_CONFIDENCE_THRESHOLD - 0.1}]},
                    }
                ],
                ledger_results=[],
                archive_actions=[],
            )

            self.assertEqual(result["schema_version"], "adversarial_verification.v1")
            self.assertEqual(result["overall_verdict"], "needs_human_review")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["findings"][0]["dimension"], "ocr_quality")
            self.assertTrue(os.path.exists(result["artifact_path"]))
            self.assertFalse(os.path.exists(os.path.join(td, "project_ledger.json")))

    def test_missing_required_fields_are_reported_without_mutating_business_state(self):
        from business_rules.adversarial_verification import run_adversarial_verification

        with tempfile.TemporaryDirectory() as td:
            result = run_adversarial_verification(
                workspace_dir=td,
                run_id="run_missing_fields",
                extracted_items=[
                    {
                        "file": os.path.join(td, "notice.pdf"),
                        "filename": "采购公告.pdf",
                        "document_type": "采购公告",
                        "extracted_text": "项目名称：合成项目002",
                        "fields": {"project_name": "合成项目002"},
                    }
                ],
                ledger_results=[],
                archive_actions=[],
            )

            self.assertEqual(result["overall_verdict"], "needs_correction")
            self.assertEqual(result["finding_count"], 1)
            finding = result["findings"][0]
            self.assertEqual(finding["dimension"], "field_completeness")
            self.assertEqual(finding["severity"], "high")
            self.assertEqual(finding["details"]["missing_fields"], ["customer", "deadline"])

            with open(result["artifact_path"], "r", encoding="utf-8") as f:
                persisted = json.load(f)
            self.assertEqual(persisted["run_id"], "run_missing_fields")
            self.assertFalse(os.path.exists(os.path.join(td, "archive_result.json")))

    def test_archive_plan_findings_are_warnings_when_project_name_missing_from_target(self):
        from business_rules.adversarial_verification import run_adversarial_verification

        with tempfile.TemporaryDirectory() as td:
            result = run_adversarial_verification(
                workspace_dir=td,
                run_id="run_archive_warning",
                extracted_items=[
                    {
                        "file": os.path.join(td, "contract.docx"),
                        "filename": "合同.docx",
                        "document_type": "合同",
                        "fields": {
                            "project_name": "合成项目007",
                            "customer": "合成机构012有限公司",
                            "contract_status": "已签约",
                        },
                    }
                ],
                ledger_results=[],
                archive_actions=[
                    {
                        "source_file": os.path.join(td, "contract.docx"),
                        "project_name": "合成项目007",
                        "document_type": "合同",
                        "target_path": os.path.join(td, "项目执行", "原始文件", "合同.docx"),
                    }
                ],
            )

            self.assertEqual(result["overall_verdict"], "pass_with_warnings")
            self.assertEqual(result["findings"][0]["dimension"], "archive_plan")
            self.assertEqual(result["findings"][0]["severity"], "low")

    def test_verify_file_organization_run_is_registered_readonly_tool(self):
        import main
        from loop_packages import get_loop_package

        package = get_loop_package("data_cleaning_file_organization")
        self.assertIn("verify_file_organization_run", package.expected_tools)

        registry = main._build_registry_for_skill("data_cleaning_file_organization")
        self.assertIn("verify_file_organization_run", registry.list_tools())

    def test_verify_file_organization_run_reads_prepared_artifacts_without_archiving(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "采购公告.md")
            with open(source, "w", encoding="utf-8") as f:
                f.write("\n".join([
                    "项目名称：合成项目009",
                    "招标人：合成机构012有限公司",
                    "投标截止：2026-05-26",
                ]))

            tools = DataCleaningTools(workspace_dir=td)
            with patch.object(tools, "_use_archive_metadata_passthrough", return_value=False):
                prepared = tools.prepare_file_organization_run([source])

            result = tools.verify_file_organization_run(prepared["run_id"])

            self.assertEqual(result["schema_version"], "adversarial_verification.v1")
            self.assertEqual(result["run_id"], prepared["run_id"])
            self.assertEqual(result["status"], "success")
            self.assertTrue(os.path.exists(result["artifact_path"]))
            self.assertFalse(os.path.exists(os.path.join(td, "runs", prepared["run_id"], "archive_result.json")))

    def test_preparation_normalizes_and_redacts_verification_exceptions(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "notice.md")
            with open(source, "w", encoding="utf-8") as stream:
                stream.write("synthetic notice")
            physical_path = os.path.join(td, "private", "verification.log")
            sensitive = "to" + "ken=" + "synthetic-sensitive-value"
            tools = DataCleaningTools(workspace_dir=td)

            with (
                patch.object(tools, "_use_archive_metadata_passthrough", return_value=True),
                patch(
                    "tools.adversarial_verification.AdversarialVerification.run",
                    side_effect=RuntimeError(f"{sensitive} at {physical_path}"),
                ),
            ):
                result = tools.prepare_file_organization_run([source])

            error_item = next(
                item
                for item in result["review_queue"]["items"]
                if item["type"] == "adversarial_verification_error"
            )
            self.assertTrue(error_item["id"])
            self.assertTrue(error_item["question"])
            self.assertEqual(
                error_item["allowed_decisions"],
                ["retry_verification", "defer", "accept_risk"],
            )
            serialized = json.dumps(result["review_queue"], ensure_ascii=False)
            self.assertNotIn(sensitive, serialized)
            self.assertNotIn(physical_path, serialized)


if __name__ == "__main__":
    unittest.main()
