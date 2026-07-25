"""Regression coverage for stable, non-sensitive LLM exception results."""

from __future__ import annotations

import json

from skills.document_parse import llm_extractor as llm_module
from skills.document_parse.llm_extractor import make_llm_extractor


def test_llm_exception_exposes_only_stable_code_and_message(monkeypatch) -> None:
    api_secret = "-".join(("sk", "live-secret-token"))
    windows_path = "C:" + r"\Users\alice\secret.docx"
    posix_path = "/" + "srv/private/secret.docx"
    configured_secret = "".join(("configured", "-secret-", "0123456789"))
    secret_fragments = (
        api_secret,
        "alice:password",
        "trace=true",
        "llm.internal.example",
        windows_path,
        posix_path,
    )
    detail = (
        f"Bearer {api_secret} failed at "
        "https://alice:password@llm.internal.example/v1/chat?trace=true "
        f"while reading {windows_path} and {posix_path}"
    )

    def fail_call(*args, **kwargs):
        raise RuntimeError(detail)

    monkeypatch.setattr(llm_module, "_call_llm", fail_call)
    extractor = make_llm_extractor(
        base_url="https://configured.example.invalid/v1",
        api_key=configured_secret,
    )

    result = extractor({"raw_text": "ordinary", "filename": "ordinary.docx"})

    assert result["llm_error"] == "DOCUMENT_PARSE.LLM.REQUEST_FAILED"
    assert result["llm_error_message"] == "LLM request failed."
    serialized = json.dumps(result, ensure_ascii=False)
    assert all(fragment not in serialized for fragment in secret_fragments)
    assert configured_secret not in serialized
