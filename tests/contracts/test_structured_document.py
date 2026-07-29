from __future__ import annotations

import pytest

from contracts.structured_document import (
    StructuredDocumentError,
    build_structured_document,
    validate_structured_document,
)


SOURCE_REF = {
    "binding_id": "source",
    "logical_uri": "business://source/contracts/contract.docx",
    "storage_provider": "local",
    "object_key": "contracts/contract.docx",
}


def test_contract_accepts_native_parse_result_with_bounded_text() -> None:
    payload = build_structured_document(
        source_ref=SOURCE_REF,
        content_hash="a" * 64,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        parser="native_docx",
        text="合同正文",
        pages=[],
        tables=[],
        fields={"contract_code": "CT-001"},
    )

    assert validate_structured_document(payload)["schema_version"] == "structured_document.v1"
    assert payload["text"] == "合同正文"


def test_contract_truncates_text_by_utf8_bytes_without_invalid_suffix() -> None:
    payload = build_structured_document(
        source_ref=SOURCE_REF,
        content_hash="a" * 64,
        media_type="text/plain",
        parser="native_text",
        text="中" * 2_000,
        pages=[],
        tables=[],
        fields={},
    )

    assert len(payload["text"].encode("utf-8")) <= 4_096
    assert payload["text"].encode("utf-8").decode("utf-8") == payload["text"]


def test_contract_rejects_invalid_content_hash() -> None:
    with pytest.raises(StructuredDocumentError):
        build_structured_document(
            source_ref=SOURCE_REF,
            content_hash="not-a-sha256",
            media_type="text/plain",
            parser="native_text",
            text="正文",
            pages=[],
            tables=[],
            fields={},
        )
