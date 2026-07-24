import subprocess
from pathlib import Path

from platform_core.models import StructureIndexRequest
from platform_core.ports import StructureIndex


class FakePageIndexClient:
    provider_version = "fake-pageindex-v1"

    def __init__(self):
        self.environment_checked = False

    def check_environment(self):
        self.environment_checked = True

    def index_pdf(self, source_path, **kwargs):
        return {
            "status": "success",
            "engine": "pageindex",
            "doc_name": "a.pdf",
            "doc_id": "pageindex-doc-1",
            "operation_id": kwargs.get("operation_id"),
            "provider_version": self.provider_version,
            "content_hash": kwargs.get("expected_content_hash"),
            "structure": [{"title": "第一章", "start_index": 1, "end_index": 3}],
            "structure_json_path": "/runtime/results/a_structure.json",
            "elapsed_seconds": 0.01,
        }

    def index_md(self, source_path, **kwargs):
        return self.index_pdf(source_path, **kwargs)


def test_pageindex_adapter_probe_checks_the_client_environment(tmp_path):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    client = FakePageIndexClient()
    adapter = PageIndexStructureIndex(tmp_path, client_factory=lambda pageindex_dir: client)

    report = adapter.probe()

    assert report.status == "ready"
    assert client.environment_checked is True


def test_pageindex_adapter_maps_success_without_exposing_path_as_identity(tmp_path):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    adapter = PageIndexStructureIndex(
        pageindex_dir=tmp_path,
        client_factory=lambda pageindex_dir: FakePageIndexClient(),
    )
    request = StructureIndexRequest("dv-1", "a" * 64, "/docs/a.pdf", "application/pdf")

    result = adapter.index(request)

    assert isinstance(adapter, StructureIndex)
    assert result.status == "success"
    assert result.provider == "pageindex"
    assert result.external_ref.startswith("pageindex://")
    assert "/runtime/results" not in result.external_ref
    assert result.structure[0]["title"] == "第一章"


def test_pageindex_adapter_returns_blocked_when_provider_is_unavailable(tmp_path):
    from integrations.pageindex.pageindex_client import PageIndexError
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    def unavailable(pageindex_dir):
        raise PageIndexError("missing runtime")

    adapter = PageIndexStructureIndex(tmp_path, client_factory=unavailable)
    request = StructureIndexRequest("dv-1", "a" * 64, "/docs/a.pdf", "application/pdf")

    assert adapter.probe().status == "blocked"
    result = adapter.index(request)
    assert result.status == "blocked"
    assert result.error_code == "INDEX.PROVIDER_UNAVAILABLE"


def test_pageindex_adapter_blocks_none_provider_version(tmp_path):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    class MissingVersionClient(FakePageIndexClient):
        provider_version = None

    client = MissingVersionClient()
    adapter = PageIndexStructureIndex(
        tmp_path,
        client_factory=lambda pageindex_dir: client,
    )
    request = StructureIndexRequest(
        "dv-1",
        "a" * 64,
        "/docs/a.pdf",
        "application/pdf",
    )

    assert adapter.probe().status == "blocked"
    result = adapter.index(request)
    assert result.status == "blocked"
    assert result.error_code == "INDEX.PROVIDER_UNAVAILABLE"


def test_pageindex_adapter_maps_malformed_provider_results_to_stable_failure(
    tmp_path,
):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    class MalformedResultClient(FakePageIndexClient):
        def __init__(self, raw_result):
            super().__init__()
            self.raw_result = raw_result

        def index_pdf(self, source_path, **kwargs):
            return self.raw_result

    request = StructureIndexRequest(
        "dv-malformed-result",
        "a" * 64,
        "/docs/a.pdf",
        "application/pdf",
    )
    for raw_result in (None, []):
        adapter = PageIndexStructureIndex(
            tmp_path,
            client_factory=lambda pageindex_dir, raw_result=raw_result: (
                MalformedResultClient(raw_result)
            ),
        )
        result = adapter.index(request)
        assert result.status == "failed"
        assert result.error_code == "INDEX.PROVIDER_FAILED"
        assert result.error == "PageIndex provider failed."


def test_pageindex_adapter_probe_blocks_when_runtime_file_cannot_start(tmp_path, monkeypatch):
    from integrations.pageindex.pageindex_client import PageIndexClient
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    client = PageIndexClient(str(tmp_path))
    python_bin = Path(client.python_bin)
    python_bin.parent.mkdir(parents=True)
    python_bin.touch()
    Path(client.cli_script).touch()
    calls = []

    def cannot_start(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("broken executable")

    monkeypatch.setattr(subprocess, "run", cannot_start)
    report = PageIndexStructureIndex(tmp_path).probe()

    assert report.status == "blocked"
    assert report.provider == "pageindex"
    assert report.reason == "PageIndex provider is unavailable."
    assert len(calls) == 1


def test_pageindex_adapter_rejects_invalid_content_hash_before_client_creation(
    tmp_path,
):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    calls = []

    def forbidden(pageindex_dir):
        calls.append(pageindex_dir)
        raise AssertionError("invalid identity must not create provider client")

    adapter = PageIndexStructureIndex(tmp_path, client_factory=forbidden)
    request = StructureIndexRequest(
        "dv-invalid",
        "not-a-sha256",
        "/docs/a.pdf",
        "application/pdf",
    )

    result = adapter.index(request)

    assert result.status == "failed"
    assert result.error_code == "INDEX.INPUT_HASH_INVALID"
    assert calls == []


def test_pageindex_adapter_rejects_provider_version_mismatch(tmp_path):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    class VersionMismatchClient(FakePageIndexClient):
        def index_pdf(self, source_path, **kwargs):
            raw = super().index_pdf(source_path, **kwargs)
            raw["provider_version"] = "different-provider-version"
            return raw

    adapter = PageIndexStructureIndex(
        tmp_path,
        client_factory=lambda pageindex_dir: VersionMismatchClient(),
    )
    request = StructureIndexRequest(
        "dv-provider",
        "a" * 64,
        "/docs/a.pdf",
        "application/pdf",
    )

    result = adapter.index(request)

    assert result.status == "failed"
    assert result.error_code == "INDEX.RESULT_IDENTITY_MISMATCH"


def test_pageindex_adapter_rejects_unsupported_media_type_before_provider_probe(tmp_path):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    def unavailable(pageindex_dir):
        raise AssertionError("unsupported media type must not create a provider client")

    adapter = PageIndexStructureIndex(tmp_path, client_factory=unavailable)
    request = StructureIndexRequest(
        "dv-1",
        "hash-1",
        "/docs/a.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    result = adapter.index(request)

    assert result.status == "blocked"
    assert result.error_code == "INDEX.UNSUPPORTED_MEDIA_TYPE"
