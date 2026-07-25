import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _register_archive_intent_run(workspace: str | Path, run_id: str) -> None:
    registry = Path(workspace) / "runs" / ".archive_intent_registry"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / f"{run_id}.json").write_text(json.dumps({
        "schema_version": "archive_intent_run_registration.v1",
        "run_id": run_id,
    }), encoding="utf-8")


def _review_item_hash(workspace: str | Path, run_id: str, item_id: str) -> str:
    from contracts.feedback_form_schema import review_item_snapshot_hash

    queue = json.loads(
        (Path(workspace) / "runs" / run_id / "review_queue.json").read_text(encoding="utf-8")
    )
    item = next(item for item in queue["items"] if item.get("id") == item_id)
    return review_item_snapshot_hash(queue, item)


class FeedbackFormTests(unittest.TestCase):
    def test_feedback_reads_verdict_with_explicit_precedence(self):
        from contracts.feedback_form_schema import build_feedback_form

        cases = [
            (
                {
                    "verification_verdict": "explicit",
                    "overall_verdict": "overall",
                    "status": "status",
                },
                "explicit",
            ),
            ({"overall_verdict": "needs_human_review", "status": "success"}, "needs_human_review"),
            ({"status": "blocked"}, "blocked"),
            ({}, ""),
        ]
        for verification, expected in cases:
            with self.subTest(verification=verification):
                form = build_feedback_form(
                    run_id="run-1",
                    review_queue={"items": []},
                    audit_review={},
                    adversarial_verification=verification,
                )
                self.assertEqual(form["verification_verdict"], expected)

    def test_feedback_form_preserves_review_trace_and_binds_snapshot_without_sensitive_paths(self):
        from contracts.feedback_form_schema import build_feedback_form

        physical_path = os.path.join(tempfile.gettempdir(), "private", "source.md")
        review_queue = {
            "schema_version": "review_queue.v2",
            "run_id": "run-trace",
            "status": "needs_review",
            "items": [{
                "id": "R007",
                "run_id": "run-trace",
                "type": "archive_target_review",
                "risk": "P1",
                "question": "Review unresolved candidate target?",
                "feedback_type": "archive_decision",
                "allowed_decisions": ["approve", "reject", "edit_target", "defer"],
                "recommended_decision": "defer",
                "source_ref": {
                    "storage_provider": "local",
                    "object_key": "inbox/source.md",
                    "logical_uri": "business://source/inbox/source.md",
                    "binding_id": "source",
                },
                "candidate_ids": ["candidate-001"],
                "candidate_target_binding_ids": ["archive-candidate"],
                "evidence_refs": [{"kind": "business_context", "candidate_id": "candidate-001", "field": "contract_code"}],
                "conflicts": [{"code": "BUSINESS_CONTEXT.AMBIGUOUS"}],
                "destination_status": "unresolved",
                "content_hash": "b" * 64,
                "artifact_schema_version": "archive_intent.v1",
                "model": "model-v1",
                "prompt_version": "prompt-v1",
                "policy_version": "policy-v1",
                "confirmed": False,
                "source_file": physical_path,
            }],
        }

        form = build_feedback_form(
            run_id="run-trace",
            review_queue=review_queue,
            audit_review={"required_feedback_items": ["R007"]},
            adversarial_verification={},
        )

        item = form["items"][0]
        self.assertEqual(item["item_id"], "R007")
        self.assertEqual(len(item["review_item_hash"]), 64)
        for key in (
            "source_ref", "candidate_ids", "candidate_target_binding_ids",
            "evidence_refs", "conflicts", "destination_status", "content_hash",
            "artifact_schema_version", "model", "prompt_version", "policy_version",
        ):
            self.assertEqual(item[key], review_queue["items"][0][key])
        self.assertFalse(item["confirmed"])
        self.assertEqual(item["source_file"], "")
        self.assertNotIn(physical_path, json.dumps(form, ensure_ascii=False))

    def test_prepare_feedback_form_writes_json_and_markdown_without_side_effects(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_form"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            _register_archive_intent_run(td, run_id)
            self._write_json(os.path.join(run_dir, "review_queue.json"), {
                "schema_version": "review_queue.v2",
                "run_id": run_id,
                "status": "needs_review",
                "items": [
                    {
                        "id": "R001",
                        "run_id": run_id,
                        "risk": "P1",
                        "question": "Approve archive target?",
                        "feedback_type": "archive_decision",
                        "allowed_decisions": ["approve", "reject", "defer"],
                        "recommended_decision": "defer",
                        "source_file": "source.docx",
                        "target_path": "archive/source.docx",
                    }
                ],
            })
            self._write_json(os.path.join(run_dir, "audit_review.json"), {
                "schema_version": "audit_review.v1",
                "run_id": run_id,
                "audit_verdict": "needs_human_feedback",
                "required_feedback_items": ["R001"],
            })
            self._write_json(os.path.join(run_dir, "adversarial_verification.json"), {
                "schema_version": "adversarial_verification.v1",
                "run_id": run_id,
                "verification_verdict": "needs_review",
                "findings": [{"id": "AV001", "severity": "high", "message": "target needs review"}],
            })

            result = DataCleaningTools(workspace_dir=td).prepare_feedback_form(run_id)

            self.assertEqual(result["schema_version"], "feedback_form.prepare.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["item_count"], 1)
            self.assertTrue(os.path.exists(result["artifacts"]["feedback_form_json"]))
            self.assertTrue(os.path.exists(result["artifacts"]["feedback_form_md"]))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "archive_result.json")))

            with open(result["artifacts"]["feedback_form_json"], "r", encoding="utf-8") as f:
                form = json.load(f)
            self.assertEqual(form["schema_version"], "feedback_form.v1")
            self.assertEqual(form["items"][0]["item_id"], "R001")
            self.assertEqual(form["items"][0]["response"]["decision"], "")
            self.assertIn("approve", form["items"][0]["allowed_decisions"])

            markdown = Path(result["artifacts"]["feedback_form_md"]).read_text(encoding="utf-8")
            self.assertIn("R001", markdown)
            self.assertIn("Approve archive target?", markdown)

    def test_apply_feedback_form_converts_simple_responses_and_generates_candidate_tests(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_apply_form"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            _register_archive_intent_run(td, run_id)
            self._write_json(os.path.join(run_dir, "review_queue.json"), {
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
                    }
                ],
            })
            form_path = os.path.join(run_dir, "feedback_form.json")
            self._write_json(form_path, {
                "schema_version": "feedback_form.v1",
                "run_id": run_id,
                "items": [
                    {
                        "item_id": "R001",
                        "feedback_type": "archive_decision",
                        "risk_level": "P1",
                        "review_item_hash": _review_item_hash(td, run_id, "R001"),
                        "source_file": "source.docx",
                        "target_path": "archive/source.docx",
                        "response": {
                            "decision": "approve",
                            "reason": "target matches project folder",
                        },
                    }
                ],
            })

            result = DataCleaningTools(workspace_dir=td).apply_feedback_form(
                run_id=run_id,
                feedback_form_path=form_path,
                generate_tests=True,
            )

            self.assertEqual(result["schema_version"], "feedback_form.apply.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["accepted"], 1)
            self.assertEqual(result["feedback_apply"]["review_queue_updates"]["updated"], 1)
            self.assertTrue(os.path.exists(result["artifacts"]["rule_candidates"]))
            self.assertTrue(os.path.exists(result["artifacts"]["test_manifest"]))

            with open(os.path.join(run_dir, "review_queue.json"), "r", encoding="utf-8") as f:
                queue = json.load(f)
            self.assertEqual(queue["items"][0]["feedback_status"], "feedback_received")

    def test_feedback_form_tools_are_registered_runtime_tools(self):
        import main
        from loop_packages import get_loop_package

        package = get_loop_package("data_cleaning_file_organization")
        self.assertIn("prepare_feedback_form", package.expected_tools)
        self.assertIn("apply_feedback_form", package.expected_tools)

        registry = main._build_registry_for_skill("data_cleaning_file_organization")
        self.assertIn("prepare_feedback_form", registry.list_tools())
        self.assertIn("apply_feedback_form", registry.list_tools())

    def test_review_file_organization_run_script_can_prepare_form(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "review_file_organization_run.py"
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_script_form"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            _register_archive_intent_run(td, run_id)
            self._write_json(os.path.join(run_dir, "review_queue.json"), {
                "schema_version": "review_queue.v2",
                "run_id": run_id,
                "status": "needs_review",
                "items": [{"id": "R001", "run_id": run_id, "feedback_type": "archive_decision"}],
            })

            completed = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-B",
                    str(script),
                    "--workspace",
                    td,
                    "--run-id",
                    run_id,
                    "--prepare-only",
                ],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue(os.path.exists(os.path.join(run_dir, "feedback_form.json")))
            self.assertIn("feedback_form_json", completed.stdout)

    def test_apply_feedback_form_requires_strict_review_item_hash(self):
        from tools.data_cleaning_tools import DataCleaningTools

        for supplied_hash in (None, "", "not-a-sha256", "A" * 64):
            with self.subTest(review_item_hash=supplied_hash), tempfile.TemporaryDirectory() as td:
                run_id = "run-form-hash"
                run_dir = Path(td) / "runs" / run_id
                run_dir.mkdir(parents=True)
                _register_archive_intent_run(td, run_id)
                self._write_json(str(run_dir / "review_queue.json"), {
                    "schema_version": "review_queue.v2",
                    "run_id": run_id,
                    "status": "needs_review",
                    "items": [{
                        "id": "R001",
                        "run_id": run_id,
                        "feedback_type": "archive_decision",
                        "allowed_decisions": ["approve", "reject", "edit_target", "defer"],
                    }],
                })
                item = {
                    "item_id": "R001",
                    "feedback_type": "archive_decision",
                    "response": {"decision": "approve"},
                }
                if supplied_hash is not None:
                    item["review_item_hash"] = supplied_hash
                result = DataCleaningTools(workspace_dir=td).apply_feedback_form(
                    run_id,
                    feedback_form={
                        "schema_version": "feedback_form.v1",
                        "run_id": run_id,
                        "items": [item],
                    },
                    generate_tests=False,
                )

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "invalid_feedback_form")
                self.assertFalse((run_dir / "human_feedback_decisions.json").exists())

    @staticmethod
    def _write_json(path: str, payload: dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    unittest.main()
