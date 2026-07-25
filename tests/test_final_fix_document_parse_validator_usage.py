"""Ensure every document_parse terminal path invokes the formal validator."""

from __future__ import annotations

import importlib


def test_blocked_route_result_is_finalized_through_formal_validator(monkeypatch) -> None:
    parse_module = importlib.import_module("skills.document_parse.parse")
    real_validate = parse_module.validate
    calls: list[dict[str, object]] = []

    def recording_validate(result):
        calls.append(result)
        return real_validate(result)

    monkeypatch.setattr(parse_module, "validate", recording_validate)

    result = parse_module.parse("unsupported.synthetic", parse_intent="raw_content")

    assert calls == [result]
