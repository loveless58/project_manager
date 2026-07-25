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
                "new_value": "虚构人员002",
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
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            with open(os.path.join(run_dir, "review_queue.json"), "w", encoding="utf-8") as stream:
                json.dump({
                    "schema_version": "review_queue.v2",
                    "run_id": run_id,
                    "status": "needs_review",
                    "items": [
                        {
                            "id": "R001", "run_id": run_id,
                            "feedback_type": "field_correction",
                            "allowed_decisions": ["correct", "defer"],
                        },
                        {
                            "id": "R002", "run_id": run_id,
                            "feedback_type": "archive_decision",
                            "allowed_decisions": ["approve", "reject", "defer"],
                        },
                        {
                            "id": "R003", "run_id": run_id,
                            "feedback_type": "parser_case",
                            "allowed_decisions": ["add_parser_case", "defer"],
                        },
                    ],
                }, stream)
            tools = DataCleaningTools(workspace_dir=td)

            result = tools.apply_feedback_decisions(
                run_id=run_id,
                feedback_decisions=[
                    {
                        "feedback_type": "field_correction",
                        "item_id": "R001",
                        "field": "sales_person",
                        "old_value": "领导",
                        "new_value": "虚构人员002",
                        "evidence_text": "销售负责人：虚构人员002",
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
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            with open(os.path.join(run_dir, "review_queue.json"), "w", encoding="utf-8") as stream:
                json.dump({
                    "schema_version": "review_queue.v2",
                    "run_id": run_id,
                    "status": "needs_review",
                    "items": [{
                        "id": "R001", "run_id": run_id,
                        "feedback_type": "false_positive",
                        "allowed_decisions": ["mark_false_positive", "defer"],
                    }],
                }, stream)
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
                                "allowed_decisions": ["approve", "reject", "defer"],
                                "recommended_decision": "defer",
                            },
                            {
                                "id": "R002",
                                "run_id": run_id,
                                "risk": "P3",
                                "feedback_type": "parser_case",
                                "allowed_decisions": ["add_parser_case", "defer"],
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

    def test_feedback_rejects_unknown_cross_run_and_disallowed_decisions_without_writes(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run-boundary"
            run_dir = Path(td) / "runs" / run_id
            run_dir.mkdir(parents=True)
            queue_path = run_dir / "review_queue.json"
            queue_path.write_text(json.dumps({
                "schema_version": "review_queue.v2",
                "run_id": run_id,
                "status": "needs_review",
                "items": [{
                    "id": "R001",
                    "run_id": run_id,
                    "feedback_type": "archive_decision",
                    "allowed_decisions": ["approve", "reject", "defer"],
                }],
            }), encoding="utf-8")
            before = queue_path.read_bytes()

            result = DataCleaningTools(workspace_dir=td).apply_feedback_decisions(
                run_id,
                [
                    {
                        "feedback_id": "FB-unknown",
                        "feedback_type": "archive_decision",
                        "item_id": "R404",
                        "decision": "approve",
                    },
                    {
                        "feedback_id": "FB-cross-run",
                        "run_id": "run-other",
                        "feedback_type": "archive_decision",
                        "item_id": "R001",
                        "decision": "approve",
                    },
                    {
                        "feedback_id": "FB-disallowed",
                        "feedback_type": "archive_decision",
                        "item_id": "R001",
                        "decision": "force_ready",
                    },
                ],
            )

            self.assertEqual(result["accepted"], 0)
            self.assertEqual(result["failed"], 3)
            self.assertEqual(
                [error["error"] for error in result["errors"]],
                ["unknown_feedback_item", "feedback_run_mismatch", "decision_not_allowed"],
            )
            self.assertEqual(queue_path.read_bytes(), before)
            self.assertFalse((run_dir / "human_feedback_decisions.json").exists())
            self.assertFalse((run_dir / "feedback_events.jsonl").exists())

    def test_feedback_replay_is_idempotent_and_conflicts_are_rejected(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run-replay"
            run_dir = Path(td) / "runs" / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "review_queue.json").write_text(json.dumps({
                "schema_version": "review_queue.v2",
                "run_id": run_id,
                "status": "needs_review",
                "items": [{
                    "id": "R001",
                    "run_id": run_id,
                    "feedback_type": "archive_decision",
                    "allowed_decisions": ["approve", "reject", "defer"],
                }],
            }), encoding="utf-8")
            tools = DataCleaningTools(workspace_dir=td)
            decision = {
                "feedback_id": "FB-replay",
                "run_id": run_id,
                "feedback_type": "archive_decision",
                "item_id": "R001",
                "decision": "approve",
                "reason": "candidate target acknowledged",
            }

            first = tools.apply_feedback_decisions(run_id, [decision])
            replay = tools.apply_feedback_decisions(run_id, [dict(decision)])
            changed = tools.apply_feedback_decisions(run_id, [{**decision, "decision": "reject"}])
            contradictory = tools.apply_feedback_decisions(run_id, [{
                **decision,
                "feedback_id": "FB-contradictory",
                "decision": "reject",
            }])

            self.assertEqual(first["accepted"], 1)
            self.assertEqual(replay["accepted"], 0)
            self.assertEqual(replay["duplicates"], 1)
            self.assertEqual(changed["errors"][0]["error"], "feedback_id_conflict")
            self.assertEqual(contradictory["errors"][0]["error"], "feedback_item_conflict")
            persisted = json.loads((run_dir / "human_feedback_decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(len(persisted["decisions"]), 1)
            self.assertEqual(persisted["decisions"][0]["feedback_id"], "FB-replay")
            events = [
                json.loads(line)
                for line in (run_dir / "feedback_events.jsonl").read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual(len(events), 1)
            queue = json.loads((run_dir / "review_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(queue["items"][0]["feedback_ids"], ["FB-replay"])
            self.assertEqual(queue["items"][0]["feedback_decisions"], ["approve"])

    def test_feedback_is_candidate_only_and_does_not_mutate_authority_artifacts(self):
        from contracts.review_queue_schema import normalize_review_queue
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "run-authority"
            run_dir = root / "runs" / run_id
            run_dir.mkdir(parents=True)
            source = root / "source.md"
            source.write_text("immutable source", encoding="utf-8")
            authority_files = {
                "archive_intents.json": b'{"schema_version":"archive_intents.v1"}',
                "planned_archive_actions.json": b'{"schema_version":"archive_plan.v1"}',
                "project_ledger.json": b'{"schema_version":"project_ledger.v1"}',
                "projection.json": b'{"schema_version":"projection.v1"}',
            }
            for name, content in authority_files.items():
                (run_dir / name).write_bytes(content)
            queue = normalize_review_queue(run_id, [{
                "type": "archive_target_review",
                "source_ref": {
                    "storage_provider": "local",
                    "object_key": "inbox/source.md",
                    "logical_uri": "business://source/inbox/source.md",
                    "binding_id": "source",
                },
                "candidate_target_binding_ids": ["archive-candidate"],
                "destination_status": "unresolved",
                "content_hash": "c" * 64,
                "artifact_schema_version": "archive_intent.v1",
                "confirmed": False,
            }])
            (run_dir / "review_queue.json").write_text(json.dumps(queue), encoding="utf-8")
            before = {name: (run_dir / name).read_bytes() for name in authority_files}
            source_before = source.read_bytes()

            result = DataCleaningTools(workspace_dir=td).apply_feedback_decisions(run_id, [{
                "feedback_id": "FB-authority",
                "run_id": run_id,
                "feedback_type": "archive_decision",
                "item_id": "R001",
                "decision": "approve",
            }])

            self.assertEqual(result["accepted"], 1)
            self.assertFalse(result["decisions"][0]["confirmed"])
            self.assertEqual(result["decisions"][0]["destination_status"], "unresolved")
            self.assertEqual(result["decisions"][0]["content_hash"], "c" * 64)
            self.assertEqual(source.read_bytes(), source_before)
            self.assertEqual(
                {name: (run_dir / name).read_bytes() for name in authority_files},
                before,
            )
            self.assertFalse((run_dir / "archive_result.json").exists())
            updated_queue = json.loads((run_dir / "review_queue.json").read_text(encoding="utf-8"))
            self.assertFalse(updated_queue["items"][0]["confirmed"])
            self.assertEqual(updated_queue["items"][0]["destination_status"], "unresolved")

    def test_feedback_rejects_unknown_fields_non_finite_numbers_and_oversize_payloads(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run-strict"
            run_dir = Path(td) / "runs" / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "review_queue.json").write_text(json.dumps({
                "schema_version": "review_queue.v2",
                "run_id": run_id,
                "status": "needs_review",
                "items": [{
                    "id": f"R{index:03d}",
                    "run_id": run_id,
                    "feedback_type": "archive_decision",
                    "allowed_decisions": ["approve", "reject", "defer"],
                } for index in range(1, 4)],
            }), encoding="utf-8")

            result = DataCleaningTools(workspace_dir=td).apply_feedback_decisions(run_id, [
                {
                    "feedback_id": "FB-unknown-field",
                    "feedback_type": "archive_decision",
                    "item_id": "R001",
                    "decision": "approve",
                    "unexpected": "value",
                },
                {
                    "feedback_id": "FB-non-finite",
                    "feedback_type": "archive_decision",
                    "item_id": "R002",
                    "decision": "approve",
                    "old_value": float("nan"),
                },
                {
                    "feedback_id": "FB-oversize",
                    "feedback_type": "archive_decision",
                    "item_id": "R003",
                    "decision": "approve",
                    "reason": "x" * (130 * 1024),
                },
            ])

            self.assertEqual(result["accepted"], 0)
            self.assertEqual(
                [error["error"] for error in result["errors"]],
                ["unknown_feedback_field", "non_finite_feedback_value", "feedback_size_limit"],
            )
            self.assertFalse((run_dir / "human_feedback_decisions.json").exists())

    def test_feedback_form_json_rejects_duplicate_keys_before_writing_artifacts(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run-duplicate-json"
            run_dir = Path(td) / "runs" / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "review_queue.json").write_text(json.dumps({
                "schema_version": "review_queue.v2",
                "run_id": run_id,
                "status": "needs_review",
                "items": [{
                    "id": "R001",
                    "run_id": run_id,
                    "feedback_type": "archive_decision",
                    "allowed_decisions": ["approve", "reject", "defer"],
                }],
            }), encoding="utf-8")
            form_path = run_dir / "feedback_form.json"
            form_path.write_text(
                '{"schema_version":"feedback_form.v1","run_id":"run-duplicate-json",'
                '"run_id":"run-duplicate-json","items":[]}',
                encoding="utf-8",
            )

            result = DataCleaningTools(workspace_dir=td).apply_feedback_form(
                run_id,
                feedback_form_path=str(form_path),
                generate_tests=False,
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"], "invalid_feedback_form")
            self.assertFalse((run_dir / "human_feedback_decisions.json").exists())

    def test_apply_feedback_decisions_is_registered_runtime_tool(self):
        import main
        from loop_packages import get_loop_package

        package = get_loop_package("data_cleaning_file_organization")
        self.assertIn("apply_feedback_decisions", package.expected_tools)

        registry = main._build_registry_for_skill("data_cleaning_file_organization")
        self.assertIn("apply_feedback_decisions", registry.list_tools())


if __name__ == "__main__":
    unittest.main()
