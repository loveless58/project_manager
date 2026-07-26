from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType

import pytest

from contracts.agent_judgement import (
    AgentJudgementSchemaError,
    REQUEST_SCHEMA_VERSION,
    RESPONSE_SCHEMA_VERSION,
    build_agent_judgement_request,
    parse_agent_judgement_response,
)


def _joined_sample(*parts: str) -> str:
    return "".join(parts)


def _sensitive_mapping(field: str, value: object) -> dict[str, object]:
    return {field: value}


VALID_INTERPRETATION_REQUEST = {
    "schema_version": "document_interpretation_evidence_pack.v1",
    "parse_artifact_ref": "artifact:parsed:invoice-001",
    "document": {
        "document_type_hint": "invoice",
        "candidate_fields": {"contract_code": "SYN-CONTRACT-001"},
        "text_segments": [{"id": "page-1", "text": "Synthetic document"}],
    },
    "business_context": {
        "status": "matched",
        "candidates": [{"id": "C-001", "document_type": "contract"}],
        "evidence": [
            {
                "kind": "business_context",
                "candidate_id": "C-001",
                "field": "contract_code",
            }
        ],
        "conflicts": [],
        "diagnostics": [{"code": "BUSINESS_CONTEXT.CANDIDATES_FOUND"}],
    },
}

VALID_INTERPRETATION = {
    "schema_version": "candidate_document_interpretation.v1",
    "status": "success",
    "document_type": "invoice",
    "fields": {"invoice_number": "SYN-INVOICE-001"},
    "relations": [{"relation_type": "invoice_contract", "target_candidate_id": "C-001"}],
    "evidence": [
        {
            "kind": "business_context",
            "candidate_id": "C-001",
            "field": "contract_code",
        }
    ],
    "confidence": 0.92,
    "interpreter": "codex_agent",
    "model": "hosted-agent",
    "prompt_version": "document_interpretation.v1",
    "policy_version": "document_interpretation_policy.v1",
}


def valid_exchange() -> tuple[dict[str, object], dict[str, object]]:
    request = build_agent_judgement_request(
        run_id="run_" + "a" * 32,
        request_id="agent-request:invoice-001",
        interpretation_request=VALID_INTERPRETATION_REQUEST,
    )
    response = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "request_id": request["request_id"],
        "request_hash": request["request_hash"],
        "interpreter": "codex_agent",
        "model": "hosted-agent",
        "created_at": "2026-07-26T00:00:00Z",
        "interpretation": deepcopy(VALID_INTERPRETATION),
    }
    return request, response


def test_response_binds_request_hash_run_and_strict_interpretation() -> None:
    request, payload = valid_exchange()

    response = parse_agent_judgement_response(payload, request=request)

    assert request["schema_version"] == REQUEST_SCHEMA_VERSION
    assert response["interpretation"]["schema_version"] == "candidate_document_interpretation.v1"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.pop("request_hash"),
        lambda r: r.__setitem__("run_id", "run_" + "b" * 32),
        lambda r: r["interpretation"]["relations"][0].__setitem__("target_candidate_id", "C-999"),
    ],
)
def test_response_mutation_is_rejected(mutation) -> None:
    request, response = valid_exchange()
    mutation(response)

    with pytest.raises(AgentJudgementSchemaError):
        parse_agent_judgement_response(response, request=request)


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema_version":"agent_judgement_response.v1","schema_version":"agent_judgement_response.v1"}',
        '{"schema_version":"agent_judgement_response.v1","created_at":"2026-07-26T00:00:00Z","score":NaN}',
    ],
)
def test_response_json_rejects_duplicate_keys_and_nonfinite_numbers(payload: str) -> None:
    request, _ = valid_exchange()

    with pytest.raises(AgentJudgementSchemaError):
        parse_agent_judgement_response(payload, request=request)


@pytest.mark.parametrize(
    "bad_value",
    [
        _joined_sample("C:", "\\", "private", "\\", "invoice.pdf"),
        _joined_sample("/", "private", "/", "invoice.pdf"),
        _joined_sample("Bearer", " ", "synthetic-credential"),
        _joined_sample("api_key", "=", "synthetic-credential"),
    ],
)
def test_request_rejects_physical_paths_and_credentials(bad_value: str) -> None:
    payload = deepcopy(VALID_INTERPRETATION_REQUEST)
    payload["document"]["text_segments"][0]["text"] = bad_value

    with pytest.raises(AgentJudgementSchemaError):
        build_agent_judgement_request(
            run_id="run_" + "a" * 32,
            request_id="agent-request:invoice-001",
            interpretation_request=payload,
        )


@pytest.mark.parametrize(
    "line_items",
    [
        [_sensitive_mapping("password", "synthetic-secret")],
        [{"details": _sensitive_mapping("credentials", {"value": "synthetic-secret"})}],
    ],
)
def test_request_rejects_sensitive_line_item_keys_at_any_depth(line_items: list[object]) -> None:
    payload = deepcopy(VALID_INTERPRETATION_REQUEST)
    payload["document"]["candidate_fields"]["line_items"] = line_items

    with pytest.raises(AgentJudgementSchemaError):
        build_agent_judgement_request(
            run_id="run_" + "a" * 32,
            request_id="agent-request:invoice-001",
            interpretation_request=payload,
        )


@pytest.mark.parametrize(
    "path_value",
    [
        _joined_sample("archive", "/", "private", "/", "invoice.pdf"),
        _joined_sample("ref:", "/", "private", "/", "invoice.pdf"),
        _joined_sample("\\", "\\", "server", "\\", "private", "\\", "invoice.pdf"),
        _joined_sample("file:", "//", "localhost", "/", "private", "/", "invoice.pdf"),
    ],
)
def test_request_rejects_physical_paths_in_transferable_and_nested_fields(path_value: str) -> None:
    payload = deepcopy(VALID_INTERPRETATION_REQUEST)
    payload["document"]["candidate_fields"]["contract_code"] = path_value

    with pytest.raises(AgentJudgementSchemaError):
        build_agent_judgement_request(
            run_id="run_" + "a" * 32,
            request_id="agent-request:invoice-001",
            interpretation_request=payload,
        )

    payload = deepcopy(VALID_INTERPRETATION_REQUEST)
    payload["document"]["candidate_fields"]["line_items"] = [{"description": path_value}]
    with pytest.raises(AgentJudgementSchemaError):
        build_agent_judgement_request(
            run_id="run_" + "a" * 32,
            request_id="agent-request:invoice-001",
            interpretation_request=payload,
        )


def test_request_accepts_read_only_mapping_inputs() -> None:
    request = build_agent_judgement_request(
        run_id="run_" + "a" * 32,
        request_id="agent-request:invoice-001",
        interpretation_request=MappingProxyType(deepcopy(VALID_INTERPRETATION_REQUEST)),
    )

    assert request["interpretation_request"]["schema_version"] == "document_interpretation_evidence_pack.v1"
