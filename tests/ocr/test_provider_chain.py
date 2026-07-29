from __future__ import annotations

from ocr.provider_chain import OcrProviderChain


class FakeProvider:
    def __init__(self, name: str, attempts: list[str], status: str) -> None:
        self.name = name
        self._attempts = attempts
        self._status = status

    def extract(self, _path: str) -> dict:
        self._attempts.append(self.name)
        if self._status == "success":
            return {
                "status": "success",
                "engine": self.name,
                "text": f"{self.name} extracted text",
                "pages": [{"page": 1, "text": "text", "confidence": 0.9}],
            }
        return {
            "status": "blocked",
            "engine": self.name,
            "blocked_reason": "ocr_provider_unavailable",
            "error": f"{self.name} unavailable",
        }


def test_chain_uses_rapidocr_before_mineru_and_easyocr(tmp_path) -> None:
    attempts: list[str] = []
    chain = OcrProviderChain(
        [
            FakeProvider("rapidocr", attempts, "blocked"),
            FakeProvider("mineru", attempts, "success"),
            FakeProvider("easyocr", attempts, "success"),
        ]
    )

    result = chain.extract(str(tmp_path / "scan.pdf"))

    assert attempts == ["rapidocr", "mineru"]
    assert result["status"] == "success"
    assert result["provider"] == "mineru"
    assert result["attempts"] == [
        {"provider": "rapidocr", "status": "blocked"},
        {"provider": "mineru", "status": "success"},
    ]


def test_chain_returns_needs_review_when_all_declared_providers_are_unavailable(tmp_path) -> None:
    attempts: list[str] = []
    result = OcrProviderChain(
        [
            FakeProvider("rapidocr", attempts, "blocked"),
            FakeProvider("mineru", attempts, "blocked"),
            FakeProvider("easyocr", attempts, "blocked"),
        ]
    ).extract(str(tmp_path / "scan.pdf"))

    assert attempts == ["rapidocr", "mineru", "easyocr"]
    assert result == {
        "status": "needs_review",
        "reason": "OCR.PROVIDERS_UNAVAILABLE",
        "attempts": [
            {"provider": "rapidocr", "status": "blocked"},
            {"provider": "mineru", "status": "blocked"},
            {"provider": "easyocr", "status": "blocked"},
        ],
    }
