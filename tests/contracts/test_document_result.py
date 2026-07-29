from __future__ import annotations

import pytest

from contracts.document_result import build_document_result


def success_document(*, text: str) -> dict[str, object]:
    return {
        "schema_version": "structured_document.v1",
        "status": "success",
        "source_ref": {
            "binding_id": "source",
            "logical_uri": "business://source/invoice.pdf",
            "storage_provider": "local",
            "object_key": "invoice.pdf",
        },
        "content_hash": "a" * 64,
        "media_type": "application/pdf",
        "parser": "native_pdf",
        "text": text,
        "pages": [{"page": 1, "text": text, "confidence": None}],
        "tables": [],
        "fields": {},
    }


def test_document_result_preserves_parse_text_and_non_archive_decision() -> None:
    result = build_document_result(
        run={"run_id": "run-1", "goal": "解析", "status": "prepared"},
        document_id="doc-1",
        structured_document=success_document(text="正文"),
        facts={"document_type": {"value": "invoice", "confidence": 0.99, "evidence": []}},
        decision={"status": "needs_review", "suggested_action": "no_archive", "reasons": []},
        artifacts=[],
    )

    assert result["schema_version"] == "document_result.v1"
    assert result["document"]["document_id"] == "doc-1"
    assert result["parse"]["text"] == "正文"
    assert result["decision"]["suggested_action"] == "no_archive"


def test_document_result_rejects_unknown_decision_action() -> None:
    with pytest.raises(ValueError, match="suggested action"):
        build_document_result(
            run={"run_id": "run-1", "goal": "解析", "status": "prepared"},
            document_id="doc-1",
            structured_document=success_document(text="正文"),
            facts={},
            decision={"status": "needs_review", "suggested_action": "archive", "reasons": []},
            artifacts=[],
        )
