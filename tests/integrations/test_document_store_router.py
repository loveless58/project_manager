from pathlib import Path

import pytest

from platform_core.models import DocumentRef


def test_router_reads_through_the_reference_binding(tmp_path: Path) -> None:
    from integrations.document_store import DocumentStoreRouter, LocalDocumentStore

    source = tmp_path / "a.md"
    source.write_bytes(b"document")
    router = DocumentStoreRouter({"source": LocalDocumentStore(tmp_path)})
    ref = DocumentRef("local", "a.md", "business://source/a.md", "source")

    stat = router.stat(ref)
    with router.open_read(ref) as stream:
        content = stream.read()

    assert stat.ref == ref
    assert content == b"document"


def test_router_requires_a_binding_id(tmp_path: Path) -> None:
    from integrations.document_store import DocumentStoreRouter, LocalDocumentStore
    from integrations.document_store.router import DocumentStoreRoutingError

    router = DocumentStoreRouter({"source": LocalDocumentStore(tmp_path)})
    ref = DocumentRef("local", "a.md", "local://a.md")

    with pytest.raises(DocumentStoreRoutingError, match="binding_id"):
        router.open_read(ref)
