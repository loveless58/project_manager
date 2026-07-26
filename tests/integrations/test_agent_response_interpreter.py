from __future__ import annotations

import json

import pytest

from contracts.agent_judgement import (
    RESPONSE_SCHEMA_VERSION,
    build_agent_judgement_request,
)


def valid_exchange() -> tuple[dict[str, object], dict[str, object]]:
    interpretation_request = {
        "schema_version": "document_interpretation_evidence_pack.v1",
        "parse_artifact_ref": "artifact:parsed:invoice-001",
        "document": {
            "document_type_hint": "invoice",
            "candidate_fields": {"contract_code": "CT-001"},
            "text_segments": [{"id": "page-1", "text": "Invoice INV-001"}],
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
    request = build_agent_judgement_request(
        run_id="run_" + "a" * 32,
        request_id="agent-request:invoice-001",
        interpretation_request=interpretation_request,
    )
    response = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "request_id": request["request_id"],
        "request_hash": request["request_hash"],
        "interpreter": "codex_agent",
        "model": "hosted-agent",
        "created_at": "2026-07-26T00:00:00Z",
        "interpretation": {
            "schema_version": "candidate_document_interpretation.v1",
            "status": "success",
            "document_type": "invoice",
            "fields": {"invoice_number": "INV-001"},
            "relations": [
                {
                    "relation_type": "invoice_contract",
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
            "confidence": 0.92,
            "interpreter": "codex_agent",
            "model": "hosted-agent",
            "prompt_version": "document_interpretation.v1",
            "policy_version": "document_interpretation_policy.v1",
        },
    }
    return request, response


def test_agent_response_interpreter_returns_only_the_bound_interpretation() -> None:
    from integrations.llm.agent_response_interpreter import AgentResponseInterpreter

    request, response = valid_exchange()
    interpreter = AgentResponseInterpreter(response, request=request)

    result = json.loads(interpreter.complete_json(request["interpretation_request"]))

    assert result == response["interpretation"]
    assert interpreter.name == "codex_agent"
    assert interpreter.model == "hosted-agent"
    assert interpreter.schema_version == "candidate_document_interpretation.v1"
    assert interpreter.prompt_version == "document_interpretation.v1"
    assert interpreter.policy_version == "document_interpretation_policy.v1"


def test_agent_response_interpreter_rejects_another_request() -> None:
    from integrations.llm.agent_response_interpreter import (
        AgentResponseInterpreter,
        DocumentInterpreterRequestError,
    )

    request, response = valid_exchange()
    interpreter = AgentResponseInterpreter(response, request=request)

    with pytest.raises(DocumentInterpreterRequestError) as error:
        interpreter.complete_json(
            {**request["interpretation_request"], "business_context": {}}
        )

    assert error.value.code == "DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda response: response.__setitem__("request_hash", "b" * 64),
        lambda response: response["interpretation"]["relations"][0].__setitem__(
            "target_candidate_id", "C-999"
        ),
        lambda response: response.__setitem__("interpreter", ""),
    ],
    ids=["mismatched request hash", "candidate outside context", "invalid agent identity"],
)
def test_agent_response_interpreter_rejects_invalid_agent_response(mutate) -> None:
    from integrations.llm.agent_response_interpreter import (
        AgentResponseInterpreter,
        DocumentInterpreterRequestError,
    )

    request, response = valid_exchange()
    mutate(response)
    interpreter = AgentResponseInterpreter(response, request=request)

    with pytest.raises(DocumentInterpreterRequestError) as error:
        interpreter.complete_json(request["interpretation_request"])

    assert error.value.code == "DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID"


def test_agent_response_interpreter_rejects_missing_agent_response() -> None:
    from integrations.llm.agent_response_interpreter import (
        AgentResponseInterpreter,
        DocumentInterpreterRequestError,
    )

    request, _ = valid_exchange()
    interpreter = AgentResponseInterpreter(None, request=request)

    with pytest.raises(DocumentInterpreterRequestError) as error:
        interpreter.complete_json(request["interpretation_request"])

    assert error.value.code == "DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID"
