"""Regression tests for honest, unified document_parse finalization."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from docx import Document

from skills.document_parse.parse import parse
from skills.document_parse.validator import validate


PARSE_INTENTS = (
    "structured_business_fields",
    "raw_content",
    "metadata_only",
)


def _assert_honestly_finalized(result: dict[str, object]) -> None:
    is_valid, errors = validate(result)
    assert result["validation"]["schema_valid"] is is_valid
    assert result["validation"]["required_fields_present"] is True
    assert result["validation"]["missing_fields"] == []
    assert is_valid, errors


@pytest.mark.parametrize("parse_intent", PARSE_INTENTS)
def test_route_failure_preserves_intent_and_uses_formal_finalization(
    parse_intent: str,
) -> None:
    result = parse("unsupported.synthetic", parse_intent=parse_intent)

    assert result["status"] == "blocked"
    assert result["reason"] == "parse_error"
    assert result["parse_intent"] == parse_intent
    _assert_honestly_finalized(result)


@pytest.mark.parametrize("parse_intent", PARSE_INTENTS)
def test_executor_blocked_preserves_intent_and_uses_formal_finalization(
    parse_intent: str,
) -> None:
    result = parse("https://example.invalid/document", parse_intent=parse_intent)

    assert result["status"] == "blocked"
    assert result["reason"] == "executor_not_implemented"
    assert result["parse_intent"] == parse_intent
    _assert_honestly_finalized(result)


@pytest.mark.parametrize("parse_intent", PARSE_INTENTS)
def test_missing_source_preserves_intent_and_reports_source_missing(
    tmp_path: Path,
    parse_intent: str,
) -> None:
    result = parse(str(tmp_path / "missing.docx"), parse_intent=parse_intent)

    assert result["status"] == "blocked"
    assert result["reason"] == "source_missing"
    assert result["parse_intent"] == parse_intent
    _assert_honestly_finalized(result)


def test_empty_metadata_only_source_is_honestly_schema_valid() -> None:
    result = parse("", parse_intent="metadata_only")

    assert result["status"] == "blocked"
    assert result["reason"] == "source_missing"
    assert result["source_path"] == ""
    assert result["parse_intent"] == "metadata_only"
    _assert_honestly_finalized(result)


def test_explicit_executor_failure_keeps_failed_status_and_is_finalized(
    monkeypatch,
) -> None:
    parse_module = importlib.import_module("skills.document_parse.parse")

    class FailedExecutor:
        name = "docx"

        @staticmethod
        def extract(source: str) -> dict[str, object]:
            return {
                "status": "failed",
                "raw_data": {},
                "error": "Synthetic executor failure.",
                "implementation_status": "implemented",
            }

    monkeypatch.setattr(parse_module, "route", lambda source: (FailedExecutor(), ".docx"))

    result = parse_module.parse("synthetic.docx", parse_intent="raw_content")

    assert result["status"] == "failed"
    assert result["reason"] == "parse_error"
    assert result["parse_intent"] == "raw_content"
    _assert_honestly_finalized(result)


@pytest.mark.parametrize(
    ("llm_confidence", "expected_status"),
    [("high", "success"), ("low", "needs_review")],
)
def test_success_and_needs_review_share_formal_finalization(
    tmp_path: Path,
    llm_confidence: str,
    expected_status: str,
) -> None:
    source = tmp_path / "ordinary.docx"
    document = Document()
    document.add_paragraph("Ordinary synthetic document without classification keywords.")
    document.save(source)

    result = parse(
        str(source),
        parse_intent="metadata_only",
        knowledge_base=lambda raw_data: None,
        llm_extractor=lambda raw_data: {
            "category": "其他",
            "extracted_fields": {},
            "confidence": llm_confidence,
            "rule_source": "llm",
        },
    )

    assert result["status"] == expected_status
    assert result["parse_intent"] == "metadata_only"
    _assert_honestly_finalized(result)
