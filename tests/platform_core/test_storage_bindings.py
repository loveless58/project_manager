from pathlib import Path

import pytest


def test_nested_roots_are_ambiguous(tmp_path: Path) -> None:
    from platform_core.storage_bindings import (
        AmbiguousStorageBindingError,
        StorageBinding,
        StorageBindingRegistry,
    )

    registry = StorageBindingRegistry(
        [
            StorageBinding("shared", "local", "node", "business://shared/", tmp_path / "shared", ("source",), True, False),
            StorageBinding(
                "incoming",
                "local",
                "node",
                "business://incoming/",
                tmp_path / "shared" / "incoming",
                ("source",),
                True,
                False,
            ),
        ]
    )

    with pytest.raises(AmbiguousStorageBindingError):
        registry.document_ref_from_path(tmp_path / "shared" / "incoming" / "a.md")

def test_document_ref_from_path_uses_the_matching_binding(tmp_path: Path) -> None:
    from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry

    registry = StorageBindingRegistry(
        [
            StorageBinding(
                "shared",
                "local",
                "node",
                "business://shared/",
                tmp_path / "shared",
                ("source",),
                True,
                False,
            )
        ]
    )

    ref = registry.document_ref_from_path(tmp_path / "shared" / "a.md")

    assert ref.storage_provider == "local"
    assert ref.object_key == "a.md"
    assert ref.logical_uri == "business://shared/a.md"
    assert ref.binding_id == "shared"

def test_storage_binding_types_are_exported_from_platform_core() -> None:
    from platform_core import StorageBinding, StorageBindingRegistry

    assert StorageBinding.__name__ == "StorageBinding"
    assert StorageBindingRegistry.__name__ == "StorageBindingRegistry"
