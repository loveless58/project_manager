"""Regression coverage for stable, non-sensitive LLM exception results."""

from __future__ import annotations

import json

from skills.document_parse import llm_extractor as llm_module
from skills.document_parse.llm_extractor import make_llm_extractor


def test_llm_exception_exposes_only_stable_code_and_message(monkeypatch) -> None:
    secret_fragments = (
        "sk-live-secret-token",
        "alice:password",
        "trace=true",
        "llm.internal.example",
        r"C:\\Users\\alice\\secret.docx",
        "/srv/private/secret.docx",
    )
    detail = (
        "Bearer sk-live-secret-token failed at "
        "https://alice:password@llm.internal.example/v1/chat?trace=true "
        r"while reading C:\Users\alice\secret.docx and /srv/private/secret.docx"
    )

    def fail_call(*args, **kwargs):
        raise RuntimeError(detail)

    monkeypatch.setattr(llm_module, "_call_llm", fail_call)
    extractor = make_llm_extractor(
        base_url="https://configured.example.invalid/v1",
        api_key="configured-secret",
    )

    result = extractor({"raw_text": "ordinary", "filename": "ordinary.docx"})

    assert result["llm_error"] == "DOCUMENT_PARSE.LLM.REQUEST_FAILED"
    assert result["llm_error_message"] == "LLM request failed."
    serialized = json.dumps(result, ensure_ascii=False)
    assert all(fragment not in serialized for fragment in secret_fragments)
    assert "configured-secret" not in serialized
