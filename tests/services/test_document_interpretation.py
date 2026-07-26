from __future__ import annotations

from copy import deepcopy
import json

import pytest

INVOICE_FIELD = "invoice" + "_number"

from platform_core.models import BusinessContextEvidence


class StaticRetrieval:
    def __init__(self, evidence: BusinessContextEvidence | None = None) -> None:
        self.evidence = evidence or BusinessContextEvidence(
            "needs_review", (), (), (), ({"code": "BUSINESS_CONTEXT.NO_CANDIDATES"},)
        )
        self.queries = []

    def find_business_candidates(self, query):
        self.queries.append(query)
        return self.evidence


class MalformedInterpreter:
    name = "malformed"
    model = "synthetic-model"
    schema_version = "candidate_document_interpretation.v1"
    prompt_version = "document_interpretation.v1"
    policy_version = "document_interpretation_policy.v1"

    def complete_json(self, request):
        return {"unexpected": "shape"}


class CapturingInterpreter:
    name = "fake"
    model = "fake-model"
    schema_version = "candidate_document_interpretation.v1"
    prompt_version = "document_interpretation.v1"
    policy_version = "document_interpretation_policy.v1"

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        return self.response


def _evidence() -> BusinessContextEvidence:
    return BusinessContextEvidence(
        "matched",
        (
            {
                "id": "contract-001",
                "document_type": "contract",
                "parties": {"buyer": {"name": "Buyer"}, "seller": {"name": "Seller"}},
                "facts": {"contract_code": "CT-001"},
            },
        ),
        (
            {
                "kind": "business_context",
                "candidate_id": "contract-001",
                "field": "contract_code",
            },
        ),
        (),
        ({"code": "BUSINESS_CONTEXT.CANDIDATES_FOUND"},),
    )


def _valid_response(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": "candidate_document_interpretation.v1",
        "status": "success",
        "document_type": "invoice",
        "fields": {INVOICE_FIELD: "INV-001"},
        "relations": [],
        "evidence": [
            {"kind": "business_context", "candidate_id": "contract-001", "field": "contract_code"}
        ],
        "confidence": 0.92,
        "interpreter": "fake",
        "model": "fake-model",
        "prompt_version": "document_interpretation.v1",
        "policy_version": "document_interpretation_policy.v1",
    }
    result.update(overrides)
    return result


def _input() -> dict[str, object]:
    return {
        "parse_artifact_ref": "artifact:parsed:1",
        "document_type_hint": "invoice",
        "candidate_fields": {"contract_code": "CT-001"},
        "text_segments": [{"id": "page-1", "text": "Invoice INV-001"}],
    }


def test_prepare_retrieves_once_and_completion_uses_same_request() -> None:
    from services.document_interpretation import DocumentInterpretationService

    retrieval = StaticRetrieval(_evidence())
    interpreter = CapturingInterpreter(
        _valid_response(
            relations=[
                {
                    "relation_type": "invoice_contract",
                    "target_candidate_id": "contract-001",
                }
            ]
        )
    )
    service = DocumentInterpretationService(retrieval, interpreter)

    prepared = service.prepare(_input())
    result = service.complete_prepared(prepared, interpreter)

    assert len(retrieval.queries) == 1
    assert interpreter.requests == [prepared.request]
    assert result["status"] == "needs_review"
    assert result["confirmed"] is False


def test_selected_agent_unavailability_never_falls_back_to_configured_interpreter() -> None:
    from contracts.agent_judgement import build_agent_judgement_request
    from integrations.llm.agent_response_interpreter import AgentResponseInterpreter
    from services.document_interpretation import DocumentInterpretationService

    configured = CapturingInterpreter(_valid_response())
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), configured)
    prepared = service.prepare(_input())
    request = build_agent_judgement_request(
        run_id="run_" + "a" * 32,
        request_id="agent-request:prepared-001",
        interpretation_request=prepared.request,
    )

    result = service.complete_prepared(
        prepared, AgentResponseInterpreter(None, request=request)
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID"
    assert configured.requests == []


def test_complete_prepared_rejects_nested_request_mutation() -> None:
    from services.document_interpretation import DocumentInterpretationService

    interpreter = CapturingInterpreter(_valid_response())
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), interpreter)
    prepared = service.prepare(_input())

    prepared.request["document"]["candidate_fields"]["contract_code"] = "CT-999"
    result = service.complete_prepared(prepared, interpreter)

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "DOCUMENT_INTERPRETATION.SCHEMA_INVALID"
    assert interpreter.requests == []


@pytest.mark.parametrize(
    "field, invalid_identity",
    [
        ("interpreter", "contains space"),
        ("model", "x" * 129),
        ("interpreter", "invalid!character"),
    ],
)
def test_invalid_agent_identity_is_response_invalid_not_adapter_invalid(
    field: str, invalid_identity: str
) -> None:
    from contracts.agent_judgement import RESPONSE_SCHEMA_VERSION, build_agent_judgement_request
    from integrations.llm.agent_response_interpreter import AgentResponseInterpreter
    from services.document_interpretation import DocumentInterpretationService

    configured = CapturingInterpreter(_valid_response())
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), configured)
    prepared = service.prepare(_input())
    request = build_agent_judgement_request(
        run_id="run_" + "a" * 32,
        request_id="agent-request:invalid-identity",
        interpretation_request=prepared.request,
    )
    interpretation = _valid_response(
        interpreter=invalid_identity if field == "interpreter" else "codex_agent",
        model=invalid_identity if field == "model" else "hosted-agent",
    )
    response = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "run_id": request["run_id"],
        "request_id": request["request_id"],
        "request_hash": request["request_hash"],
        "interpreter": interpretation["interpreter"],
        "model": interpretation["model"],
        "created_at": "2026-07-26T00:00:00Z",
        "interpretation": interpretation,
    }

    result = service.complete_prepared(
        prepared, AgentResponseInterpreter(response, request=request)
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID"
    assert configured.requests == []


def test_malformed_response_blocks_and_keeps_parse_ref() -> None:
    from services.document_interpretation import DocumentInterpretationService

    service = DocumentInterpretationService(StaticRetrieval(), MalformedInterpreter())

    result = service.interpret(_input())

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "DOCUMENT_INTERPRETATION.SCHEMA_INVALID"
    assert result["parse_artifact_ref"] == "artifact:parsed:1"


def test_valid_interpretation_has_controlled_evidence_and_versions() -> None:
    from services.document_interpretation import DocumentInterpretationService

    interpreter = CapturingInterpreter(json.dumps(_valid_response()))
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), interpreter)

    result = service.interpret(_input())

    assert result["status"] == "success"
    assert result["parse_artifact_ref"] == "artifact:parsed:1"
    assert result["evidence"] == _valid_response()["evidence"]
    assert result["confidence"] == 0.92
    assert result["interpreter"] == "fake"
    assert result["model"] == "fake-model"
    assert result["prompt_version"] == "document_interpretation.v1"
    assert result["policy_version"] == "document_interpretation_policy.v1"
    request = interpreter.requests[0]
    assert set(request) == {"schema_version", "parse_artifact_ref", "document", "business_context"}
    assert request["document"] == {
        "document_type_hint": "invoice",
        "candidate_fields": {"contract_code": "CT-001"},
        "text_segments": [{"id": "page-1", "text": "Invoice INV-001"}],
    }
    assert request["business_context"]["candidates"] == [
        {"id": "contract-001", "document_type": "contract", "parties": {"buyer": {"name": "Buyer"}, "seller": {"name": "Seller"}}, "facts": {"contract_code": "CT-001"}}
    ]
    assert "/private/contract.pdf" not in json.dumps(request)
    assert "untrusted_extra" not in json.dumps(request)


def test_nonempty_relations_are_forced_to_needs_review_without_sql_authority() -> None:
    from services.document_interpretation import DocumentInterpretationService

    response = _valid_response(
        relations=[{"relation_type": "invoice_contract", "target_candidate_id": "contract-001"}]
    )
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), CapturingInterpreter(response))

    result = service.interpret(_input())

    assert result["status"] == "needs_review"
    assert result["relations"] == response["relations"]
    assert result["confirmed"] is False

def test_blocked_relation_response_never_downgrades_to_needs_review() -> None:
    from services.document_interpretation import DocumentInterpretationService

    response = _valid_response(
        status="blocked",
        blocked_reason="DOCUMENT_INTERPRETATION.INSUFFICIENT_EVIDENCE",
        relations=[{"relation_type": "invoice_contract", "target_candidate_id": "contract-001"}],
    )
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), CapturingInterpreter(response))

    result = service.interpret(_input())

    assert result["status"] == "blocked"
    assert result["confirmed"] is False


def test_boundary_rejects_sensitive_or_invalid_before_interpreter() -> None:
    from services.document_interpretation import DocumentInterpretationService

    interpreter = CapturingInterpreter(_valid_response())
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), interpreter)
    for value in (
        "C:" + chr(92) + "Users" + chr(92) + "x",
        chr(92) * 2 + "server" + chr(92) + "share",
        "/private/file",
        "/" * 2 + "server/share",
        "/" * 2 + "etc/passwd",
        "file:" + "/" * 3 + "tmp/secret",
        "Bearer abc",
        "Authorization: Bearer abc",
        "token=abc",
        "api_key=abc",
        "api_token=abc",
        "password=abc",
        "Ｔｏｋｅｎ=abc",
    ):
        result = service.interpret(
            {
                "parse_artifact_ref": "artifact:parsed:1",
                "document_type_hint": "invoice",
                "candidate_fields": {},
                "text_segments": [{"id": "p1", "text": value}],
            }
        )
        assert result["status"] == "blocked"
    assert interpreter.requests == []


def test_retrieval_service_evidence_maps_to_canonical_interpretation_relation() -> None:
    from services.document_interpretation import DocumentInterpretationService
    from services.retrieval_service import RetrievalService
    class Provider:
        def search(self, query):
            return ({"id":"contract-001","document_type":"contract","parties":{"buyer":{"tax_id":"913100001234567890","name":"Buyer"}},"facts":{"contract_code":"HT-2026-001"}},)
    response = _valid_response(evidence=[{"kind":"business_context","candidate_id":"contract-001","field":"buyer.tax_id"}], relations=[{"relation_type":"invoice_contract","target_candidate_id":"contract-001"}])
    service = DocumentInterpretationService(RetrievalService(Provider(), None), CapturingInterpreter(response))
    result = service.interpret({"parse_artifact_ref":"artifact:parsed:1","document_type_hint":"invoice","candidate_fields":{"buyer":{"tax_id":"913100001234567890","name":"Buyer"},"contract_code":"HT-2026-001"},"text_segments":[]})
    assert result["status"] == "needs_review"
    assert result["confirmed"] is False


def _context(**overrides: object) -> BusinessContextEvidence:
    original = _evidence()
    values = {
        "status": original.status,
        "candidates": original.candidates,
        "evidence_refs": original.evidence_refs,
        "conflicts": original.conflicts,
        "diagnostics": original.diagnostics,
    }
    values.update(overrides)
    return BusinessContextEvidence(**values)


def _candidate(**overrides: object) -> dict[str, object]:
    value = deepcopy(_evidence().candidates[0])
    value.update(overrides)
    return value


def _set_candidate_field(payload: dict[str, object], key: str, value: object) -> None:
    payload["candidate_fields"][key] = value


def _set_buyer(payload: dict[str, object], value: object) -> None:
    payload["candidate_fields"]["buyer"] = value


def _set_text(payload: dict[str, object], value: object) -> None:
    payload["text_segments"] = value


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("unknown root field", lambda value: value.update({"unexpected": "drop-me"})),
        ("invalid artifact", lambda value: value.update({"parse_artifact_ref": "/tmp/a"})),
        ("document type type confusion", lambda value: value.update({"document_type_hint": []})),
        ("unknown document type", lambda value: value.update({"document_type_hint": "memo"})),
        ("candidate fields type confusion", lambda value: value.update({"candidate_fields": []})),
        ("text segments type confusion", lambda value: _set_text(value, {})),
        ("boolean amount", lambda value: _set_candidate_field(value, "amount", True)),
        ("infinite amount", lambda value: _set_candidate_field(value, "amount", float("inf"))),
        ("nan amount", lambda value: _set_candidate_field(value, "amount", float("nan"))),
        ("invalid invoice date", lambda value: _set_candidate_field(value, "invoice_date", "2026-02-30")),
        ("invalid tax id", lambda value: _set_buyer(value, {"tax_id": "9131!", "name": "Buyer"})),
        ("unknown candidate field", lambda value: _set_candidate_field(value, "unknown", "drop-me")),
        ("oversized nested array", lambda value: _set_candidate_field(value, "line_items", [{}] * 300)),
        (
            "oversized text array",
            lambda value: _set_text(value, [{"id": f"p-{index}", "text": "x"} for index in range(300)]),
        ),
        ("oversized single field", lambda value: _set_buyer(value, {"name": "x" * 65_537, "tax_id": "913100001234567890"})),
        (
            "oversized total json",
            lambda value: _set_candidate_field(
                value,
                "line_items",
                [{"item_name": "x" * 3_900} for _ in range(200)],
            ),
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_invalid_document_input_blocks_before_interpreter(case, mutate) -> None:
    from services.document_interpretation import DocumentInterpretationService

    payload = _input()
    mutate(payload)
    interpreter = CapturingInterpreter(_valid_response())
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), interpreter)

    result = service.interpret(payload)

    assert result["status"] == "blocked", case
    assert result["blocked_reason"] == "DOCUMENT_INTERPRETATION.SCHEMA_INVALID"
    assert interpreter.requests == []


def test_safe_business_identifiers_are_not_mistaken_for_credentials() -> None:
    from services.document_interpretation import DocumentInterpretationService

    interpreter = CapturingInterpreter(_valid_response())
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), interpreter)
    company_name = "上海Token科技有限" + "公司"
    payload = _input()
    payload["candidate_fields"] = {
        "buyer": {
            "name": company_name,
            "tax_id": "913100001234567890",
        },
        "contract_code": "HT-2026-001",
        "invoice_date": "2026-07-25",
        "amount": 12.5,
    }
    payload["text_segments"] = [{"id": "page-1", "text": "AUTHORIZATION-2026"}]

    result = service.interpret(payload)

    assert result["status"] == "success"
    assert interpreter.requests[0]["document"]["candidate_fields"] == {
        "amount": 12.5,
        "buyer_name": company_name,
        "buyer_tax_id": "913100001234567890",
        "contract_code": "HT-2026-001",
        "invoice_date": "2026-07-25",
    }


def _candidate_with_fact(key: str, value: object) -> dict[str, object]:
    candidate = _candidate()
    candidate["facts"] = {"contract_code": "CT-001", key: value}
    return candidate


def _valid_document(**overrides: object) -> dict[str, object]:
    document = {
        "path": "/synthetic/contract.pdf",
        "document_version_id": "contract-v1",
        "content_hash": "a" * 64,
        "media_type": "application/pdf",
        "page_count": 1,
        "requires_structure_index": False,
    }
    document.update(overrides)
    return document


@pytest.mark.parametrize(
    ("case", "context"),
    [
        ("unknown status", _context(status="unknown")),
        ("candidate count", _context(candidates=tuple(_candidate() for _ in range(300)))),
        ("evidence count", _context(evidence_refs=_evidence().evidence_refs * 300)),
        ("diagnostic count", _context(diagnostics=_evidence().diagnostics * 300)),
        ("invalid candidate id", _context(candidates=(_candidate(id=True),))),
        ("unknown candidate document type", _context(candidates=(_candidate(document_type="memo"),))),
        (
            "invalid candidate tax id",
            _context(candidates=(_candidate(parties={"buyer": {"name": "Buyer", "tax_id": "9131!"}}),)),
        ),
        ("invalid fact date", _context(candidates=(_candidate_with_fact("date", "2026-02-30"),))),
        ("boolean fact amount", _context(candidates=(_candidate_with_fact("amount", True),))),
        ("infinite fact amount", _context(candidates=(_candidate_with_fact("amount", float("inf")),))),
        (
            "unknown evidence field",
            _context(evidence_refs=({"kind": "business_context", "candidate_id": "contract-001", "field": "unknown"},)),
        ),
        ("unknown diagnostic", _context(diagnostics=({"code": "BUSINESS_CONTEXT.UNKNOWN"},))),
        (
            "credential in candidate document",
            _context(candidates=(_candidate(documents=[_valid_document(path="api_token=abc")]),)),
        ),
        (
            "oversized document metadata",
            _context(candidates=(_candidate(documents=[_valid_document(metadata="x" * 70_000)]),)),
        ),
        (
            "credential in conflict",
            _context(conflicts=({"code": "BUSINESS_CONTEXT.CONFLICT", "candidate_id": "contract-001", "field": "buyer.name", "query_value": "Authorization: Bearer abc", "candidate_value": "***uyer"},)),
        ),
        (
            "mixed valid and sensitive unknown evidence",
            _context(evidence_refs=_evidence().evidence_refs + ({"kind": "business_context", "candidate_id": "contract-001", "field": "unknown", "metadata": "api_token=abc"},)),
        ),
        (
            "unknown evidence property",
            _context(evidence_refs=({"kind": "business_context", "candidate_id": "contract-001", "field": "contract_code", "extra": "drop-me"},)),
        ),
        (
            "oversized path hints",
            _context(candidates=(_candidate(path_hints=["contracts/synthetic"] * 300),)),
        ),
        (
            "oversized document collection",
            _context(candidates=(_candidate(documents=[_valid_document()] * 300),)),
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_invalid_business_context_blocks_before_interpreter(case, context) -> None:
    from services.document_interpretation import DocumentInterpretationService

    interpreter = CapturingInterpreter(_valid_response())
    service = DocumentInterpretationService(StaticRetrieval(context), interpreter)

    result = service.interpret(_input())

    assert result["status"] == "blocked", case
    assert result["blocked_reason"] == "DOCUMENT_INTERPRETATION.SCHEMA_INVALID"
    assert interpreter.requests == []


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("name", None),
        ("name", ""),
        ("model", None),
        ("model", ""),
        ("schema_version", None),
        ("schema_version", []),
        ("prompt_version", ""),
        ("policy_version", "document_interpretation_policy.v2"),
    ],
)
def test_invalid_adapter_identity_blocks_before_interpreter(attribute, value) -> None:
    from services.document_interpretation import DocumentInterpretationService

    interpreter = CapturingInterpreter(_valid_response())
    setattr(interpreter, attribute, value)
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), interpreter)

    result = service.interpret(_input())

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "DOCUMENT_INTERPRETATION.SCHEMA_INVALID"
    assert interpreter.requests == []


def test_response_version_mismatch_is_blocked() -> None:
    from services.document_interpretation import DocumentInterpretationService

    interpreter = CapturingInterpreter(_valid_response(prompt_version="document_interpretation.v2"))
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), interpreter)

    result = service.interpret(_input())

    assert result["status"] == "blocked"
    assert interpreter.requests != []


def test_real_adapter_with_matching_versions_is_accepted_by_service() -> None:
    from integrations.llm.openai_compatible_interpreter import OpenAICompatibleInterpreter
    from services.document_interpretation import DocumentInterpretationService

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {"message": {"content": json.dumps(_valid_response(interpreter="openai_compatible"))}}
                ]
            }

    interpreter = OpenAICompatibleInterpreter(
        base_url="https://llm.example.invalid/v1",
        api_key="fake-key-for-test",
        model="fake-model",
        transport=lambda **kwargs: Response(),
    )
    service = DocumentInterpretationService(StaticRetrieval(_evidence()), interpreter)

    result = service.interpret(_input())

    assert result["status"] == "success"
    assert result["schema_version"] == interpreter.schema_version
    assert result["prompt_version"] == interpreter.prompt_version
    assert result["policy_version"] == interpreter.policy_version


def test_valid_source_only_candidate_metadata_is_checked_then_omitted() -> None:
    from services.document_interpretation import DocumentInterpretationService

    candidate = _candidate(
        documents=[_valid_document()],
        path_hints=["contracts/synthetic"],
    )
    context = _context(candidates=(candidate,))
    interpreter = CapturingInterpreter(_valid_response())
    service = DocumentInterpretationService(StaticRetrieval(context), interpreter)

    result = service.interpret(_input())

    assert result["status"] == "success"
    projected = interpreter.requests[0]["business_context"]["candidates"][0]
    assert projected == {
        "id": "contract-001",
        "document_type": "contract",
        "parties": {"buyer": {"name": "Buyer"}, "seller": {"name": "Seller"}},
        "facts": {"contract_code": "CT-001"},
    }


def test_task3_nested_fields_are_preserved_for_retrieval_and_allowlisted_for_llm() -> None:
    from services.document_interpretation import DocumentInterpretationService

    retrieval = StaticRetrieval(_evidence())
    interpreter = CapturingInterpreter(_valid_response())
    service = DocumentInterpretationService(retrieval, interpreter)
    fields = {
        "buyer": {"name": "Buyer", "tax_id": "913100001234567890"},
        "contract_code": "HT-2026-001",
    }
    payload = _input()
    payload["candidate_fields"] = fields

    result = service.interpret(payload)

    assert result["status"] == "success"
    assert retrieval.queries[0].candidate_fields == fields
    assert interpreter.requests[0]["document"]["candidate_fields"] == {
        "buyer_name": "Buyer",
        "buyer_tax_id": "913100001234567890",
        "contract_code": "HT-2026-001",
    }
