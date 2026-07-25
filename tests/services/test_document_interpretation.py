from __future__ import annotations

import json

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

    def complete_json(self, request):
        return {"unexpected": "shape"}


class CapturingInterpreter:
    name = "fake"
    model = "fake-model"

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
                "documents": [{"path": "/private/contract.pdf"}],
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
        "fields": {"invoice_number": "INV-001"},
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
        "untrusted_extra": "must never reach the LLM",
    }


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
