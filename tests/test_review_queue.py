import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ReviewQueueContractTests(unittest.TestCase):
    def test_adversarial_error_is_actionable(self):
        from contracts.review_queue_schema import normalize_review_queue

        item = normalize_review_queue(
            "run-1", [{"type": "adversarial_verification_error"}],
        )["items"][0]

        self.assertTrue(item["id"])
        self.assertTrue(item["question"])
        self.assertEqual(
            item["allowed_decisions"],
            ["retry_verification", "defer", "accept_risk"],
        )

    def test_relation_and_target_reviews_have_explicit_candidate_only_decisions(self):
        from contracts.review_queue_schema import normalize_review_queue

        queue = normalize_review_queue("run-defaults", [
            {"type": "business_relation_review"},
            {"type": "archive_target_review"},
        ])

        relation, target = queue["items"]
        self.assertTrue(relation["question"])
        self.assertEqual(
            relation["allowed_decisions"],
            ["approve", "reject", "correct_relation", "defer"],
        )
        self.assertTrue(target["question"])
        self.assertEqual(
            target["allowed_decisions"],
            ["approve", "reject", "edit_target", "defer"],
        )

    def test_review_projection_preserves_trace_fields_and_drops_physical_or_secret_text(self):
        from contracts.review_queue_schema import normalize_review_queue

        physical_path = os.path.join(tempfile.gettempdir(), "private", "source.md")
        secret_text = "to" + "ken=" + "synthetic-sensitive-value"
        item = normalize_review_queue("run-projection", [{
            "type": "business_relation_review",
            "source_ref": {
                "storage_provider": "local",
                "object_key": "inbox/source.md",
                "logical_uri": "business://source/inbox/source.md",
                "binding_id": "source",
            },
            "candidate_ids": ["candidate-001"],
            "evidence_refs": [
                {"kind": "business_context", "candidate_id": "candidate-001", "field": "contract_code"},
                {"kind": "unsafe_physical_ref", "ref": physical_path},
            ],
            "conflicts": [{"code": "BUSINESS_CONTEXT.AMBIGUOUS"}],
            "content_hash": "a" * 64,
            "artifact_schema_version": "candidate_document_interpretation.v1",
            "model": "model-v1",
            "prompt_version": "prompt-v1",
            "policy_version": "policy-v1",
            "source_file": physical_path,
            "message": secret_text,
        }])["items"][0]

        self.assertEqual(item["candidate_ids"], ["candidate-001"])
        self.assertEqual(item["evidence_refs"][0]["candidate_id"], "candidate-001")
        self.assertEqual(item["conflicts"], [{"code": "BUSINESS_CONTEXT.AMBIGUOUS"}])
        self.assertEqual(item["source_ref"]["binding_id"], "source")
        self.assertEqual(item["content_hash"], "a" * 64)
        self.assertEqual(item["artifact_schema_version"], "candidate_document_interpretation.v1")
        self.assertNotIn("source_file", item)
        self.assertNotIn("message", item)
        self.assertNotIn(physical_path, str(item))
        self.assertNotIn(secret_text, str(item))

        unsafe_source_ref = normalize_review_queue("run-unsafe-source-ref", [{
            "type": "business_relation_review",
            "source_ref": {
                "storage_provider": "local",
                "object_key": physical_path,
                "logical_uri": "file:///private/source.md",
                "binding_id": "source",
            },
        }])["items"][0]
        self.assertNotIn("source_ref", unsafe_source_ref)
        self.assertNotIn(physical_path, str(unsafe_source_ref))

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

    def test_legacy_preparation_collects_verification_before_one_normalizer_call(self):
        from contracts.review_queue_schema import normalize_review_queue
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "notice.md")
            with open(source, "w", encoding="utf-8") as stream:
                stream.write("synthetic notice")
            tools = DataCleaningTools(workspace_dir=td)
            verification = {
                "schema_version": "adversarial_verification.v1",
                "run_id": "ignored-by-preparation",
                "status": "success",
                "overall_verdict": "needs_human_review",
                "block_reason": "verification_requires_review",
                "low_confidence_items": ["field-1"],
            }
            with (
                patch.object(tools, "_use_archive_metadata_passthrough", return_value=True),
                patch("tools.adversarial_verification.AdversarialVerification.run", return_value=verification),
                patch("tools.data_cleaning_tools.normalize_review_queue", wraps=normalize_review_queue) as normalizer,
            ):
                result = tools.prepare_file_organization_run([source])

            self.assertEqual(normalizer.call_count, 1)
            raw_types = [item["type"] for item in normalizer.call_args.kwargs["raw_items"]]
            self.assertIn("extraction_quality_review", raw_types)
            self.assertIn("adversarial_verification", raw_types)
            for item in result["review_queue"]["items"]:
                self.assertTrue(item["id"])
                self.assertTrue(item["question"])
                self.assertTrue(item["allowed_decisions"])

    def test_task5_preparation_collects_relation_and_target_trace_before_one_normalizer_call(self):
        from contracts.review_queue_schema import normalize_review_queue
        from integrations.document_store import DocumentStoreRouter, LocalDocumentStore
        from services.archive_targets import ArchiveTargetResolver
        from services.document_interpretation import DocumentInterpretationService
        from tests.test_business_judgement_run import (
            SchemaValidInterpreter,
            StaticRetrieval,
            _binding,
            _matched_context,
        )
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_root, target_root, workspace = root / "source", root / "archive", root / "runtime"
            source_root.mkdir()
            target_root.mkdir()
            source = source_root / "contract.md"
            source.write_text("synthetic contract", encoding="utf-8")
            from platform_core.storage_bindings import StorageBindingRegistry

            registry = StorageBindingRegistry([
                _binding("source", source_root, ("source",)),
                _binding("archive", target_root, ("archive_target",), readable=False, writable=True),
            ])
            router = DocumentStoreRouter(registry, {"source": LocalDocumentStore(source_root)})
            retrieval = StaticRetrieval(_matched_context())
            interpretation = DocumentInterpretationService(retrieval, SchemaValidInterpreter())
            tools = DataCleaningTools(
                workspace_dir=str(workspace),
                storage_binding_registry=registry,
                document_store_router=router,
                retrieval_service=retrieval,
                interpretation_service=interpretation,
                archive_target_resolver=ArchiveTargetResolver(registry),
            )

            with patch("tools.data_cleaning_tools.normalize_review_queue", wraps=normalize_review_queue) as normalizer:
                result = tools.prepare_file_organization_run([str(source)])

            self.assertEqual(normalizer.call_count, 1)
            raw_types = [item["type"] for item in normalizer.call_args.kwargs["raw_items"]]
            self.assertEqual(raw_types, ["business_relation_review", "archive_target_review"])
            relation, target = result["review_queue"]["items"]
            self.assertEqual(relation["candidate_ids"], ["C-001"])
            self.assertEqual(relation["evidence_refs"][0]["candidate_id"], "C-001")
            self.assertEqual(relation["source_ref"]["binding_id"], "source")
            self.assertEqual(relation["artifact_schema_version"], "candidate_document_interpretation.v1")
            self.assertEqual(relation["model"], "fake-model")
            self.assertFalse(relation["confirmed"])
            self.assertEqual(target["destination_status"], "unresolved")
            self.assertEqual(target["content_hash"], result["archive_intents"][0]["content_hash"])
            self.assertFalse(target["confirmed"])

    def test_clear_review_queue_keeps_v2_schema_and_empty_items(self):
        from contracts.review_queue_schema import normalize_review_queue

        queue = normalize_review_queue(run_id="run_clear", raw_items=[])

        self.assertEqual(queue["schema_version"], "review_queue.v2")
        self.assertEqual(queue["status"], "clear")
        self.assertEqual(queue["items"], [])


if __name__ == "__main__":
    unittest.main()
