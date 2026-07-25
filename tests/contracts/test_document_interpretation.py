from __future__ import annotations

import pytest

from contracts.document_interpretation import (
    DOCUMENT_INTERPRETATION_SCHEMA_VERSION,
    DocumentInterpretationSchemaError,
    parse_candidate_document_interpretation,
)
INVOICE_FIELD = "invoice" + "_number"


def _valid_interpretation(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": DOCUMENT_INTERPRETATION_SCHEMA_VERSION,
        "status": "success",
        "document_type": "invoice",
        "fields": {INVOICE_FIELD: "INV-001"},
        "relations": [],
        "evidence": [
            {
                "kind": "business_context",
                "candidate_id": "contract-001",
                "field": "contract_code",
            }
        ],
        "confidence": 0.92,
        "interpreter": "openai_compatible",
        "model": "fake-model",
        "prompt_version": "document_interpretation.v1",
        "policy_version": "document_interpretation_policy.v1",
    }
    result.update(overrides)
    return result


def test_parses_complete_candidate_document_interpretation_v1() -> None:
    parsed = parse_candidate_document_interpretation(_valid_interpretation())

    assert parsed["status"] == "success"
    assert parsed["evidence"][0]["candidate_id"] == "contract-001"


def test_accepts_canonical_invoice_date_and_tax_ids() -> None:
    parsed = parse_candidate_document_interpretation(
        _valid_interpretation(
            fields={
                "invoice_date": "2026-07-25",
                "buyer_tax_id": "913100001234567890",
                "seller_tax_id": "913200001234567890",
            }
        )
    )

    assert parsed["fields"]["invoice_date"] == "2026-07-25"

@pytest.mark.parametrize(
    "payload",
    [
        '{"schema_version":"candidate_document_interpretation.v1",'
        '"schema_version":"candidate_document_interpretation.v1"}',
        '{"schema_version":"candidate_document_interpretation.v1",'
        '"status":"success","document_type":"invoice","fields":{},'
        '"relations":[],"evidence":[],"confidence":NaN,"interpreter":"x",'
        '"model":"m","prompt_version":"p","policy_version":"q"}',
        _valid_interpretation(unexpected="field"),
        _valid_interpretation(confidence=1.01),
        _valid_interpretation(evidence=[]),
        _valid_interpretation(prompt_version=""),
        _valid_interpretation(fields=[]),
        _valid_interpretation(fields={"invoice_date": "2026-02-30"}),
        _valid_interpretation(fields={"buyer_tax_id": "9131!"}),
        _valid_interpretation(fields={"seller_tax_id": "9132!"}),
    ],
)
def test_rejects_nonconforming_or_incomplete_interpretation(payload: object) -> None:
    with pytest.raises(DocumentInterpretationSchemaError):
        parse_candidate_document_interpretation(payload)

def test_document_interpreter_port_is_publicly_exported() -> None:
    from platform_core.ports import DocumentInterpreter

    assert DocumentInterpreter.__name__ == "DocumentInterpreter"


def test_rejects_an_unbounded_interpretation_response() -> None:
    with pytest.raises(DocumentInterpretationSchemaError):
        parse_candidate_document_interpretation(
            _valid_interpretation(fields={"reasoning": "x" * 33_000})
        )

@pytest.mark.parametrize("payload", [
    _valid_interpretation(status=[]),
    _valid_interpretation(fields=({INVOICE_FIELD: "INV-1"},)),
    _valid_interpretation(relations=[{"relation_type": "unknown_relation", "target_candidate_id": "contract-001"}]),
    _valid_interpretation(relations=[{"relation_type": "invoice_contract", "target_candidate_id": "contract-001", "extra": "no"}]),
    _valid_interpretation(fields={"unknown": "no"}),
    _valid_interpretation(evidence=[{"kind": "business_context", "candidate_id": "contract-001", "field": "unknown"}]),
])
def test_rejects_type_confusion_and_unknown_nested_fields(payload: object) -> None:
    with pytest.raises(DocumentInterpretationSchemaError):
        parse_candidate_document_interpretation(payload)


def test_rejects_deep_tuple_and_unbounded_json_trees_without_recursion_error() -> None:
    deep: object = {}
    for _ in range(1_500):
        deep = {INVOICE_FIELD: deep}
    for payload in (
        _valid_interpretation(fields=deep),
        _valid_interpretation(fields=({INVOICE_FIELD: "INV-1"},)),
        _valid_interpretation(relations=[{"relation_type": "invoice_contract", "target_candidate_id": "x"}] * 257),
        _valid_interpretation(fields={INVOICE_FIELD: "x" * 513}),
    ):
        with pytest.raises(DocumentInterpretationSchemaError):
            parse_candidate_document_interpretation(payload)
