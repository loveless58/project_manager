from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from platform_core.models import DocumentRef
from platform_core.ports import DocumentStore


def test_local_store_reads_object_inside_root(tmp_path: Path) -> None:
    from integrations.document_store.local_store import LocalDocumentStore

    source = tmp_path / "project" / "a.txt"
    source.parent.mkdir()
    source.write_bytes(b"abc")
    store = LocalDocumentStore(tmp_path)
    ref = DocumentRef("local", "project/a.txt", "local://project/a.txt")

    stat = store.stat(ref)
    with store.open_read(ref) as stream:
        content = stream.read()

    assert isinstance(store, DocumentStore)
    assert stat.size_bytes == 3
    assert stat.available is True
    assert content == b"abc"


def test_local_store_rejects_path_escape(tmp_path: Path) -> None:
    from integrations.document_store.local_store import DocumentStorePathError, LocalDocumentStore

    store = LocalDocumentStore(tmp_path)
    ref = DocumentRef("local", "../secret.txt", "local://../secret.txt")

    with pytest.raises(DocumentStorePathError, match="outside configured root"):
        store.stat(ref)


def test_local_store_rejects_absolute_path(tmp_path: Path) -> None:
    from integrations.document_store.local_store import DocumentStorePathError, LocalDocumentStore

    outside = tmp_path.parent / "secret.txt"
    ref = DocumentRef("local", str(outside), f"local://{outside.as_posix()}")

    with pytest.raises(DocumentStorePathError, match="outside configured root"):
        LocalDocumentStore(tmp_path).stat(ref)


def test_local_store_rejects_existing_symlink_to_outside_root(tmp_path: Path) -> None:
    from integrations.document_store.local_store import DocumentStorePathError, LocalDocumentStore

    outside = tmp_path.parent / "secret.txt"
    outside.write_bytes(b"secret")
    symlink = tmp_path / "escaped.txt"
    try:
        symlink.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    ref = DocumentRef("local", "escaped.txt", "local://escaped.txt")

    with pytest.raises(DocumentStorePathError, match="outside configured root"):
        LocalDocumentStore(tmp_path).open_read(ref)


def test_local_store_rejects_provider_mismatch(tmp_path: Path) -> None:
    from integrations.document_store.local_store import DocumentStorePathError, LocalDocumentStore

    store = LocalDocumentStore(tmp_path)
    ref = DocumentRef("remote", "project/a.txt", "remote://project/a.txt")

    with pytest.raises(DocumentStorePathError, match="provider mismatch"):
        store.open_read(ref)


def test_local_store_stat_etag_uses_file_size_and_modification_time(tmp_path: Path) -> None:
    from integrations.document_store.local_store import LocalDocumentStore

    source = tmp_path / "a.txt"
    source.write_bytes(b"abc")
    modified_ns = 1_700_000_000_123_456_789
    os.utime(source, ns=(modified_ns, modified_ns))
    ref = DocumentRef("local", "a.txt", "local://a.txt")

    stat = LocalDocumentStore(tmp_path).stat(ref)

    actual_modified_ns = source.stat().st_mtime_ns
    assert stat.etag == hashlib.sha256(f"3:{actual_modified_ns}".encode("ascii")).hexdigest()


def test_disabled_store_implements_protocol_and_rejects_operations() -> None:
    from integrations.document_store.disabled_store import DisabledDocumentStore, DocumentStoreUnavailable

    store = DisabledDocumentStore()
    ref = DocumentRef("disabled", "a.txt", "disabled://a.txt")

    assert isinstance(store, DocumentStore)
    with pytest.raises(DocumentStoreUnavailable, match="document store is disabled"):
        store.stat(ref)
    with pytest.raises(DocumentStoreUnavailable, match="document store is disabled"):
        store.open_read(ref)
