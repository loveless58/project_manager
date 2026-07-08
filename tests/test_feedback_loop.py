import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FeedbackLoopTests(unittest.TestCase):
    def test_feedback_decision_contract_normalizes_supported_types(self):
        from contracts.feedback_schema import normalize_feedback_decision

        decision = normalize_feedback_decision(
            {
                "feedback_type": "field_correction",
                "item_id": "R001",
                "field": "sales_person",
                "old_value": "领导",
                "new_value": "甄勇",
                "reason": "source text supports corrected owner",
            },
            run_id="run_feedback",
            index=1,
        )

        self.assertEqual(decision["schema_version"], "human_feedback.decision.v1")
        self.assertEqual(decision["feedback_id"], "FB001")
        self.assertEqual(decision["feedback_type"], "field_correction")
        self.assertEqual(decision["run_id"], "run_feedback")
        self.assertEqual(decision["decision"], "correct")
        self.assertEqual(decision["risk_level"], "P2")

    def test_apply_feedback_decisions_writes_review_artifacts_without_business_mutation(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_feedback"
            os.makedirs(os.path.join(td, "runs", run_id))
            tools = DataCleaningTools(workspace_dir=td)

            result = tools.apply_feedback_decisions(
                run_id=run_id,
                feedback_decisions=[
                    {
                        "feedback_type": "field_correction",
                        "item_id": "R001",
                        "field": "sales_person",
                        "old_value": "领导",
                        "new_value": "甄勇",
                        "evidence_text": "销售负责人：甄勇",
                    },
                    {
                        "feedback_type": "archive_decision",
                        "item_id": "R002",
                        "decision": "approve",
                        "source_file": "a.docx",
                        "target_path": "归档/a.docx",
                    },
                    {
                        "feedback_type": "parser_case",
                        "item_id": "R003",
                        "input_pattern": "合同金额：12.5万元",
                        "expected_field": "contract_amount",
                        "expected_value": "125000",
                    },
                ],
            )

            self.assertEqual(result["schema_version"], "human_feedback.apply.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["accepted"], 3)
            self.assertEqual(result["failed"], 0)
            self.assertTrue(os.path.exists(result["artifacts"]["feedback_events"]))
            self.assertTrue(os.path.exists(result["artifacts"]["human_feedback_decisions"]))
            self.assertTrue(os.path.exists(result["artifacts"]["rule_candidates"]))
            self.assertTrue(os.path.exists(result["artifacts"]["parser_test_candidates"]))
            self.assertFalse(os.path.exists(os.path.join(td, "runs", run_id, "archive_result.json")))
            self.assertFalse(os.path.exists(os.path.join(td, "project_ledger.json")))

            with open(result["artifacts"]["human_feedback_decisions"], "r", encoding="utf-8") as f:
                persisted = json.load(f)
            self.assertEqual(persisted["schema_version"], "human_feedback.decisions.v1")
            self.assertEqual(len(persisted["decisions"]), 3)

            with open(result["artifacts"]["parser_test_candidates"], "r", encoding="utf-8") as f:
                parser_candidates = json.load(f)
            self.assertEqual(len(parser_candidates["items"]), 2)
            self.assertEqual(parser_candidates["items"][0]["status"], "pending_test_authoring")

            with open(result["artifacts"]["rule_candidates"], "r", encoding="utf-8") as f:
                rule_candidates = json.load(f)
            self.assertEqual(len(rule_candidates["items"]), 1)
            self.assertEqual(rule_candidates["items"][0]["feedback_type"], "archive_decision")

            with open(result["artifacts"]["feedback_events"], "r", encoding="utf-8") as f:
                events = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(events), 3)
            self.assertEqual(events[0]["schema_version"], "human_feedback.event.v1")

    def test_apply_feedback_decisions_reports_invalid_items_without_failing_batch(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_invalid_feedback"
            os.makedirs(os.path.join(td, "runs", run_id))
            result = DataCleaningTools(workspace_dir=td).apply_feedback_decisions(
                run_id=run_id,
                feedback_decisions=[
                    {"feedback_type": "unsupported_type", "item_id": "R404"},
                    {"feedback_type": "false_positive", "item_id": "R001", "finding_id": "AV001"},
                ],
            )

            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["accepted"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["errors"][0]["error"], "unsupported_feedback_type")

    def test_apply_feedback_decisions_updates_matching_review_queue_items(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_feedback_queue"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            review_queue_path = os.path.join(run_dir, "review_queue.json")
            with open(review_queue_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "schema_version": "review_queue.v2",
                        "run_id": run_id,
                        "status": "needs_review",
                        "items": [
                            {
                                "id": "R001",
                                "run_id": run_id,
                                "risk": "P1",
                                "feedback_type": "archive_decision",
                                "recommended_decision": "defer",
                            },
                            {
                                "id": "R002",
                                "run_id": run_id,
                                "risk": "P3",
                                "feedback_type": "parser_case",
                                "recommended_decision": "add_parser_case",
                            },
                        ],
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            result = DataCleaningTools(workspace_dir=td).apply_feedback_decisions(
                run_id=run_id,
                feedback_decisions=[
                    {
                        "feedback_id": "FB010",
                        "feedback_type": "archive_decision",
                        "item_id": "R001",
                        "decision": "approve",
                    }
                ],
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["review_queue_updates"]["updated"], 1)
            self.assertEqual(result["review_queue_updates"]["pending"], ["R002"])
            self.assertEqual(result["artifacts"]["review_queue"], review_queue_path)

            with open(review_queue_path, "r", encoding="utf-8") as f:
                updated_queue = json.load(f)
            self.assertEqual(updated_queue["schema_version"], "review_queue.v2")
            self.assertEqual(updated_queue["status"], "needs_review")
            self.assertEqual(updated_queue["items"][0]["feedback_status"], "feedback_received")
            self.assertEqual(updated_queue["items"][0]["feedback_ids"], ["FB010"])
            self.assertEqual(updated_queue["items"][0]["feedback_decisions"], ["approve"])
            self.assertEqual(updated_queue["items"][1]["feedback_status"], "pending")

    def test_apply_feedback_decisions_is_registered_runtime_tool(self):
        import main
        from loop_packages import get_loop_package

        package = get_loop_package("data_cleaning_file_organization")
        self.assertIn("apply_feedback_decisions", package.expected_tools)

        registry = main._build_registry_for_skill("data_cleaning_file_organization")
        self.assertIn("apply_feedback_decisions", registry.list_tools())


if __name__ == "__main__":
    unittest.main()
