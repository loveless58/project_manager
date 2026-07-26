"""One-response, fail-closed adapter for an explicitly supplied agent exchange."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from contracts.agent_judgement import parse_agent_judgement_response
from integrations.llm.openai_compatible_interpreter import DocumentInterpreterRequestError


_SCHEMA_VERSION = "candidate_document_interpretation.v1"
_PROMPT_VERSION = "document_interpretation.v1"
_POLICY_VERSION = "document_interpretation_policy.v1"
_FALLBACK_IDENTITY = "agent_response"


class AgentResponseInterpreter:
    """Expose one already-supplied agent response as a document interpreter."""

    schema_version = _SCHEMA_VERSION
    prompt_version = _PROMPT_VERSION
    policy_version = _POLICY_VERSION

    def __init__(self, response: object, *, request: Mapping[str, Any]) -> None:
        self._response = response
        self._request = _json_copy(request)
        self.name = _identity(response, "interpreter")
        self.model = _identity(response, "model")

    def complete_json(self, request: Mapping[str, Any]) -> str:
        try:
            current_request = _json_copy(request)
            if (
                type(self._request) is not dict
                or current_request
                != self._request.get("interpretation_request")
            ):
                raise ValueError("request binding")

            envelope = parse_agent_judgement_response(
                self._response,
                request=self._request,
            )
            interpretation = envelope["interpretation"]
            if (
                interpretation["schema_version"],
                interpretation["prompt_version"],
                interpretation["policy_version"],
            ) != (
                self.schema_version,
                self.prompt_version,
                self.policy_version,
            ):
                raise ValueError("interpretation version")
            self.name = envelope["interpreter"]
            self.model = envelope["model"]
            return json.dumps(
                interpretation,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except DocumentInterpreterRequestError:
            raise
        except Exception:
            raise DocumentInterpreterRequestError(
                "DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID"
            ) from None


def _identity(response: object, field: str) -> str:
    if isinstance(response, Mapping):
        value = response.get(field)
        if type(value) is str and value:
            return value
    return _FALLBACK_IDENTITY


def _json_copy(value: object) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        return None


__all__ = ["AgentResponseInterpreter", "DocumentInterpreterRequestError"]

