import pytest

from platform_core.registry import AdapterKind, AdapterRegistry, AdapterRegistryError
from platform_core.settings import load_app_settings


def test_factory_is_explicit_and_lazy():
    registry = AdapterRegistry()
    calls = []
    registry.register(AdapterKind.DOCUMENT_STORE, "fake", lambda settings: calls.append(settings) or "adapter")
    settings = load_app_settings(config_file="", environ={})

    assert calls == []
    assert registry.build(AdapterKind.DOCUMENT_STORE, "fake", settings) == "adapter"
    assert calls == [settings]


def test_duplicate_registration_is_rejected():
    registry = AdapterRegistry()
    registry.register(AdapterKind.STRUCTURE_INDEX, "fake", lambda settings: object())

    with pytest.raises(AdapterRegistryError, match="already registered"):
        registry.register(AdapterKind.STRUCTURE_INDEX, "fake", lambda settings: object())


def test_unknown_adapter_lists_available_names():
    registry = AdapterRegistry()
    registry.register(AdapterKind.PROJECTION_WRITER, "filesystem", lambda settings: object())
    settings = load_app_settings(config_file="", environ={})

    with pytest.raises(AdapterRegistryError, match="available=filesystem"):
        registry.build(AdapterKind.PROJECTION_WRITER, "missing", settings)
