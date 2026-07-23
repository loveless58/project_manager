def test_composition_api_is_exported_from_platform_core():
    import platform_core

    assert "RuntimeAdapters" in platform_core.__all__
    assert "build_default_registry" in platform_core.__all__
    assert "build_runtime_adapters" in platform_core.__all__
