import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ReviewQueueContractTests(unittest.TestCase):
    def test_review_queue_contract_adds_decision_fields_without_dropping_legacy_fields(self):
        from contracts.review_queue_schema import normalize_review_queue

        queue = normalize_review_queue(
            run_id="run_review",
            raw_items=[
                {
                    "type": "archive_action_review",
                    "severity": "medium",
                    "project_name": "合成项目002",
                    "source_file": "a.docx",
                    "target_path": "归档/a.docx",
                    "blockers": ["human_review_recommended"],
                }
            ],
        )

        self.assertEqual(queue["schema_version"], "review_queue.v2")
        self.assertEqual(queue["status"], "needs_review")
        item = queue["items"][0]
        self.assertEqual(item["id"], "R001")
        self.assertEqual(item["type"], "archive_action_review")
        self.assertEqual(item["risk"], "P2")
        self.assertEqual(item["feedback_type"], "archive_decision")
        self.assertEqual(item["allowed_decisions"], ["approve", "reject", "edit_target", "defer"])
        self.assertEqual(item["recommended_decision"], "defer")
        self.assertIn("归档", item["question"])
        self.assertEqual(item["blockers"], ["human_review_recommended"])

    def test_prepare_file_organization_run_outputs_standardized_review_queue_items(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "采购公告.md")
            with open(source, "w", encoding="utf-8") as f:
                f.write("项目名称：合成项目029\n招标人：合成机构013有限公司\n投标截止：2026-05-26\n")

            tools = DataCleaningTools(workspace_dir=td)
            with patch.object(tools, "_use_archive_metadata_passthrough", return_value=True):
                result = tools.prepare_file_organization_run([source])

            self.assertEqual(result["review_queue"]["schema_version"], "review_queue.v2")
            self.assertEqual(result["review_queue"]["status"], "needs_review")
            item = result["review_queue"]["items"][0]
            self.assertIn("id", item)
            self.assertIn("risk", item)
            self.assertIn("question", item)
            self.assertIn("allowed_decisions", item)
            self.assertIn("recommended_decision", item)
            self.assertIn("feedback_type", item)
            self.assertIn("evidence", item)
            self.assertEqual(item["type"], "extraction_quality_review")
            self.assertEqual(item["reason"], "metadata_passthrough_archive_only")

    def test_clear_review_queue_keeps_v2_schema_and_empty_items(self):
        from contracts.review_queue_schema import normalize_review_queue

        queue = normalize_review_queue(run_id="run_clear", raw_items=[])

        self.assertEqual(queue["schema_version"], "review_queue.v2")
        self.assertEqual(queue["status"], "clear")
        self.assertEqual(queue["items"], [])


if __name__ == "__main__":
    unittest.main()
