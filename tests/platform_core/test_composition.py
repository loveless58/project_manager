import pytest

from platform_core.ports import DocumentStore, ProjectionWriter, StructureIndex
from platform_core.settings import load_app_settings


def test_local_runtime_builds_declared_adapters(tmp_path):
    from platform_core.composition import build_runtime_adapters

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "business"),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
            "PROJECT_MANAGER_DOCUMENT_STORE": "local",
            "PROJECT_MANAGER_STRUCTURE_INDEX": "disabled",
            "PROJECT_MANAGER_PROJECTION_WRITER": "filesystem",
        },
    )

    adapters = build_runtime_adapters(settings)

    assert isinstance(adapters.document_store, DocumentStore)
    assert isinstance(adapters.structure_index, StructureIndex)
    assert isinstance(adapters.projection_writer, ProjectionWriter)
    assert adapters.summary() == {
        "document_store": "local",
        "structure_index": "disabled",
        "projection_writer": "filesystem",
    }


def test_central_control_plane_can_disable_document_store(tmp_path):
    from platform_core.composition import build_runtime_adapters

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_DEPLOYMENT_MODE": "central",
            "PROJECT_MANAGER_DATABASE_PROVIDER": "postgresql",
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
            "PROJECT_MANAGER_DOCUMENT_STORE": "disabled",
        },
    )

    adapters = build_runtime_adapters(settings)

    assert adapters.document_store.name == "disabled"


def test_unknown_provider_remains_an_explicit_configuration_error(tmp_path):
    from platform_core.composition import build_runtime_adapters
    from platform_core.registry import AdapterRegistryError

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "business"),
            "PROJECT_MANAGER_DOCUMENT_STORE": "unregistered",
        },
    )

    with pytest.raises(AdapterRegistryError, match="unknown document_store adapter"):
        build_runtime_adapters(settings)
