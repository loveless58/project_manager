from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DocumentInterpreter(Protocol):
    """Completes a JSON-only request using the supplied controlled evidence pack."""

    name: str
    model: str
    schema_version: str
    prompt_version: str
    policy_version: str

    def complete_json(self, request: Mapping[str, Any]) -> object:
        raise NotImplementedError


__all__ = ["DocumentInterpreter"]
