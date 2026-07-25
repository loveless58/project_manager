from pathlib import Path

import pytest

from platform_core.models import DocumentRef


def test_router_reads_through_the_reference_binding(tmp_path: Path) -> None:
    from integrations.document_store import DocumentStoreRouter, LocalDocumentStore
    from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry

    source = tmp_path / "a.md"
    source.write_bytes(b"document")
    bindings = StorageBindingRegistry(
        [
            StorageBinding(
                "source",
                "local",
                "node",
                "business://source/",
                tmp_path,
                ("source",),
                True,
                False,
            )
        ]
    )
    router = DocumentStoreRouter(bindings, {"source": LocalDocumentStore(tmp_path)})
    ref = DocumentRef("local", "a.md", "business://source/a.md", "source")

    stat = router.stat(ref)
    with router.open_read(ref) as stream:
        content = stream.read()

    assert stat.ref == ref
    assert content == b"document"


def test_router_requires_a_binding_id(tmp_path: Path) -> None:
    from integrations.document_store import DocumentStoreRouter, LocalDocumentStore
    from integrations.document_store.router import DocumentStoreRoutingError

    from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry

    bindings = StorageBindingRegistry(
        [
            StorageBinding(
                "source",
                "local",
                "node",
                "business://source/",
                tmp_path,
                ("source",),
                True,
                False,
            )
        ]
    )
    router = DocumentStoreRouter(bindings, {"source": LocalDocumentStore(tmp_path)})
    ref = DocumentRef("local", "a.md", "local://a.md")

    with pytest.raises(DocumentStoreRoutingError, match="binding_id"):
        router.open_read(ref)

def _router_with_two_bindings(tmp_path: Path):
    from integrations.document_store import DocumentStoreRouter, LocalDocumentStore
    from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry

    source_a = tmp_path / "a"
    source_b = tmp_path / "b"
    source_a.mkdir()
    source_b.mkdir()
    (source_a / "shared.md").write_bytes(b"a")
    (source_b / "shared.md").write_bytes(b"b")
    bindings = StorageBindingRegistry(
        [
            StorageBinding(
                "a",
                "local",
                "node",
                "business://a/",
                source_a,
                ("source",),
                True,
                False,
            ),
            StorageBinding(
                "b",
                "local",
                "node",
                "business://b/",
                source_b,
                ("source",),
                True,
                False,
            ),
        ]
    )
    return DocumentStoreRouter(
        bindings,
        {"a": LocalDocumentStore(source_a), "b": LocalDocumentStore(source_b)},
    )


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        (
            DocumentRef("local", "shared.md", "business://a/shared.md", "b"),
            "logical_uri",
        ),
        (
            DocumentRef("remote", "shared.md", "business://b/shared.md", "b"),
            "storage_provider",
        ),
    ],
)
def test_router_rejects_reference_claims_that_do_not_match_its_binding(
    tmp_path: Path, ref: DocumentRef, message: str
) -> None:
    from integrations.document_store.router import DocumentStoreRoutingError

    router = _router_with_two_bindings(tmp_path)

    with pytest.raises(DocumentStoreRoutingError, match=message):
        router.open_read(ref)


def test_router_rejects_unknown_provider_even_when_binding_id_is_mapped(
    tmp_path: Path,
) -> None:
    from integrations.document_store import DocumentStoreRouter, LocalDocumentStore
    from integrations.document_store.router import DocumentStoreRoutingError
    from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry

    source = tmp_path / "source"
    source.mkdir()
    bindings = StorageBindingRegistry(
        [
            StorageBinding(
                "source",
                "unknown",
                "node",
                "business://source/",
                source,
                ("source",),
                True,
                False,
            )
        ]
    )
    router = DocumentStoreRouter(bindings, {"source": LocalDocumentStore(source)})
    ref = DocumentRef("unknown", "a.md", "business://source/a.md", "source")

    with pytest.raises(DocumentStoreRoutingError, match="provider is not supported"):
        router.open_read(ref)

def test_router_rejects_unreadable_binding_even_if_a_store_is_injected(
    tmp_path: Path,
) -> None:
    from integrations.document_store import DocumentStoreRouter, LocalDocumentStore
    from integrations.document_store.router import DocumentStoreRoutingError
    from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry

    bindings = StorageBindingRegistry(
        [
            StorageBinding(
                "source",
                "local",
                "node",
                "business://source/",
                tmp_path,
                ("source",),
                False,
                False,
            )
        ]
    )
    router = DocumentStoreRouter(bindings, {"source": LocalDocumentStore(tmp_path)})
    ref = DocumentRef("local", "a.md", "business://source/a.md", "source")

    with pytest.raises(DocumentStoreRoutingError, match="not readable"):
        router.open_read(ref)
