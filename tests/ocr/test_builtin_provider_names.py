from ocr.providers import EasyOcrProvider, RapidOcrProvider


def test_builtin_ocr_providers_expose_chain_names():
    assert RapidOcrProvider.name == "rapidocr"
    assert EasyOcrProvider.name == "easyocr"
