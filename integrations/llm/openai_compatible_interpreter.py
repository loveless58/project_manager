"""OpenAI-compatible, JSON-only document interpretation adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Any

import requests

from common.provider_config import resolve_llm_api_key, resolve_llm_base_url, resolve_llm_model


_MAX_RESPONSE_CHARS = 32_000
_SYSTEM_PROMPT = (
    "Return only one JSON object that conforms exactly to candidate_document_interpretation.v1. "
    "Do not add markdown or prose. Use only the supplied evidence pack; do not infer or confirm business relations."
)


class DocumentInterpreterRequestError(RuntimeError):
    """A stable public error that deliberately omits provider details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Document interpretation request failed.")


class OpenAICompatibleInterpreter:
    name = "openai_compatible"
    schema_version = "candidate_document_interpretation.v1"
    prompt_version = "document_interpretation.v1"
    policy_version = "document_interpretation_policy.v1"

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, *, transport: Callable[..., Any] | None = None) -> None:
        self.base_url = resolve_llm_base_url(base_url, required=True)
        self.api_key = resolve_llm_api_key(api_key, required=True)
        self.model = resolve_llm_model(model)
        self._transport = transport or requests.post

    def complete_json(self, request: Mapping[str, Any]) -> str:
        try:
            evidence_json = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            response = self._transport(
                url=f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": evidence_json}],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
                timeout=60,
            )
            response.raise_for_status()
            content = _content(response.json())
            if len(content) > _MAX_RESPONSE_CHARS:
                raise DocumentInterpreterRequestError("DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID")
            return content
        except DocumentInterpreterRequestError:
            raise
        except Exception:
            raise DocumentInterpreterRequestError("DOCUMENT_INTERPRETATION.LLM.REQUEST_FAILED") from None


def _content(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise DocumentInterpreterRequestError("DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise DocumentInterpreterRequestError("DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID")
    message = choices[0].get("message")
    if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
        raise DocumentInterpreterRequestError("DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID")
    return message["content"]


__all__ = ["DocumentInterpreterRequestError", "OpenAICompatibleInterpreter"]
