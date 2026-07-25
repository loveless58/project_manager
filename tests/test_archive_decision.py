import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestArchiveDecision(unittest.TestCase):
    def test_project_manager_prd_is_internal_project_archive_decision(self):
        from business_rules.archive_decision import evaluate_archive_decision

        with tempfile.TemporaryDirectory() as td:
            decision = evaluate_archive_decision(
                source_file=r"Q:\SyntheticBusiness\PRD-project-manager-ocr.md",  # repo-hygiene: allow=synthetic-path
                extracted={
                    "filename": "PRD-project-manager-ocr.md",
                    "file_type": ".md",
                    "document_type": "项目投标",
                    "classification": {
                        "document_type": "项目治理文档",
                        "business_domain": "internal_project",
                        "project_phase": None,
                        "archive_phase": None,
                        "confidence": 0.98,
                        "evidence": ["filename:PRD-project-manager-ocr.md", "text:project_manager"],
                        "requires_review": False,
                    },
                    "extracted_text": "# PRD\nproject_manager OCR design",
                    "fields": {},
                },
                business_judgement={"human_review_required": True},
                project_files_dir=os.path.join(td, "项目文件"),
            )

            self.assertEqual(decision["schema_version"], "archive_decision.v1")
            self.assertEqual(decision["subject_type"], "internal_project")
            self.assertEqual(decision["subject_name"], "project_manager")
            self.assertIsNone(decision["archive_phase"])
            self.assertEqual(decision["document_type"], "项目治理文档")
            self.assertIn("pm_internal_archive_pending_redesign", decision["blockers"])
            self.assertTrue(decision["human_review_required"])
            self.assertIsNone(decision["target_dir"])
            self.assertIsNone(decision["target_path"])

    def test_unknown_business_document_keeps_unknown_project_blocker(self):
        from business_rules.archive_decision import evaluate_archive_decision

        with tempfile.TemporaryDirectory() as td:
            decision = evaluate_archive_decision(
                source_file=r"Q:\SyntheticBusiness\random.md",  # repo-hygiene: allow=synthetic-path
                extracted={
                    "filename": "random.md",
                    "file_type": ".md",
                    "document_type": "项目投标",
                    "classification": {"document_type": "项目投标", "business_domain": "bid_project", "project_phase": None, "archive_phase": None, "confidence": 0.5, "evidence": [], "requires_review": False},
                    "extracted_text": "unstructured notes",
                    "fields": {},
                },
                business_judgement={"human_review_required": True},
                project_files_dir=os.path.join(td, "项目文件"),
            )

            self.assertEqual(decision["subject_type"], "unknown")
            self.assertIn("unknown_project", decision["blockers"])
            self.assertTrue(decision["human_review_required"])

    def test_closed_lost_project_record_uses_lost_phase_from_source_path(self):
        from business_rules.archive_decision import evaluate_archive_decision

        with tempfile.TemporaryDirectory() as td:
            decision = evaluate_archive_decision(
                source_file=r"Q:\SyntheticBusiness\项目文件\项目丢标\合成终端采购项目\项目记录.md",  # repo-hygiene: allow=synthetic-path
                extracted={
                    "filename": "项目记录.md",
                    "file_type": ".md",
                    "document_type": "项目记录",
                    "classification": {"document_type": "项目记录", "business_domain": "bid_project", "project_phase": "项目丢标", "archive_phase": "项目丢标", "confidence": 0.9, "evidence": [], "requires_review": False},
                    "extracted_text": "# 项目记录：合成终端采购项目\n报名状态：已弃标",
                    "fields": {
                        "project_name": "合成终端采购项目",
                        "bid_status": "弃标",
                        "lifecycle_stage": "closed_lost",
                    },
                },
                business_judgement={
                    "business_stage": "closed_lost",
                    "human_review_required": False,
                },
                project_files_dir=os.path.join(td, "项目文件"),
            )

            self.assertEqual(decision["subject_type"], "bid_project")
            self.assertEqual(decision["subject_name"], "合成终端采购项目")
            self.assertEqual(decision["archive_phase"], "项目丢标")
            self.assertEqual(decision["blockers"], [])
            self.assertFalse(decision["human_review_required"])
            self.assertIn(os.path.join("项目丢标", "合成终端采购项目", "原始文件"), decision["target_dir"])

    def test_imported_digital_asset_uses_digital_asset_target(self):
        from business_rules.archive_decision import evaluate_archive_decision

        with tempfile.TemporaryDirectory() as td:
            decision = evaluate_archive_decision(
                source_file=r"Q:\SyntheticBusiness\项目文件\项目执行\合成数字资产项目\数据资产\docling_invoice_extract.json",  # repo-hygiene: allow=synthetic-path
                extracted={
                    "filename": "docling_invoice_extract.json",
                    "file_type": ".json",
                    "document_type": "数据资产",
                    "classification": {"document_type": "数据资产", "business_domain": "bid_project", "project_phase": "项目执行", "archive_phase": "项目执行", "confidence": 0.9, "evidence": [], "requires_review": False},
                    "extracted_text": "",
                    "fields": {
                        "project_name": "合成数字资产项目",
                        "lifecycle_stage": "execution",
                    },
                },
                business_judgement={
                    "business_stage": "execution",
                    "human_review_required": False,
                },
                project_files_dir=os.path.join(td, "项目文件"),
            )

            self.assertEqual(decision["archive_phase"], "项目执行")
            self.assertIn(os.path.join("项目执行", "合成数字资产项目", "数字资产", "导入资产"), decision["target_dir"])

    def test_project_directory_preserves_business_parentheses(self):
        from business_rules.archive_decision import evaluate_archive_decision

        project_name = "合成供应商框架项目（开发类）"
        with tempfile.TemporaryDirectory() as td:
            decision = evaluate_archive_decision(
                source_file=fr"Q:\SyntheticBusiness\项目文件\项目执行\{project_name}\项目记录.md",  # repo-hygiene: allow=synthetic-path
                extracted={
                    "filename": "项目记录.md",
                    "file_type": ".md",
                    "document_type": "项目记录",
                    "classification": {"document_type": "项目记录", "business_domain": "bid_project", "project_phase": "项目执行", "archive_phase": "项目执行", "confidence": 0.9, "evidence": [], "requires_review": False},
                    "extracted_text": f"# 项目记录：{project_name}",
                    "fields": {
                        "project_name": project_name,
                        "lifecycle_stage": "execution",
                    },
                },
                business_judgement={
                    "business_stage": "execution",
                    "human_review_required": False,
                },
                project_files_dir=os.path.join(td, "项目文件"),
            )

            self.assertIn(os.path.join("项目执行", project_name, "原始文件"), decision["target_dir"])
            self.assertNotIn("合成供应商框架项目_开发类_", decision["target_dir"])

    def test_archive_decision_preserves_project_name_and_does_not_auto_merge_aliases(self):
        from business_rules.archive_decision import evaluate_archive_decision

        with tempfile.TemporaryDirectory() as td:
            decision = evaluate_archive_decision(
                source_file=r"Q:\SyntheticBusiness\项目文件\项目执行\合成服务系统项目\项目记录.md",  # repo-hygiene: allow=synthetic-path
                extracted={
                    "filename": "项目记录.md",
                    "file_type": ".md",
                    "document_type": "项目记录",
                    "classification": {"document_type": "项目记录", "business_domain": "bid_project", "project_phase": "项目执行", "archive_phase": "项目执行", "confidence": 0.9, "evidence": [], "requires_review": False},
                    "extracted_text": "# 项目记录：合成服务系统项目",
                    "fields": {
                        "project_name": "合成服务系统项目",
                        "lifecycle_stage": "execution",
                    },
                },
                business_judgement={
                    "business_stage": "execution",
                    "human_review_required": False,
                },
                project_files_dir=os.path.join(td, "项目文件"),
            )

            self.assertEqual(decision["subject_name"], "合成服务系统项目")
            self.assertIn(os.path.join("项目执行", "合成服务系统项目", "原始文件"), decision["target_dir"])


if __name__ == "__main__":
    unittest.main()
