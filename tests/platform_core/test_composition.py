import pytest

from platform_core.ports import DocumentStore, ProjectionWriter, StructureIndex
from platform_core.settings import load_app_settings


def test_local_runtime_builds_declared_adapters(tmp_path):
    from app_bootstrap.composition import build_runtime_adapters

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
    from app_bootstrap.composition import build_runtime_adapters

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
    from app_bootstrap.composition import build_runtime_adapters
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

def test_runtime_exposes_binding_registry_and_router(tmp_path):
    from app_bootstrap.composition import build_runtime_adapters
    from integrations.document_store import DocumentStoreRouter
    from platform_core.storage_bindings import StorageBindingRegistry

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "business"),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
        },
    )

    adapters = build_runtime_adapters(settings)

    assert isinstance(adapters.storage_binding_registry, StorageBindingRegistry)
    assert isinstance(adapters.document_store_router, DocumentStoreRouter)
    assert set(adapters.document_store_router.stores_by_binding) == {
        "legacy-business-root"
    }

def test_runtime_adapters_preserve_legacy_three_argument_construction() -> None:
    from app_bootstrap.composition import RuntimeAdapters

    document_store = object()
    structure_index = object()
    projection_writer = object()

    adapters = RuntimeAdapters(document_store, structure_index, projection_writer)

    assert adapters.document_store is document_store
    assert adapters.structure_index is structure_index
    assert adapters.projection_writer is projection_writer
    assert adapters.storage_binding_registry is None
    assert adapters.document_store_router is None


def test_runtime_adapters_preserve_legacy_keyword_construction() -> None:
    from app_bootstrap.composition import RuntimeAdapters

    document_store = object()
    structure_index = object()
    projection_writer = object()

    adapters = RuntimeAdapters(
        document_store=document_store,
        structure_index=structure_index,
        projection_writer=projection_writer,
    )

    assert adapters.document_store is document_store
    assert adapters.structure_index is structure_index
    assert adapters.projection_writer is projection_writer

def test_runtime_does_not_create_a_router_store_for_unreadable_binding(tmp_path):
    from app_bootstrap.composition import build_runtime_adapters
    from platform_core.storage_bindings import StorageBinding

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "business"),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
        },
    )
    unreadable = StorageBinding(
        "source",
        "local",
        "node",
        "business://source/",
        tmp_path / "source",
        ("source",),
        False,
        False,
    )
    settings = settings.__class__(
        deployment_mode=settings.deployment_mode,
        business_root=settings.business_root,
        storage_bindings=(unreadable,),
        runtime_workspace=settings.runtime_workspace,
        database=settings.database,
        providers=settings.providers,
    )

    adapters = build_runtime_adapters(settings)

    assert adapters.document_store_router.stores_by_binding == {}

@pytest.mark.parametrize(
    "field_name",
    ["runtime_workspace", "projection_root", "sqlite_path"],
)
def test_runtime_composition_rejects_binding_nested_in_runtime_owned_path(
    tmp_path,
    field_name,
):
    """Hand-built AppSettings must not bypass Settings path isolation."""
    from dataclasses import replace

    from app_bootstrap.composition import build_runtime_adapters
    from platform_core.settings import SettingsError
    from platform_core.storage_bindings import StorageBinding

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "business"),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
            "PROJECT_MANAGER_SQLITE_PATH": str(tmp_path / "state.sqlite3"),
            "PROJECT_MANAGER_PROJECTION_ROOT": str(tmp_path / "projection"),
        },
    )
    protected = {
        "runtime_workspace": settings.runtime_workspace,
        "projection_root": settings.providers.projection_root,
        "sqlite_path": settings.database.sqlite_path,
    }[field_name]
    assert protected is not None
    binding = StorageBinding(
        "source",
        "local",
        "node-a",
        "business://source/",
        protected / "source",
        ("source",),
        True,
        False,
    )
    settings = replace(settings, storage_bindings=(binding,))

    with pytest.raises(
        SettingsError,
        match=rf"storage binding must not be inside {field_name}",
    ):
        build_runtime_adapters(settings)
