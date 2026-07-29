from ocr.providers import MineruProvider


def test_providers_export_mineru_provider():
    assert MineruProvider.name == "mineru"
