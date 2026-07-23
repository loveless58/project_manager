from io import BytesIO

from platform_core.models import (
    CapabilityReport,
    DocumentRef,
    ObjectStat,
    ProjectionRef,
    ProjectionRequest,
    StructureIndexRequest,
    StructureIndexResult,
)
from platform_core.ports import DocumentStore, ProjectionWriter, StructureIndex


class FakeDocumentStore:
    name = "fake"

    def stat(self, ref):
        return ObjectStat(ref=ref, size_bytes=3, modified_at=0.0, etag="abc", available=True)

    def open_read(self, ref):
        return BytesIO(b"abc")


class FakeStructureIndex:
    name = "fake"

    def probe(self):
        return CapabilityReport(status="ready", provider="fake", provider_version="1", reason="ready")

    def index(self, request):
        return StructureIndexResult(
            status="success",
            provider="fake",
            external_ref="memory://1",
            structure=(),
            error_code="",
            error="",
        )


class FakeProjectionWriter:
    name = "fake"

    def write(self, request):
        return ProjectionRef(provider="fake", logical_uri="memory://result", sha256="abc", size_bytes=3)


def test_structural_protocols_accept_matching_adapters():
    assert isinstance(FakeDocumentStore(), DocumentStore)
    assert isinstance(FakeStructureIndex(), StructureIndex)
    assert isinstance(FakeProjectionWriter(), ProjectionWriter)


def test_requests_and_refs_are_immutable():
    ref = DocumentRef(storage_provider="local", object_key="a/b.txt", logical_uri="local://a/b.txt")
    request = ProjectionRequest(
        projection_type="markdown",
        relative_path="project/a.md",
        content="# A",
        media_type="text/markdown",
    )
    index_request = StructureIndexRequest(
        document_version_id="dv-1",
        content_hash="abc",
        source_path="/tmp/a.pdf",
        media_type="application/pdf",
    )

    assert ref.object_key == "a/b.txt"
    assert request.projection_type == "markdown"
    assert index_request.document_version_id == "dv-1"
