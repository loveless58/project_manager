import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class AgentRoleTests(unittest.TestCase):
    def test_adversarial_agent_adds_loop_control_fields(self):
        from agents import AdversarialAgent

        with tempfile.TemporaryDirectory() as td:
            result = AdversarialAgent(workspace_dir=td).review(
                run_id="run_needs_review",
                extracted_items=[
                    {
                        "file": os.path.join(td, "scan.png"),
                        "document_type": "采购公告",
                        "fields": {"project_name": "合成项目008"},
                        "ocr": {"pages": [{"confidence": 0.2}]},
                    }
                ],
                archive_actions=[],
            )

            self.assertEqual(result["schema_version"], "adversarial_verification.v1")
            self.assertEqual(result["agent_role"], "adversarial_agent")
            self.assertEqual(result["overall_verdict"], "needs_human_review")
            self.assertTrue(result["needs_human_review"])
            self.assertIn("apply_human_review", result["next_actions"])
            self.assertFalse(result["archive_allowed"])
            self.assertTrue(os.path.exists(result["artifact_path"]))
            with open(result["artifact_path"], "r", encoding="utf-8") as f:
                persisted = json.load(f)
            self.assertEqual(persisted["agent_role"], "adversarial_agent")
            self.assertIn("apply_human_review", persisted["next_actions"])

    def test_audit_agent_reviews_run_artifacts_without_mutating_business_state(self):
        from agents import AuditAgent

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_audit"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            self._write_json(os.path.join(run_dir, "input_manifest.json"), {"schema_version": "file_organization.input_manifest.v1"})
            self._write_json(os.path.join(run_dir, "review_queue.json"), {"schema_version": "file_organization.review_queue.v1", "items": []})
            self._write_json(os.path.join(run_dir, "planned_archive_actions.json"), {"schema_version": "archive_plan.v1", "actions": [{"source_file": "a.docx"}]})
            self._write_json(os.path.join(run_dir, "trace.json"), {"schema_version": "file_organization.trace.v1", "events": []})
            self._write_json(os.path.join(run_dir, "adversarial_verification.json"), {
                "schema_version": "adversarial_verification.v1",
                "overall_verdict": "pass",
                "findings": [],
                "archive_action_count": 1,
            })

            result = AuditAgent(workspace_dir=td).review_run(run_id)

            self.assertEqual(result["schema_version"], "audit_review.v1")
            self.assertEqual(result["agent_role"], "audit_agent")
            self.assertEqual(result["audit_verdict"], "acceptable")
            self.assertEqual(result["missing_artifacts"], [])
            self.assertTrue(result["human_confirmation_required"])
            self.assertIn("execute_archive_plan_with_confirmation", result["next_actions"])
            self.assertTrue(os.path.exists(result["artifact_path"]))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "archive_result.json")))
            self.assertFalse(os.path.exists(os.path.join(td, "project_ledger.json")))

    def test_audit_agent_blocks_when_adversarial_report_is_missing(self):
        from agents import AuditAgent

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_missing_verification"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            self._write_json(os.path.join(run_dir, "input_manifest.json"), {})
            self._write_json(os.path.join(run_dir, "review_queue.json"), {})
            self._write_json(os.path.join(run_dir, "planned_archive_actions.json"), {"actions": []})
            self._write_json(os.path.join(run_dir, "trace.json"), {})

            result = AuditAgent(workspace_dir=td).review_run(run_id)

            self.assertEqual(result["audit_verdict"], "blocked")
            self.assertIn("adversarial_verification", result["missing_artifacts"])
            self.assertIn("verify_file_organization_run", result["next_actions"])

    def test_audit_agent_requires_feedback_for_high_risk_review_queue_items(self):
        from agents import AuditAgent

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_needs_feedback"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            self._write_json(os.path.join(run_dir, "input_manifest.json"), {"schema_version": "file_organization.input_manifest.v1", "run_id": run_id})
            self._write_json(os.path.join(run_dir, "review_queue.json"), {
                "schema_version": "review_queue.v2",
                "run_id": run_id,
                "status": "needs_review",
                "items": [
                    {
                        "id": "R001",
                        "type": "archive_action_review",
                        "risk": "P1",
                        "question": "是否确认归档？",
                        "feedback_type": "archive_decision",
                        "allowed_decisions": ["approve", "reject", "edit_target", "defer"],
                        "recommended_decision": "defer",
                        "evidence": [],
                    },
                    {
                        "id": "R002",
                        "type": "extraction_quality_review",
                        "risk": "P3",
                        "question": "是否修正字段？",
                        "feedback_type": "field_correction",
                        "allowed_decisions": ["accept", "correct", "defer"],
                        "recommended_decision": "accept",
                        "evidence": [],
                    },
                ],
            })
            self._write_json(os.path.join(run_dir, "planned_archive_actions.json"), {"schema_version": "archive_plan.v1", "run_id": run_id, "actions": []})
            self._write_json(os.path.join(run_dir, "trace.json"), {"schema_version": "file_organization.trace.v1", "run_id": run_id, "events": []})
            self._write_json(os.path.join(run_dir, "adversarial_verification.json"), {
                "schema_version": "adversarial_verification.v1",
                "run_id": run_id,
                "overall_verdict": "pass",
                "findings": [],
            })

            result = AuditAgent(workspace_dir=td).review_run(run_id)

            self.assertEqual(result["audit_verdict"], "needs_human_feedback")
            self.assertEqual(result["required_feedback_items"], ["R001"])
            self.assertTrue(result["human_feedback_required"])
            self.assertIn("apply_feedback_decisions", result["next_actions"])
            self.assertTrue(os.path.exists(result["artifact_path"]))
            with open(result["artifact_path"], "r", encoding="utf-8") as f:
                persisted = json.load(f)
            self.assertEqual(persisted["required_feedback_items"], ["R001"])

    def test_file_organization_policy_runs_verify_then_audit(self):
        from planner import RuleBasedPlanner

        goal = "请整理文件并归档 C:\\tmp\\demo.docx"
        planner = RuleBasedPlanner(goal)
        prepare_response = (
            "Thought: prepare\n"
            "Action: prepare_file_organization_run\n"
            "Action Input: {\"file_paths\": [\"C:\\\\tmp\\\\demo.docx\"], \"project_name\": \"\"}"
        )
        verify_response = (
            "Thought: verify\n"
            "Action: verify_file_organization_run\n"
            "Action Input: {\"run_id\": \"run_001\"}"
        )

        response, _ = planner.get_response([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": goal},
            {"role": "assistant", "content": prepare_response},
            {"role": "user", "content": "Observation: {'schema_version': 'file_organization.run.v1', 'run_id': 'run_001', 'status': 'success'}"},
        ])
        self.assertIn("Action: verify_file_organization_run", response)
        self.assertIn('"run_id": "run_001"', response)

        response, _ = planner.get_response([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": goal},
            {"role": "assistant", "content": prepare_response},
            {"role": "user", "content": "Observation: {'schema_version': 'file_organization.run.v1', 'run_id': 'run_001', 'status': 'success'}"},
            {"role": "assistant", "content": verify_response},
            {"role": "user", "content": "Observation: {'schema_version': 'adversarial_verification.v1', 'run_id': 'run_001', 'overall_verdict': 'pass'}"},
        ])
        self.assertIn("Action: audit_file_organization_run", response)
        self.assertIn('"run_id": "run_001"', response)

    @staticmethod
    def _write_json(path, payload):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    unittest.main()
