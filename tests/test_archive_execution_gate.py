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

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["moved"], 0)
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
                            "type": "archive_action_review",
                            "feedback_type": "archive_decision",
                            "allowed_decisions": ["approve", "reject", "edit_target", "defer"],
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
                            "type": "archive_action_review",
                            "feedback_type": "archive_decision",
                            "allowed_decisions": ["approve", "reject", "edit_target", "defer"],
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

    def test_forged_resolved_archive_intent_is_never_executable(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_0123456789abcdef0123456789abcdef"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            source_path = os.path.join(td, "source.docx")
            target_path = os.path.join(td, "archive", "source.docx")
            Path(source_path).write_text("source", encoding="utf-8")
            self._write_json(os.path.join(run_dir, "planned_archive_actions.json"), {
                "schema_version": "archive_plan.v1", "run_id": run_id,
                "archive_intent_required": True,
                "actions": [{
                    "schema_version": "archive_action.v1", "run_id": run_id,
                    "source_file": source_path, "target_path": target_path,
                    "status": "ready", "confirmed": True,
                    "archive_intent": {
                        "schema_version": "archive_intent.v1",
                        "destination_status": "resolved",
                    },
                    "blockers": [],
                }],
            })

            result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["moved"], 0)
            self.assertTrue(os.path.exists(source_path))
            self.assertFalse(os.path.exists(target_path))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "archive_result.json")))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "archive_execution_gate.json")))

    def test_execute_archive_plan_rejects_unsafe_run_ids_before_filesystem_access(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            for run_id in (
                "../outside", r"..\outside", str(Path(td).resolve()),
                r"C:\outside", "run／outside", "run＼outside",
            ):
                result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["moved"], 0)
            self.assertFalse((Path(td) / "archive_result.json").exists())

    def test_execute_archive_plan_rejects_plan_run_id_mismatch(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_11111111111111111111111111111111"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            source_path = os.path.join(td, "source.docx")
            target_path = os.path.join(td, "archive", "source.docx")
            Path(source_path).write_text("source", encoding="utf-8")
            self._write_json(os.path.join(run_dir, "planned_archive_actions.json"), {
                "schema_version": "archive_plan.v1",
                "run_id": "run_22222222222222222222222222222222",
                "actions": [{
                    "status": "ready", "source_file": source_path,
                    "target_path": target_path, "blockers": [],
                }],
            })

            result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

            self.assertEqual(result["status"], "blocked")
            self.assertTrue(os.path.exists(source_path))
            self.assertFalse(os.path.exists(target_path))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "archive_result.json")))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_execute_archive_plan_rejects_symlinked_run_directory(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
            run_id = "run_33333333333333333333333333333333"
            runs_dir = Path(td) / "runs"
            runs_dir.mkdir()
            outside_run = Path(outside) / run_id
            outside_run.mkdir()
            self._write_json(str(outside_run / "planned_archive_actions.json"), {
                "schema_version": "archive_plan.v1", "run_id": run_id, "actions": [],
            })
            try:
                os.symlink(outside_run, runs_dir / run_id, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

            self.assertEqual(result["status"], "blocked")
            self.assertFalse((outside_run / "archive_result.json").exists())

    def test_execute_archive_plan_rejects_duplicate_keys_and_nonfinite_json(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source_path = os.path.join(td, "source.docx")
            Path(source_path).write_text("source", encoding="utf-8")
            for index, fragment in enumerate((
                '"run_id":"run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","run_id":"run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
                '"run_id":"run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","score":NaN',
                '"run_id":"run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","score":Infinity',
            )):
                run_id = "run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                run_dir = Path(td) / "runs" / run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                plan = run_dir / "planned_archive_actions.json"
                plan.write_text(
                    '{"schema_version":"archive_plan.v1",' + fragment + ',"actions":[]}',
                    encoding="utf-8",
                )

                result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

                self.assertEqual(result["status"], "blocked", index)
                self.assertIn("archive_plan_invalid", result["gate"]["blockers"])
                self.assertTrue(os.path.exists(source_path))
                self.assertFalse((run_dir / "archive_result.json").exists())

    def test_execute_archive_plan_rejects_gate_artifact_run_id_mismatch_without_writing_gate(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_gate_artifact_mismatch"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            source_path = os.path.join(td, "source.docx")
            target_path = os.path.join(td, "archive", "source.docx")
            Path(source_path).write_text("source", encoding="utf-8")
            self._write_json(os.path.join(run_dir, "planned_archive_actions.json"), {
                "schema_version": "archive_plan.v1", "run_id": run_id,
                "actions": [{
                    "status": "ready", "source_file": source_path,
                    "target_path": target_path, "blockers": [],
                }],
            })
            self._write_json(os.path.join(run_dir, "audit_review.json"), {
                "schema_version": "audit_review.v1", "run_id": "different_run",
                "human_feedback_required": False, "required_feedback_items": [],
            })

            result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

            self.assertEqual(result["status"], "blocked")
            self.assertIn("artifact_run_id_mismatch", result["gate"]["blockers"])
            self.assertTrue(os.path.exists(source_path))
            self.assertFalse(os.path.exists(target_path))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "archive_execution_gate.json")))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "archive_result.json")))

    def test_execute_archive_plan_validates_present_empty_gate_artifacts_before_execution(self):
        from tools.data_cleaning_tools import DataCleaningTools

        malformed_roots = ({}, [], None, "wrong-root")
        for artifact_name in ("audit_review.json", "review_queue.json"):
            for case_index, payload in enumerate(malformed_roots):
                with self.subTest(artifact_name=artifact_name, payload=payload), tempfile.TemporaryDirectory() as td:
                    run_id = f"run_empty_{artifact_name.split('.')[0]}_{case_index}"
                    run_dir = Path(td) / "runs" / run_id
                    run_dir.mkdir(parents=True)
                    source = Path(td) / "source.docx"
                    target = Path(td) / "archive" / "source.docx"
                    source.write_text("source", encoding="utf-8")
                    self._write_json(str(run_dir / "planned_archive_actions.json"), {
                        "schema_version": "archive_plan.v1", "run_id": run_id,
                        "actions": [{
                            "status": "ready", "source_file": str(source),
                            "target_path": str(target), "blockers": [],
                        }],
                    })
                    self._write_json(str(run_dir / artifact_name), payload)

                    result = DataCleaningTools(workspace_dir=td).execute_archive_plan(
                        run_id, confirmed=True,
                    )

                    self.assertEqual(result["status"], "blocked")
                    self.assertEqual(result["moved"], 0)
                    self.assertTrue(source.exists())
                    self.assertFalse(target.exists())
                    self.assertFalse((run_dir / "archive_execution_gate.json").exists())
                    self.assertFalse((run_dir / "archive_result.json").exists())

    def test_execute_archive_plan_rejects_out_of_range_json_integers_before_gate(self):
        from tools.data_cleaning_tools import DataCleaningTools

        first_out_of_range = 1_000_000_000_000_000_001
        for value in (first_out_of_range, -first_out_of_range):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as td:
                run_id = "run_integer_boundary"
                run_dir = Path(td) / "runs" / run_id
                run_dir.mkdir(parents=True)
                source = Path(td) / "source.docx"
                target = Path(td) / "archive" / "source.docx"
                source.write_text("source", encoding="utf-8")
                self._write_json(str(run_dir / "planned_archive_actions.json"), {
                    "schema_version": "archive_plan.v1", "run_id": run_id,
                    "actions": [{
                        "status": "ready", "source_file": str(source),
                        "target_path": str(target), "blockers": [],
                        "business_judgement": {"evidence_count": value},
                    }],
                })

                result = DataCleaningTools(workspace_dir=td).execute_archive_plan(
                    run_id, confirmed=True,
                )

                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["moved"], 0)
                self.assertTrue(source.exists())
                self.assertFalse(target.exists())
                self.assertFalse((run_dir / "archive_execution_gate.json").exists())
                self.assertFalse((run_dir / "archive_result.json").exists())

    def test_execute_archive_plan_rejects_invalid_legacy_artifact_schemas_without_side_effects(self):
        from tools.data_cleaning_tools import DataCleaningTools

        cases = (
            ("plan_unknown", "plan", {"unexpected": True}),
            ("plan_version", "plan", {"schema_version": "archive_plan.v0"}),
            ("action_unknown", "action", {"unexpected": True}),
            ("action_blockers", "action", {"blockers": None}),
            ("audit_unknown", "audit", {"unexpected": True}),
            ("audit_version", "audit", {"schema_version": "audit_review.v0"}),
            ("audit_type", "audit", {"human_feedback_required": "yes"}),
            ("queue_unknown", "queue", {"unexpected": True}),
            ("queue_version", "queue", {"schema_version": "review_queue.v1"}),
            ("queue_items_type", "queue", {"items": {}}),
            ("queue_item_unknown", "queue_item", {"unexpected": True}),
        )
        for name, artifact_kind, changes in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                run_id = f"run_schema_{name}"
                run_dir = Path(td) / "runs" / run_id
                run_dir.mkdir(parents=True)
                source = Path(td) / "source.docx"
                target = Path(td) / "archive" / "source.docx"
                source.write_text("source", encoding="utf-8")
                action = {
                    "status": "ready", "source_file": str(source),
                    "target_path": str(target), "blockers": [],
                }
                plan = {"schema_version": "archive_plan.v1", "run_id": run_id, "actions": [action]}
                audit = {
                    "schema_version": "audit_review.v1", "run_id": run_id,
                    "audit_verdict": "acceptable", "human_feedback_required": False,
                    "required_feedback_items": [],
                }
                queue = {
                    "schema_version": "review_queue.v2", "run_id": run_id,
                    "status": "clear", "items": [],
                }
                if artifact_kind == "plan":
                    plan.update(changes)
                elif artifact_kind == "action":
                    action.update(changes)
                elif artifact_kind == "audit":
                    audit.update(changes)
                elif artifact_kind == "queue":
                    queue.update(changes)
                else:
                    queue["status"] = "needs_review"
                    queue["items"] = [{
                        "id": "R001", "run_id": run_id, "risk": "P1",
                        "recommended_decision": "defer", "feedback_status": "pending",
                        **changes,
                    }]
                self._write_json(str(run_dir / "planned_archive_actions.json"), plan)
                self._write_json(str(run_dir / "audit_review.json"), audit)
                self._write_json(str(run_dir / "review_queue.json"), queue)

                result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["moved"], 0)
                self.assertTrue(source.exists())
                self.assertFalse(target.exists())
                self.assertFalse((run_dir / "archive_execution_gate.json").exists())
                self.assertFalse((run_dir / "archive_result.json").exists())

    def test_execute_archive_plan_rejects_oversized_json_and_huge_integer_before_gate(self):
        from tools.data_cleaning_tools import DataCleaningTools

        for name in ("oversized_bytes", "huge_integer"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                run_id = f"run_{name}"
                run_dir = Path(td) / "runs" / run_id
                run_dir.mkdir(parents=True)
                source = Path(td) / "source.docx"
                target = Path(td) / "archive" / "source.docx"
                source.write_text("source", encoding="utf-8")
                if name == "oversized_bytes":
                    raw = (
                        '{"schema_version":"archive_plan.v1","run_id":"' + run_id
                        + '","actions":[' + " " * 300_000 + ']} '
                    ).encode("utf-8")
                else:
                    raw = (
                        '{"schema_version":"archive_plan.v1","run_id":"' + run_id
                        + '","actions":[{"status":"ready","source_file":'
                        + json.dumps(str(source)) + ',"target_path":' + json.dumps(str(target))
                        + ',"blockers":[],"confirmed":' + "9" * 4_000 + '}]} '
                    ).encode("utf-8")
                (run_dir / "planned_archive_actions.json").write_bytes(raw)

                result = DataCleaningTools(workspace_dir=td).execute_archive_plan(run_id, confirmed=True)

                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["moved"], 0)
                self.assertTrue(source.exists())
                self.assertFalse(target.exists())
                self.assertFalse((run_dir / "archive_execution_gate.json").exists())
                self.assertFalse((run_dir / "archive_result.json").exists())

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
