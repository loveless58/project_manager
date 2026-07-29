from __future__ import annotations

from skills.file_organizer.document_parse import DocumentParseSkill


def test_native_parse_records_the_actual_successful_parser_attempt(tmp_path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("正文", encoding="utf-8")

    result = DocumentParseSkill().parse(str(source))

    assert result["attempts"] == [
        {"stage": "native", "provider": "native_txt", "status": "success"}
    ]


def test_parse_failure_preserves_native_and_ocr_attempts(tmp_path) -> None:
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"legacy")

    result = DocumentParseSkill().parse(str(source))

    assert result["status"] == "needs_review"
    assert result["attempts"][0] == {
        "stage": "native", "provider": "native", "status": "blocked"
    }
    assert result["attempts"][-1]["stage"] == "ocr"
