import json
import sys
import types


_SENSITIVE_ERROR = (
    "request failed at https://user:top-secret@llm.example.invalid/v1 for "
    + "/".join(
        ("C:", "Users", "alice", "private-contract.pdf")
    )
)


def _failing_litellm():
    return types.SimpleNamespace(
        completion=lambda **kwargs: (_ for _ in ()).throw(RuntimeError(_SENSITIVE_ERROR))
    )


def _assert_sanitized(result):
    serialized = json.dumps(result, ensure_ascii=False)
    for value in ("user", "top-secret", "alice", "private-contract", _SENSITIVE_ERROR):
        assert value not in serialized


def test_archive_llm_failures_keep_stable_codes_and_exclude_exception_text(
    monkeypatch,
):
    from skills.archive_files.scripts import build_archive_decision as archive

    monkeypatch.setattr(archive, "_read_knowledge_base", lambda: "knowledge")
    monkeypatch.setattr(
        archive, "resolve_llm_base_url", lambda required: "https://llm.example.invalid/v1"
    )
    monkeypatch.setitem(sys.modules, "litellm", _failing_litellm())

    knowledge_result = archive._search_knowledge_base("proposal.pdf", None)
    fallback_result = archive._llm_general_fallback("proposal.pdf", None)

    assert knowledge_result["reasoning"] == "LLM request failed."
    assert knowledge_result["llm_error"] == "ARCHIVE.LLM.REQUEST_FAILED"
    assert fallback_result["reasoning"] == "LLM request failed."
    assert fallback_result["llm_error"] == "ARCHIVE.LLM.REQUEST_FAILED"
    _assert_sanitized(knowledge_result)
    _assert_sanitized(fallback_result)
