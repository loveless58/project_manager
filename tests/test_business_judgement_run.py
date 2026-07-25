from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from platform_core.models import BusinessContextEvidence
from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry


CANDIDATE_ID = f"C-{1:03d}"
CONTRACT_CODE = f"CT-{1:03d}"


class StaticRetrieval:
    def __init__(self, evidence: BusinessContextEvidence) -> None:
        self.evidence = evidence
        self.calls = 0

    def find_business_candidates(self, query):
        self.calls += 1
        return self.evidence


class SchemaValidInterpreter:
    name = "fake"
    model = "fake-model"
    schema_version = "candidate_document_interpretation.v1"
    prompt_version = "document_interpretation.v1"
    policy_version = "document_interpretation_policy.v1"

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, request):
        self.calls += 1
        return {
            "schema_version": self.schema_version,
            "status": "success",
            "document_type": "contract",
            "fields": {"contract_code": CONTRACT_CODE},
            "relations": [{"relation_type": "contract_project", "target_candidate_id": CANDIDATE_ID}],
            "evidence": [{"kind": "business_context", "candidate_id": CANDIDATE_ID, "field": "contract_code"}],
            "confidence": 0.94,
            "interpreter": self.name,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "policy_version": self.policy_version,
        }


class CountingInterpretation:
    def __init__(self) -> None:
        self.calls = 0

    def interpret(self, evidence_pack):
        self.calls += 1
        raise AssertionError("ambiguous binding must stop before interpretation")


def _binding(binding_id: str, root: Path, roles: tuple[str, ...], *, readable: bool = True,
             writable: bool = False, enabled: bool = True) -> StorageBinding:
    return StorageBinding(binding_id, "local", "node", f"business://{binding_id}/", root,
                          roles, readable, writable, enabled)


def _matched_context() -> BusinessContextEvidence:
    return BusinessContextEvidence(
        "matched",
        ({"id": "C-001", "document_type": "contract", "parties": {},
          "facts": {"contract_code": CONTRACT_CODE}},),
        ({"kind": "business_context", "candidate_id": CANDIDATE_ID, "field": "contract_code"},),
        (),
        ({"code": "BUSINESS_CONTEXT.CANDIDATES_FOUND", "count": 1},),
    )


def test_preparation_builds_review_only_interpretation_and_intent_artifacts(tmp_path: Path) -> None:
    from integrations.document_store import DocumentStoreRouter, LocalDocumentStore
    from services.archive_targets import ArchiveTargetResolver
    from services.document_interpretation import DocumentInterpretationService
    from tools.data_cleaning_tools import DataCleaningTools

    source_root, target_root, workspace = tmp_path / "source", tmp_path / "archive", tmp_path / "runtime"
    source_root.mkdir()
    target_root.mkdir()
    source = source_root / "contract.md"
    original = f"合同\n合同编号：{CONTRACT_CODE}\n项目名称：合成项目001"
    source.write_text(original, encoding="utf-8")
    registry = StorageBindingRegistry([
        _binding("source", source_root, ("source",)),
        _binding("archive", target_root, ("archive_target",), readable=False, writable=True),
    ])
    router = DocumentStoreRouter(registry, {"source": LocalDocumentStore(source_root)})
    retrieval = StaticRetrieval(_matched_context())
    interpreter = SchemaValidInterpreter()
    interpretation = DocumentInterpretationService(retrieval, interpreter)
    tools = DataCleaningTools(
        workspace_dir=str(workspace), storage_binding_registry=registry,
        document_store_router=router, retrieval_service=retrieval,
        interpretation_service=interpretation,
        archive_target_resolver=ArchiveTargetResolver(registry),
    )

    result = tools.prepare_file_organization_run([str(source)])

    assert result["candidate_interpretations"][0]["business_relation"]["candidate_contract_id"] == CANDIDATE_ID
    assert result["archive_intents"][0]["schema_version"] == "archive_intent.v1"
    assert result["archive_intents"][0]["destination_status"] == "unresolved"
    assert result["archive_actions"][0]["status"] == "needs_review"
    assert result["archive_actions"][0]["confirmed"] is False
    assert result["archive_actions"][0]["archive_decision"]["human_review_required"] is True
    assert result["archive_actions"][0]["archive_decision"]["target_dir"] is None
    assert result["archive_actions"][0]["archive_decision"]["target_path"] is None
    assert Path(result["artifacts"]["candidate_interpretations"]).is_file()
    assert Path(result["artifacts"]["archive_intents"]).is_file()
    assert source.read_text(encoding="utf-8") == original
    assert not list(tmp_path.rglob("archive_result.json"))
    assert not list(tmp_path.rglob("project_ledger.json"))
    assert retrieval.calls == 1
    assert interpreter.calls == 1


def test_runtime_workspace_must_stay_outside_storage_bindings(tmp_path: Path) -> None:
    from services.archive_targets import ArchiveTargetResolver
    from tools.data_cleaning_tools import DataCleaningTools

    binding_root = tmp_path / "sync-root"
    workspace = binding_root / "runtime"
    registry = StorageBindingRegistry([
        _binding("source", binding_root, ("source",)),
    ])

    with pytest.raises(ValueError, match="runtime workspace must be outside storage bindings"):
        DataCleaningTools(
            workspace_dir=str(workspace),
            storage_binding_registry=registry,
            archive_target_resolver=ArchiveTargetResolver(registry),
        )


def test_nested_binding_ambiguity_stops_before_parse_retrieval_and_interpretation(tmp_path: Path) -> None:
    from services.archive_targets import ArchiveTargetResolver
    from tools.data_cleaning_tools import DataCleaningTools

    shared, incoming = tmp_path / "shared", tmp_path / "shared" / "incoming"
    incoming.mkdir(parents=True)
    source = incoming / "contract.md"
    source.write_text("合同", encoding="utf-8")
    registry = StorageBindingRegistry([
        _binding("shared", shared, ("source",)),
        _binding("incoming", incoming, ("source",)),
    ])
    retrieval = StaticRetrieval(_matched_context())
    interpretation = CountingInterpretation()
    tools = DataCleaningTools(
        workspace_dir=str(tmp_path / "runtime"), storage_binding_registry=registry,
        retrieval_service=retrieval, interpretation_service=interpretation,
        archive_target_resolver=ArchiveTargetResolver(registry),
    )

    with patch.object(tools, "extract_document", wraps=tools.extract_document) as native_parse:
        result = tools.prepare_file_organization_run([str(source)])

    assert "STORAGE_BINDING.AMBIGUOUS" in json.dumps(result, ensure_ascii=False)
    assert native_parse.call_count == retrieval.calls == interpretation.calls == 0
    assert result["candidate_interpretations"] == []
    assert result["archive_actions"][0]["status"] == "needs_review"
    assert not list(tmp_path.rglob("archive_result.json"))


@pytest.mark.parametrize(
    ("candidate_ids", "binding_options", "expected_status"),
    [
        (("source",), {}, "unresolved"),
        (("SynologyDrive",), {}, "unresolved"),
        (("disabled",), {"enabled": False}, "unresolved"),
        (("readonly",), {"writable": False}, "unresolved"),
        (("wrong-role",), {"roles": ("source",)}, "unresolved"),
        (("archive",), {}, "resolved"),
        (("archive", "archive-2"), {}, "unresolved"),
    ],
)
def test_archive_target_resolution_is_explicit_unique_and_never_defaults(
    tmp_path: Path, candidate_ids: tuple[str, ...], binding_options: dict[str, object],
    expected_status: str,
) -> None:
    from platform_core.models import DocumentRef
    from services.archive_targets import ArchiveTargetResolver

    bindings = [_binding("source", tmp_path / "source", ("source",))]
    for binding_id in {"SynologyDrive", "disabled", "readonly", "wrong-role", "archive", "archive-2"}:
        options = binding_options if binding_id in candidate_ids and len(candidate_ids) == 1 else {}
        bindings.append(_binding(
            binding_id, tmp_path / binding_id, options.get("roles", ("archive_target",)),
            readable=False, writable=options.get("writable", True), enabled=options.get("enabled", True),
        ))
    resolver = ArchiveTargetResolver(StorageBindingRegistry(bindings))
    source_ref = DocumentRef("local", "contract.md", "business://source/contract.md", "source")

    result = resolver.resolve(source_ref, candidate_ids)

    assert result.destination_status == expected_status
    if expected_status == "resolved":
        assert result.candidate_target_binding_ids == ("archive",)
    else:
        assert result.resolved_binding_id == ""
