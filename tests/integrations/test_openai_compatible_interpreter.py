from __future__ import annotations

import json


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


def test_openai_compatible_interpreter_uses_json_only_and_configured_transport() -> None:
    from integrations.llm.openai_compatible_interpreter import OpenAICompatibleInterpreter

    captured = {}

    def transport(**kwargs):
        captured.update(kwargs)
        return FakeResponse(
            {"choices": [{"message": {"content": '{"status":"success"}'}}]}
        )

    interpreter = OpenAICompatibleInterpreter(
        base_url="https://llm.example.invalid/v1",
        api_key="fake-key-for-test",
        model="fake-model",
        transport=transport,
    )

    result = interpreter.complete_json({"schema_version": "document_interpretation_evidence_pack.v1"})

    assert json.loads(result) == {"status": "success"}
    assert captured["url"] == "https://llm.example.invalid/v1/chat/completions"
    assert captured["timeout"] == 60
    assert captured["json"]["model"] == "fake-model"
    assert captured["json"]["temperature"] == 0.1
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["Authorization"] == "Bearer fake-key-for-test"
    assert "JSON" in captured["json"]["messages"][0]["content"]


def test_openai_compatible_interpreter_redacts_transport_failures() -> None:
    from integrations.llm.openai_compatible_interpreter import (
        DocumentInterpreterRequestError,
        OpenAICompatibleInterpreter,
    )

    token_value = "sk-live-secret"
    private_path = "C:" + r"\\Users\\alice\\private-contract.pdf"

    def transport(**kwargs):
        raise RuntimeError(f"Bearer {token_value} failed at {private_path}")

    interpreter = OpenAICompatibleInterpreter(
        base_url="https://llm.example.invalid/v1",
        api_key=token_value,
        model="fake-model",
        transport=transport,
    )

    try:
        interpreter.complete_json({"schema_version": "document_interpretation_evidence_pack.v1"})
    except DocumentInterpreterRequestError as error:
        assert error.code == "DOCUMENT_INTERPRETATION.LLM.REQUEST_FAILED"
        assert str(error) == "Document interpretation request failed."
        assert token_value not in str(error)
        assert "alice" not in str(error)
    else:
        raise AssertionError("Expected a stable, sanitized request error")
