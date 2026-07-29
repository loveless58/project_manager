import os
import sys
import tempfile
import types
from unittest.mock import patch

from ocr.providers.rapidocr_provider import RapidOcrProvider


def test_rapidocr_provider_prefers_current_rapidocr_package():
    fake_engine = lambda path: (
        [[[[0, 0], [1, 0], [1, 1], [0, 1]], "current rapidocr", 0.95]],
        [0.05, 0.10, 0.20],
    )
    fake_rapidocr = types.SimpleNamespace(RapidOCR=lambda: fake_engine)

    with tempfile.TemporaryDirectory() as td:
        image_path = os.path.join(td, "scan.png")
        with open(image_path, "wb") as file_handle:
            file_handle.write(b"fake image bytes")

        with patch.dict(sys.modules, {"rapidocr": fake_rapidocr}):
            provider = RapidOcrProvider(dependency_probe=lambda name: name == "rapidocr")
            result = provider.extract(image_path)

    assert result["status"] == "success"
    assert result["engine"] == "rapidocr"
    assert "current rapidocr" in result["text"]
