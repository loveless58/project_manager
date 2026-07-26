"""Explicit OCR capability denial for read-only business-file runs."""

from __future__ import annotations

from typing import Any


class DisabledOcrProvider:
    """Return a stable blocked OCR result without probing or loading engines."""

    name = "disabled"
    blocked_reason = "OCR.CAPABILITY_DISABLED"
    disables_ocr_capability = True

    def probe(self) -> dict[str, Any]:
        """Describe the selected disabled capability without environment discovery."""
        return {
            "schema_version": "ocr.capabilities.v1",
            "status": "blocked",
            "engine": self.name,
            "blocked_reason": self.blocked_reason,
            "message": "OCR is disabled for this safe business-file run.",
        }

    def extract(self, _file_path: str) -> dict[str, Any]:
        """Return ``ocr.result.v1`` without reading OCR engines or subprocesses."""
        return {
            "schema_version": "ocr.result.v1",
            "status": "blocked",
            "engine": self.name,
            "text": "",
            "pages": [],
            "blocked_reason": self.blocked_reason,
            "error": "OCR capability is disabled; provide a natively readable document for review.",
        }


__all__ = ["DisabledOcrProvider"]
