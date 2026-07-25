import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class CandidateTestGenerationTests(unittest.TestCase):
    def test_generate_candidate_tests_writes_runnable_drafts_inside_run_package(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            run_id = "run_l36"
            run_dir = os.path.join(td, "runs", run_id)
            os.makedirs(run_dir)
            registry = Path(td) / "runs" / ".archive_intent_registry"
            registry.mkdir(parents=True)
            (registry / f"{run_id}.json").write_text(json.dumps({
                "schema_version": "archive_intent_run_registration.v1",
                "run_id": run_id,
            }), encoding="utf-8")
            (Path(run_dir) / "review_queue.json").write_text(json.dumps({
                "schema_version": "review_queue.v2",
                "run_id": run_id,
                "status": "clear",
                "items": [],
            }), encoding="utf-8")
            parser_candidates_path = os.path.join(run_dir, "parser_test_candidates.json")
            rule_candidates_path = os.path.join(run_dir, "rule_candidates.json")
            with open(parser_candidates_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "schema_version": "parser_test_candidates.v1",
                        "run_id": run_id,
                        "items": [
                            {
                                "schema_version": "parser_test_candidate.v1",
                                "feedback_id": "FB001",
                                "run_id": run_id,
                                "item_id": "R001",
                                "field": "contract_amount",
                                "input_pattern": "合同金额：12.5万元",
                                "expected_value": "125000",
                                "status": "pending_test_authoring",
                            }
                        ],
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            with open(rule_candidates_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "schema_version": "rule_candidates.v1",
                        "run_id": run_id,
                        "items": [
                            {
                                "schema_version": "rule_candidate.v1",
                                "feedback_id": "FB002",
                                "feedback_type": "archive_decision",
                                "run_id": run_id,
                                "item_id": "R002",
                                "decision": "approve",
                                "risk_level": "P1",
                                "requires_test": True,
                                "status": "pending_rule_approval",
                            }
                        ],
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            result = DataCleaningTools(workspace_dir=td).generate_candidate_tests(run_id)

            self.assertEqual(result["schema_version"], "candidate_test_generation.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["parser_candidate_count"], 1)
            self.assertEqual(result["rule_candidate_count"], 1)
            self.assertEqual(
                result["next_actions"],
                ["review_generated_tests", "promote_approved_tests_to_repo"],
            )

            parser_test_path = result["artifacts"]["parser_candidate_tests"]
            rule_test_path = result["artifacts"]["rule_candidate_tests"]
            manifest_path = result["artifacts"]["test_manifest"]
            self.assertTrue(os.path.exists(parser_test_path))
            self.assertTrue(os.path.exists(rule_test_path))
            self.assertTrue(os.path.exists(manifest_path))
            self.assertIn(
                os.path.join("runs", run_id, "generated_tests"),
                parser_test_path,
            )

            parser_test_source = Path(parser_test_path).read_text(encoding="utf-8")
            rule_test_source = Path(rule_test_path).read_text(encoding="utf-8")
            self.assertIn("contract_amount", parser_test_source)
            self.assertIn("archive_decision", rule_test_source)

            for draft_path in [parser_test_path, rule_test_path]:
                completed = subprocess.run(
                    [sys.executable, "-X", "utf8", "-B", draft_path],
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )

            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["schema_version"], "candidate_test_generation.v1")
            self.assertEqual(manifest["run_id"], run_id)

    def test_generate_candidate_tests_is_registered_runtime_tool(self):
        import main
        from loop_packages import get_loop_package

        package = get_loop_package("data_cleaning_file_organization")
        self.assertIn("generate_candidate_tests", package.expected_tools)

        registry = main._build_registry_for_skill("data_cleaning_file_organization")
        self.assertIn("generate_candidate_tests", registry.list_tools())


if __name__ == "__main__":
    unittest.main()
