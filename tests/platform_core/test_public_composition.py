def test_composition_api_is_importable_from_platform_core():
    from platform_core import RuntimeAdapters, build_default_registry, build_runtime_adapters

    assert RuntimeAdapters.__name__ == "RuntimeAdapters"
    assert callable(build_default_registry)
    assert callable(build_runtime_adapters)
