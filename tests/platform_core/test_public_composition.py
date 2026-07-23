import subprocess
import sys


def test_composition_api_is_importable_from_application_bootstrap():
    from app_bootstrap import RuntimeAdapters, build_default_registry, build_runtime_adapters

    assert RuntimeAdapters.__name__ == "RuntimeAdapters"
    assert callable(build_default_registry)
    assert callable(build_runtime_adapters)


def test_importing_platform_core_does_not_expose_bootstrap_or_load_integrations():
    script = """
import sys
import platform_core

assert not hasattr(platform_core, "build_runtime_adapters")
assert not any(name == "integrations" or name.startswith("integrations.") for name in sys.modules)
"""

    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
