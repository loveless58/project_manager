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

@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("binding_id", " source ", "binding_id must not contain leading or trailing whitespace"),
        ("provider", " local ", "provider must not contain leading or trailing whitespace"),
    ],
)
def test_storage_binding_rejects_whitespace_identifiers(
    tmp_path: Path, field_name: str, value: str, message: str
) -> None:
    from platform_core.storage_bindings import StorageBinding, StorageBindingError

    values = {
        "binding_id": "source",
        "provider": "local",
        "node_id": "node",
        "logical_root": "business://source/",
        "physical_root": tmp_path / "source",
        "roles": ("source",),
        "readable": True,
        "writable": False,
    }
    values[field_name] = value

    with pytest.raises(StorageBindingError, match=message):
        StorageBinding(**values)


def test_unreadable_binding_cannot_generate_a_read_reference(tmp_path: Path) -> None:
    from platform_core.storage_bindings import (
        StorageBinding,
        StorageBindingNotFoundError,
        StorageBindingRegistry,
    )

    registry = StorageBindingRegistry(
        [
            StorageBinding(
                "source",
                "local",
                "node",
                "business://source/",
                tmp_path / "source",
                ("source",),
                False,
                False,
            )
        ]
    )

    with pytest.raises(StorageBindingNotFoundError, match="enabled and readable"):
        registry.document_ref_from_path(tmp_path / "source" / "a.md")

def test_registry_does_not_create_reference_for_symlink_escaping_binding(
    tmp_path: Path,
) -> None:
    from platform_core.storage_bindings import (
        StorageBinding,
        StorageBindingNotFoundError,
        StorageBindingRegistry,
    )

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_bytes(b"outside")
    symlink = root / "escaped"
    try:
        symlink.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    registry = StorageBindingRegistry(
        [
            StorageBinding(
                "source",
                "local",
                "node",
                "business://source/",
                root,
                ("source",),
                True,
                False,
            )
        ]
    )

    with pytest.raises(StorageBindingNotFoundError):
        registry.document_ref_from_path(symlink / "a.md")
