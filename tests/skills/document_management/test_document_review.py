from __future__ import annotations


RUN = {"run_id": "run-1", "goal": "解析，不归档", "status": "prepared"}


def unavailable_document(reason: str) -> dict[str, object]:
    return {
        "schema_version": "structured_document.v1",
        "status": "needs_review",
        "reason": reason,
        "source_ref": {
            "binding_id": "source",
            "logical_uri": "business://source/legacy.doc",
            "storage_provider": "local",
            "object_key": "legacy.doc",
        },
        "content_hash": "a" * 64,
        "media_type": "application/msword",
        "parser": "unavailable",
        "text": "",
        "pages": [],
        "tables": [],
        "fields": {},
    }


def test_review_skill_keeps_parse_failure_reviewable_without_archive_target() -> None:
    from skills.document_management.document_review.skill import DocumentReviewSkill

    result = DocumentReviewSkill().build(
        RUN, "doc-1", unavailable_document("NATIVE.UNSUPPORTED_MEDIA"), {}
    )

    assert result["parse"]["status"] == "needs_review"
    assert result["decision"] == {
        "status": "needs_review",
        "suggested_action": "no_archive",
        "reasons": ["NATIVE.UNSUPPORTED_MEDIA"],
    }


def test_parse_workflow_skill_delegates_to_existing_document_parse_skill(tmp_path) -> None:
    from skills.document_management.document_parse.skill import DocumentParseWorkflowSkill

    source = tmp_path / "source.txt"
    source.write_text("正文", encoding="utf-8")
    parsed = DocumentParseWorkflowSkill().parse(
        str(source),
        source_ref={
            "binding_id": "source",
            "logical_uri": "business://source/source.txt",
            "storage_provider": "local",
            "object_key": "source.txt",
        },
    )

    assert parsed["status"] == "success"
    assert parsed["text"] == "正文"
