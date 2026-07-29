from __future__ import annotations

from contracts.agent_run import build_agent_run
from contracts.document_result import build_document_result


def test_run_and_document_result_expose_safe_audit_metadata() -> None:
    run = build_agent_run(
        run_id="run-1", goal="解析，不归档", input_refs=[], status="prepared", artifacts={}
    )
    document = {
        "schema_version": "structured_document.v1", "status": "success",
        "source_ref": {"binding_id": "source", "logical_uri": "business://source/invoices/a.pdf", "storage_provider": "local", "object_key": "invoices/a.pdf"},
        "content_hash": "a" * 64, "media_type": "application/pdf", "parser": "native_pdf",
        "text": "正文", "pages": [], "tables": [], "fields": {}, "attempts": [{"stage": "native", "provider": "native_pdf", "status": "success"}],
    }

    result = build_document_result(
        run=run, document_id="doc-1", structured_document=document, facts={},
        decision={"status": "needs_review", "suggested_action": "no_archive", "reasons": []}, artifacts=[],
    )

    assert run["agent"] == {"name": "DocumentManagementAgent", "version": "1.0"}
    assert run["created_at_utc"].endswith("Z")
    assert run["safety"] == {"source_files": "read_only", "archive": "not_requested"}
    assert result["document"]["original_filename"] == "a.pdf"
    assert result["run"]["agent"] == run["agent"]
    assert result["parse"]["attempts"][0]["provider"] == "native_pdf"
