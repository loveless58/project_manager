import types

from ocr.providers.easyocr_provider import EasyOcrProvider


def test_easyocr_provider_disables_implicit_model_downloads():
    captured = {}

    def reader(languages, **kwargs):
        captured["languages"] = languages
        captured["kwargs"] = kwargs
        return object()

    provider = EasyOcrProvider()
    provider._get_reader(types.SimpleNamespace(Reader=reader))

    assert captured["languages"] == ["ch_sim", "en"]
    assert captured["kwargs"]["download_enabled"] is False
