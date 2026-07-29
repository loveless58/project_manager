"""Explicit, ordered OCR provider selection for file-organizer skills."""

from __future__ import annotations

from typing import Any, Iterable


class OcrProviderChain:
    """Try declared providers in order without installing or discovering extras."""

    def __init__(self, providers: Iterable[object]) -> None:
        self._providers = tuple(providers)

    def extract(self, path: str) -> dict[str, Any]:
        attempts: list[dict[str, str]] = []
        for provider in self._providers:
            name = str(getattr(provider, "name", "")).strip()
            if not name:
                continue
            try:
                result = provider.extract(path)
            except Exception:
                result = {"status": "failed"}
            status = str(result.get("status", "failed")) if isinstance(result, dict) else "failed"
            attempts.append({"provider": name, "status": status})
            if status == "success" and isinstance(result, dict):
                return {**result, "provider": name, "attempts": attempts}
        return {
            "status": "needs_review",
            "reason": "OCR.PROVIDERS_UNAVAILABLE",
            "attempts": attempts,
        }


__all__ = ["OcrProviderChain"]
