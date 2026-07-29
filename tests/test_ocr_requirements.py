from pathlib import Path


def test_optional_ocr_requirements_declare_portable_provider_chain():
    requirements = Path("requirements-ocr.txt").read_text(encoding="utf-8").lower()

    assert "rapidocr" in requirements
    assert "onnxruntime" in requirements
    assert "mineru[all]" in requirements
    assert "easyocr" in requirements
