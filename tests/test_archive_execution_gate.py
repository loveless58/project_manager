import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ArchiveExecutionGateTests(unittest.TestCase):
    def test_unconfirmed_archive_intent_has_zero_side_effects(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_unconfirmed_intent"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            source_path = os.path.join(td, "source.docx")
            target_dir = os.path.join(td, "archive")
            target_path = os.path.join(target_dir, "source.docx")
            Path(source_path).write_text("source", encoding="utf-8")
            self._write_json(os.path.join(run_dir, "planned_archive_actions.json"), {
                "schema_version": "archive_plan.v1", "run_id": run_id,
                "actions": [{
                    "source_file": source_path, "target_path": target_path,
                    "status": "needs_review", "confirmed": False,
                    "archive_intent": {"schema_version": "archive_intent.v1", "destination_status": "unresolved"},
                    "blockers": ["ARCHIVE_TARGET.UNRESOLVED"],
                }],
            })

            result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=False)

            self.assertEqual(result["status"], "needs_confirmation")
            self.assertTrue(os.path.exists(source_path))
            self.assertFalse(os.path.exists(target_dir))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "archive_result.json")))

    def test_confirmed_execution_rejects_unresolved_review_intent_without_result_artifact(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_unresolved_intent"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            source_path = os.path.join(td, "source.docx")
            target_path = os.path.join(td, "archive", "source.docx")
            Path(source_path).write_text("source", encoding="utf-8")
            self._write_json(os.path.join(run_dir, "planned_archive_actions.json"), {
                "schema_version": "archive_plan.v1", "run_id": run_id,
                "actions": [{
                    "source_file": source_path, "target_path": target_path,
                    "status": "needs_review", "confirmed": False,
                    "archive_intent": {"schema_version": "archive_intent.v1", "destination_status": "unresolved"},
                    "blockers": ["DOCUMENT_INTERPRETATION.NEEDS_REVIEW"],
                }],
            })

            result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["moved"], 0)
            self.assertTrue(os.path.exists(source_path))
            self.assertFalse(os.path.exists(target_path))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "archive_result.json")))

    def test_execute_archive_plan_blocks_confirmed_run_with_pending_required_feedback(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_gate"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            source_path = os.path.join(td, "source.docx")
            target_path = os.path.join(td, "archive", "source.docx")
            Path(source_path).write_text("source", encoding="utf-8")
            self._write_json(
                os.path.join(run_dir, "planned_archive_actions.json"),
                {
                    "schema_version": "archive_plan.v1",
                    "run_id": run_id,
                    "actions": [
                        {
                            "source_file": source_path,
                            "target_path": target_path,
                            "project_name": "合成项目010",
                            "document_type": "contract",
                            "status": "ready",
                            "confirmed": True,
                            "archive_intent": {
                                "schema_version": "archive_intent.v1",
                                "destination_status": "resolved",
                            },
                            "blockers": [],
                        }
                    ],
                },
            )
            self._write_json(
                os.path.join(run_dir, "audit_review.json"),
                {
                    "schema_version": "audit_review.v1",
                    "run_id": run_id,
                    "audit_verdict": "needs_human_feedback",
                    "human_feedback_required": True,
                    "required_feedback_items": ["R001"],
                },
            )
            self._write_json(
                os.path.join(run_dir, "review_queue.json"),
                {
                    "schema_version": "review_queue.v2",
                    "run_id": run_id,
                    "status": "needs_review",
                    "items": [
                        {
                            "id": "R001",
                            "run_id": run_id,
                            "risk": "P1",
                            "recommended_decision": "defer",
                            "feedback_status": "pending",
                        }
                    ],
                },
            )

            result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

            self.assertEqual(result["schema_version"], "archive_plan.execute.v1")
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["gate"]["schema_version"], "archive_execution_gate.v1")
            self.assertEqual(result["gate"]["status"], "blocked")
            self.assertIn("required_feedback_pending", result["gate"]["blockers"])
            self.assertFalse(os.path.exists(target_path))
            gate_path = os.path.join(run_dir, "archive_execution_gate.json")
            self.assertTrue(os.path.exists(gate_path))
            with open(gate_path, "r", encoding="utf-8") as f:
                persisted = json.load(f)
            self.assertEqual(persisted["status"], "blocked")

    def test_execute_archive_plan_allows_confirmed_run_after_required_feedback_received(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_gate_clear"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            source_path = os.path.join(td, "source.docx")
            target_path = os.path.join(td, "archive", "source.docx")
            Path(source_path).write_text("source", encoding="utf-8")
            self._write_json(
                os.path.join(run_dir, "planned_archive_actions.json"),
                {
                    "schema_version": "archive_plan.v1",
                    "run_id": run_id,
                    "actions": [
                        {
                            "source_file": source_path,
                            "target_path": target_path,
                            "project_name": "合成项目010",
                            "document_type": "contract",
                            "status": "ready",
                            "confirmed": True,
                            "archive_intent": {
                                "schema_version": "archive_intent.v1",
                                "destination_status": "resolved",
                            },
                            "blockers": [],
                        }
                    ],
                },
            )
            self._write_json(
                os.path.join(run_dir, "audit_review.json"),
                {
                    "schema_version": "audit_review.v1",
                    "run_id": run_id,
                    "audit_verdict": "needs_human_feedback",
                    "human_feedback_required": True,
                    "required_feedback_items": ["R001"],
                },
            )
            self._write_json(
                os.path.join(run_dir, "review_queue.json"),
                {
                    "schema_version": "review_queue.v2",
                    "run_id": run_id,
                    "status": "reviewed",
                    "items": [
                        {
                            "id": "R001",
                            "run_id": run_id,
                            "risk": "P1",
                            "recommended_decision": "defer",
                            "feedback_status": "feedback_received",
                            "feedback_ids": ["FB001"],
                        }
                    ],
                },
            )

            result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["gate"]["status"], "passed")
            self.assertTrue(os.path.exists(target_path))

    def test_archive_execution_gate_contract_blocks_pending_required_items(self):
        from contracts.archive_gate_schema import evaluate_archive_execution_gate

        gate = evaluate_archive_execution_gate(
            run_id="run_contract",
            confirmed=True,
            audit_review={
                "human_feedback_required": True,
                "required_feedback_items": ["R001"],
            },
            review_queue={
                "items": [
                    {"id": "R001", "feedback_status": "pending"},
                    {"id": "R002", "feedback_status": "feedback_received"},
                ]
            },
        )

        self.assertEqual(gate["schema_version"], "archive_execution_gate.v1")
        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["pending_required_feedback_items"], ["R001"])

    def test_loop_package_declares_archive_execution_gate_hard_rule(self):
        from loop_packages import get_loop_package

        package = get_loop_package("data_cleaning_file_organization")

        self.assertIn(
            "archive_execution_gate_blocks_pending_feedback",
            package.manifest["hard_rules"],
        )

    @staticmethod
    def _write_json(path: str, payload: dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    unittest.main()
