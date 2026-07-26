from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from contracts.agent_judgement import canonical_hash
from platform_core.models import BusinessContextEvidence
from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry


def _binding(
    binding_id: str,
    root: Path,
    roles: tuple[str, ...],
    *,
    readable: bool = True,
    writable: bool = False,
) -> StorageBinding:
    return StorageBinding(
        binding_id,
        "local",
        "node",
        f"business://{binding_id}/",
        root,
        roles,
        readable,
        writable,
        True,
    )


class StaticRetrieval:
    def find_business_candidates(self, _query: object) -> BusinessContextEvidence:
        return BusinessContextEvidence(
            "matched",
            (
                {
                    "id": "C-001",
                    "document_type": "contract",
                    "parties": {},
                    "facts": {"contract_code": "SYN-CONTRACT-001"},
                },
                {
                    "id": "C-002",
                    "document_type": "contract",
                    "parties": {},
                    "facts": {"contract_code": "SYN-CONTRACT-002"},
                },
            ),
            (
                {
                    "kind": "business_context",
                    "candidate_id": "C-001",
                    "field": "contract_code",
                },
            ),
            (),
            ({"code": "BUSINESS_CONTEXT.CANDIDATES_FOUND", "count": 1},),
        )


def native_markdown(tmp_path: Path, name: str = "contract.md") -> Path:
    source = tmp_path / "source" / name
    source.parent.mkdir(exist_ok=True)
    source.write_text("合同\n合同编号：SYN-CONTRACT-001\n项目名称：合成项目", encoding="utf-8")
    return source


def agent_mode_tools(tmp_path: Path):
    from integrations.document_store import DocumentStoreRouter, LocalDocumentStore
    from services.agent_judgement_gateway import AgentJudgementGateway
    from services.archive_targets import ArchiveTargetResolver
    from tools.data_cleaning_tools import DataCleaningTools

    source_root = tmp_path / "source"
    source_root.mkdir(exist_ok=True)
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    registry = StorageBindingRegistry(
        [
            _binding("source", source_root, ("source",)),
            _binding(
                "archive",
                archive_root,
                ("archive_target",),
                readable=False,
                writable=True,
            ),
        ]
    )
    return DataCleaningTools(
        workspace_dir=str(tmp_path / "runtime"),
        storage_binding_registry=registry,
        document_store_router=DocumentStoreRouter(
            registry, {"source": LocalDocumentStore(source_root)}
        ),
        retrieval_service=StaticRetrieval(),
        archive_target_resolver=ArchiveTargetResolver(registry),
        agent_judgement_gateway=AgentJudgementGateway(),
    )


def load_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prepared_agent_run(tmp_path: Path, count: int = 1):
    tools = agent_mode_tools(tmp_path)
    files = [str(native_markdown(tmp_path))]
    for index in range(2, count + 1):
        source = native_markdown(tmp_path, f"contract-{index}.md")
        source.write_text(
            f"合同\n合同编号：SYN-CONTRACT-{index:03d}\n项目名称：合成项目{index}", encoding="utf-8"
        )
        files.append(str(source))
    return tools, tools.prepare_agent_judgement_run(files)


def response_exchange_path(prepared: dict[str, Any], name: str) -> Path:
    run_dir = Path(prepared["artifacts"]["run_dir"])
    exchange_dir = run_dir.parents[1] / "agent-host-responses"
    exchange_dir.mkdir(parents=True, exist_ok=True)
    return exchange_dir / name


def prepared_source_files(prepared: dict[str, Any]) -> list[str]:
    run_dir = Path(prepared["artifacts"]["run_dir"])
    source_root = run_dir.parents[2] / "source"
    manifest = load_json(prepared["artifacts"]["input_manifest"])
    return [
        str(source_root / item["name"])
        for item in manifest["files"]
    ]


def matching_response(request: dict[str, Any]) -> dict[str, Any]:
    interpretation_request = request["interpretation_request"]
    return {
        "schema_version": "agent_judgement_response.v1",
        "run_id": request["run_id"],
        "request_id": request["request_id"],
        "request_hash": request["request_hash"],
        "interpreter": "approved_agent",
        "model": "approved_local_model",
        "created_at": "2026-07-26T00:00:00Z",
        "interpretation": {
            "schema_version": "candidate_document_interpretation.v1",
            "status": "success",
            "document_type": interpretation_request["document"]["document_type_hint"],
            "fields": {"contract_code": "SYN-CONTRACT-001"},
            "relations": [
                {
                    "relation_type": "contract_project",
                    "target_candidate_id": "C-001",
                }
            ],
            "evidence": [
                {
                    "kind": "business_context",
                    "candidate_id": "C-001",
                    "field": "contract_code",
                }
            ],
            "confidence": 0.94,
            "interpreter": "approved_agent",
            "model": "approved_local_model",
            "prompt_version": "document_interpretation.v1",
            "policy_version": "document_interpretation_policy.v1",
        },
    }


def write_responses(
    prepared: dict[str, Any],
    path: Path,
    *,
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    request_artifact = load_json(prepared["artifacts"]["agent_judgement_requests"])
    payload = {
        "schema_version": "agent_judgement_responses.v1",
        "run_id": prepared["run_id"],
        "responses": [matching_response(item) for item in request_artifact["requests"]],
    }
    if mutate is not None:
        mutate(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def write_matching_agent_responses(prepared: dict[str, Any]) -> str:
    return write_responses(prepared, response_exchange_path(prepared, "responses.json"))


def write_bad_hash_response(prepared: dict[str, Any]) -> str:
    return write_responses(
        prepared,
        response_exchange_path(prepared, "bad-response.json"),
        mutate=lambda payload: payload["responses"][0].__setitem__("request_hash", "0" * 64),
    )


def artifact_bytes(artifacts: dict[str, str]) -> dict[str, bytes]:
    run_dir = Path(artifacts["run_dir"])
    return {
        str(path.relative_to(run_dir)): path.read_bytes()
        for path in sorted(run_dir.rglob("*"))
        if path.is_file() and not path.name.endswith(".lock")
    }


def test_agent_prepare_writes_safe_requests_and_no_review_or_archive_result(
    tmp_path: Path,
) -> None:
    tools = agent_mode_tools(tmp_path)

    result = tools.prepare_agent_judgement_run([str(native_markdown(tmp_path))])

    request_artifact = load_json(result["artifacts"]["agent_judgement_requests"])
    run_dir = Path(result["artifacts"]["run_dir"])
    assert result["status"] == "awaiting_agent_judgement"
    assert request_artifact["schema_version"] == "agent_judgement_requests.v1"
    assert request_artifact["run_id"] == result["run_id"]
    assert len(request_artifact["requests"]) == 1
    assert "physical_root" not in json.dumps(request_artifact)
    assert not (run_dir / "review_queue.json").exists()
    assert not (run_dir / "planned_archive_actions.json").exists()
    assert not (run_dir / "archive_result.json").exists()


def test_agent_prepare_writes_exact_immutable_input_snapshot(tmp_path: Path) -> None:
    _, prepared = prepared_agent_run(tmp_path)

    snapshot = load_json(prepared["artifacts"]["agent_judgement_input_snapshot"])
    manifest = load_json(prepared["artifacts"]["input_manifest"])
    requests = load_json(prepared["artifacts"]["agent_judgement_requests"])
    manifest_entry = manifest["files"][0]
    request = requests["requests"][0]
    extracted_path = (
        Path(prepared["artifacts"]["extracted_dir"])
        / f"{manifest_entry['content_hash'][:24]}_extracted.json"
    )
    extracted = load_json(str(extracted_path))

    assert snapshot == {
        "schema_version": "agent_judgement_input_snapshot.v1",
        "run_id": prepared["run_id"],
        "items": [
            {
                "request_id": request["request_id"],
                "source_ref": manifest_entry["source_ref"],
                "content_hash": manifest_entry["content_hash"],
                "parse_artifact_ref": extracted["parse_artifact_ref"],
                "extracted_digest": canonical_hash(extracted),
            }
        ],
    }


def test_resume_without_ordered_source_files_is_fail_closed(tmp_path: Path) -> None:
    tools, prepared = prepared_agent_run(tmp_path)
    before = artifact_bytes(prepared["artifacts"])

    result = tools.resume_agent_judgement_run(
        prepared["run_id"], write_matching_agent_responses(prepared)
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "AGENT_JUDGEMENT.RUN_INPUT_INVALID"
    assert artifact_bytes(prepared["artifacts"]) == before


@pytest.mark.parametrize(
    "artifact_name,mutate",
    [
        (
            "input_manifest",
            lambda artifact: artifact["files"][0]["source_ref"].__setitem__(
                "object_key", "replacement.md"
            ),
        ),
        (
            "extracted",
            lambda artifact: artifact.__setitem__(
                "text_length", artifact["text_length"] + 1
            ),
        ),
        (
            "agent_judgement_requests",
            lambda artifact: artifact["requests"][0]["interpretation_request"].__setitem__(
                "parse_artifact_ref", "artifact:parsed:" + "0" * 24
            ),
        ),
    ],
    ids=["manifest source ref", "extracted payload", "request parse ref"],
)
def test_resume_rejects_tampered_phase_one_input_without_artifact_mutation(
    tmp_path: Path,
    artifact_name: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    tools, prepared = prepared_agent_run(tmp_path)
    manifest = load_json(prepared["artifacts"]["input_manifest"])
    if artifact_name == "extracted":
        content_hash = manifest["files"][0]["content_hash"]
        path = (
            Path(prepared["artifacts"]["extracted_dir"])
            / f"{content_hash[:24]}_extracted.json"
        )
    else:
        path = Path(prepared["artifacts"][artifact_name])
    artifact = load_json(str(path))
    mutate(artifact)
    if artifact_name == "agent_judgement_requests":
        request = artifact["requests"][0]
        request["request_hash"] = canonical_hash({
            "schema_version": request["schema_version"],
            "run_id": request["run_id"],
            "request_id": request["request_id"],
            "interpretation_request": request["interpretation_request"],
        })
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    before = artifact_bytes(prepared["artifacts"])

    result = tools.resume_agent_judgement_run(
        prepared["run_id"],
        write_matching_agent_responses(prepared),
        prepared_source_files(prepared),
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "AGENT_JUDGEMENT.RUN_INPUT_INVALID"
    assert artifact_bytes(prepared["artifacts"]) == before


def test_resume_rejects_same_content_from_alternate_logical_source(tmp_path: Path) -> None:
    tools, prepared = prepared_agent_run(tmp_path)
    original = Path(prepared_source_files(prepared)[0])
    alternate = original.with_name("alternate.md")
    alternate.write_bytes(original.read_bytes())
    before = artifact_bytes(prepared["artifacts"])

    result = tools.resume_agent_judgement_run(
        prepared["run_id"],
        write_matching_agent_responses(prepared),
        [str(alternate)],
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "AGENT_JUDGEMENT.RUN_INPUT_INVALID"
    assert artifact_bytes(prepared["artifacts"]) == before


def test_failed_prepare_run_is_marked_and_cannot_be_resumed(tmp_path: Path) -> None:
    tools = agent_mode_tools(tmp_path)
    source = native_markdown(tmp_path)

    result = tools.prepare_agent_judgement_run([str(source), str(source)])

    marker = Path(result["artifacts"]["run_dir"]) / "agent_judgement_failed.json"
    resumed = tools.resume_agent_judgement_run(
        result["run_id"], str(tmp_path / "ignored-response.json")
    )

    assert result["status"] == "blocked"
    assert marker.is_file()
    assert resumed["status"] == "blocked"
    assert resumed["blocked_reason"] == "AGENT_JUDGEMENT.RUN_PREPARATION_FAILED"


def test_resume_accepts_only_exact_response_and_keeps_confirmed_false(tmp_path: Path) -> None:
    tools, prepared = prepared_agent_run(tmp_path)

    result = tools.resume_agent_judgement_run(
        prepared["run_id"],
        write_matching_agent_responses(prepared),
        prepared_source_files(prepared),
    )

    assert result["status"] == "success"
    assert all(item["confirmed"] is False for item in result["archive_actions"])
    assert Path(result["artifacts"]["candidate_interpretations"]).is_file()
    assert Path(result["artifacts"]["review_queue"]).is_file()
    assert Path(result["artifacts"]["feedback_form_json"]).is_file()
    assert Path(result["artifacts"]["feedback_form_md"]).is_file()
    assert not (Path(prepared["artifacts"]["run_dir"]) / "archive_result.json").exists()


def test_resume_propagates_response_consumer_os_error_after_validation(
    tmp_path: Path,
) -> None:
    """A post-validation transaction I/O failure is not an invalid agent response."""
    from services.agent_judgement_gateway import AgentJudgementGateway

    _, prepared = prepared_agent_run(tmp_path)
    run_dir = prepared["artifacts"]["run_dir"]

    def fail_after_validation(*_args: object) -> dict[str, Any]:
        raise OSError("synthetic disk full")

    gateway = AgentJudgementGateway(
        run_dir_resolver=lambda _run_id: run_dir,
        response_consumer=fail_after_validation,
    )

    with pytest.raises(OSError, match="synthetic disk full"):
        gateway.resume_run(
            prepared["run_id"],
            write_matching_agent_responses(prepared),
            prepared_source_files(prepared),
        )


def test_tampered_response_keeps_all_existing_artifacts_byte_identical(tmp_path: Path) -> None:
    tools, prepared = prepared_agent_run(tmp_path)
    before = artifact_bytes(prepared["artifacts"])

    result = tools.resume_agent_judgement_run(
        prepared["run_id"], write_bad_hash_response(prepared)
    )

    assert result["status"] == "blocked"
    assert artifact_bytes(prepared["artifacts"]) == before
    assert not list(Path(prepared["artifacts"]["run_dir"]).glob(".feedback-txn-*"))
    assert not (Path(prepared["artifacts"]["run_dir"]) / "archive_result.json").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["responses"][0].__setitem__("run_id", "run_" + "f" * 32),
        lambda payload: payload["responses"][0]["interpretation"]["relations"][0].__setitem__(
            "target_candidate_id", "C-999"
        ),
        lambda payload: payload["responses"][0]["interpretation"]["evidence"][0].__setitem__(
            "candidate_id", "C-999"
        ),
        lambda payload: payload["responses"][0]["interpretation"]["fields"].__setitem__(
            "contract_name", "C:/physical/source.md"
        ),
        lambda payload: payload["responses"][0]["interpretation"]["fields"].__setitem__(
            "contract_name", "Bearer synthetic-credential"
        ),
        lambda payload: payload["responses"][0].__setitem__("created_at", "2026-02-30T00:00:00Z"),
        lambda payload: payload["responses"][0]["interpretation"]["fields"].__setitem__(
            "contract_code", {"nested": "SYN-CONTRACT-001"}
        ),
        lambda payload: payload["responses"][0]["interpretation"].__setitem__("confirmed", True),
    ],
    ids=[
        "another run",
        "altered candidate id",
        "altered evidence",
        "physical path",
        "credential",
        "invalid created at",
        "nested field",
        "attempted confirmation",
    ],
)
def test_invalid_response_is_fail_closed_without_any_artifact_mutation(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> None:
    tools, prepared = prepared_agent_run(tmp_path)
    before = artifact_bytes(prepared["artifacts"])
    response_path = write_responses(
        prepared, tmp_path / "invalid-response.json", mutate=mutate
    )

    result = tools.resume_agent_judgement_run(prepared["run_id"], response_path)

    assert result["status"] == "blocked"
    assert artifact_bytes(prepared["artifacts"]) == before
    run_dir = Path(prepared["artifacts"]["run_dir"])
    assert not list(run_dir.glob(".feedback-txn-*"))
    assert not (run_dir / "archive_result.json").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["responses"][0]["interpretation"].__setitem__(
            "prompt_version", "other_prompt.v1"
        ),
        lambda payload: payload["responses"][0]["interpretation"].__setitem__(
            "policy_version", "other_policy.v1"
        ),
        lambda payload: payload["responses"][0]["interpretation"]["relations"][0].__setitem__(
            "target_candidate_id", "C-002"
        ),
    ],
    ids=["wrong prompt version", "wrong policy version", "relation without matching evidence"],
)
def test_response_semantic_mismatch_is_fail_closed_before_review_transaction(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> None:
    """A parser-legal envelope must not become a blocked review artifact."""
    tools, prepared = prepared_agent_run(tmp_path)
    before = artifact_bytes(prepared["artifacts"])
    response_path = write_responses(
        prepared, tmp_path / "semantic-mismatch.json", mutate=mutate
    )

    result = tools.resume_agent_judgement_run(prepared["run_id"], response_path)

    assert result["status"] == "blocked"
    assert artifact_bytes(prepared["artifacts"]) == before
    run_dir = Path(prepared["artifacts"]["run_dir"])
    assert not list(run_dir.glob(".feedback-txn-*"))
    assert not (run_dir / "review_queue.json").exists()


def test_second_different_valid_response_cannot_overwrite_consumed_review_artifacts(
    tmp_path: Path,
) -> None:
    """The first valid response consumes a run exactly once."""
    tools, prepared = prepared_agent_run(tmp_path)
    first = tools.resume_agent_judgement_run(
        prepared["run_id"],
        write_matching_agent_responses(prepared),
        prepared_source_files(prepared),
    )
    before = artifact_bytes(prepared["artifacts"])
    changed_response_path = write_responses(
        prepared,
        tmp_path / "different-valid-response.json",
        mutate=lambda payload: (
            payload["responses"][0].__setitem__("model", "different_local_model"),
            payload["responses"][0]["interpretation"].__setitem__(
                "model", "different_local_model"
            ),
        ),
    )

    second = tools.resume_agent_judgement_run(
        prepared["run_id"],
        changed_response_path,
        prepared_source_files(prepared),
    )

    assert first["status"] == "success"
    assert second["status"] == "blocked"
    assert artifact_bytes(prepared["artifacts"]) == before
    assert not list(Path(prepared["artifacts"]["run_dir"]).glob(".feedback-txn-*"))


def test_duplicate_and_reordered_responses_are_fail_closed(tmp_path: Path) -> None:
    tools, prepared = prepared_agent_run(tmp_path, count=2)
    before = artifact_bytes(prepared["artifacts"])

    duplicate_path = write_responses(
        prepared,
        tmp_path / "duplicate.json",
        mutate=lambda payload: payload["responses"].__setitem__(
            1, copy.deepcopy(payload["responses"][0])
        ),
    )
    duplicate = tools.resume_agent_judgement_run(prepared["run_id"], duplicate_path)

    assert duplicate["status"] == "blocked"
    assert artifact_bytes(prepared["artifacts"]) == before

    reorder_path = write_responses(
        prepared,
        tmp_path / "reordered.json",
        mutate=lambda payload: payload.__setitem__(
            "responses", list(reversed(payload["responses"]))
        ),
    )
    reordered = tools.resume_agent_judgement_run(prepared["run_id"], reorder_path)

    assert reordered["status"] == "blocked"
    assert artifact_bytes(prepared["artifacts"]) == before


def test_missing_response_is_capability_disabled_without_side_effects(tmp_path: Path) -> None:
    tools, prepared = prepared_agent_run(tmp_path)
    before = artifact_bytes(prepared["artifacts"])

    result = tools.resume_agent_judgement_run(
        prepared["run_id"],
        str(tmp_path / "not-supplied.json"),
        prepared_source_files(prepared),
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "LLM.CAPABILITY_DISABLED"
    assert artifact_bytes(prepared["artifacts"]) == before


def test_resume_cannot_execute_archive_actions_even_when_execution_is_requested(
    tmp_path: Path,
) -> None:
    tools, prepared = prepared_agent_run(tmp_path)
    resumed = tools.resume_agent_judgement_run(
        prepared["run_id"],
        write_matching_agent_responses(prepared),
        prepared_source_files(prepared),
    )

    result = tools.execute_archive_plan(prepared["run_id"], confirmed=True)

    assert resumed["status"] == "success"
    assert result["status"] == "blocked"
    assert not (Path(prepared["artifacts"]["run_dir"]) / "archive_result.json").exists()
