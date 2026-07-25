from platform_core.models import (
    BusinessContextQuery,
    CapabilityReport,
    StructureIndexResult,
)
from services.retrieval_service import RetrievalService


class StaticContext:
    def __init__(self, records):
        self.records = records

    def search(self, query):
        return list(self.records)


class RecordingIndex:
    name = "recording"

    def __init__(self):
        self.requests = []

    def probe(self):
        return CapabilityReport("ready", self.name, "test-v1", "ready")

    def index(self, request):
        self.requests.append(request)
        return StructureIndexResult("success", self.name, "recording://1", (), "", "")


def test_no_candidate_means_no_pageindex():
    index = RecordingIndex()
    query = BusinessContextQuery("发票", {"buyer": {"tax_id": "9131"}}, ())

    result = RetrievalService(StaticContext([]), index).find_business_candidates(query)

    assert result.status == "needs_review"
    assert index.requests == []


def test_matching_contract_is_first_and_only_its_declared_long_document_is_indexed(tmp_path):
    index = RecordingIndex()
    long_document = tmp_path / "contract.pdf"
    short_document = tmp_path / "invoice.pdf"
    query = BusinessContextQuery(
        "发票",
        {
            "buyer": {"tax_id": "91310001", "name": "合成甲方有限公司"},
            "contract_code": "SYN-CONTRACT-001",
        },
        (),
    )
    candidate = {
        "id": "contract-001",
        "document_type": "合同",
        "parties": {
            "buyer": {"tax_id": "91310001", "name": "合成甲方有限公司"},
            "seller": {"tax_id": "91320002", "name": "合成乙方有限公司"},
        },
        "facts": {"contract_code": "SYN-CONTRACT-001"},
        "documents": [
            {
                "path": str(long_document),
                "document_version_id": "contract-v1",
                "content_hash": "a" * 64,
                "media_type": "application/pdf",
                "page_count": 30,
            },
            {
                "path": str(short_document),
                "document_version_id": "invoice-v1",
                "content_hash": "b" * 64,
                "media_type": "application/pdf",
                "page_count": 1,
            },
        ],
    }

    result = RetrievalService(StaticContext([candidate]), index).find_business_candidates(query)

    assert result.status == "matched"
    assert result.candidates[0]["id"] == "contract-001"
    assert result.evidence_refs[0]["field"] == "buyer.tax_id"
    assert [request.document_version_id for request in index.requests] == ["contract-v1"]
    assert index.requests[0].source_path == str(long_document)


def test_invoice_line_items_do_not_create_project_match_or_index_request(tmp_path):
    index = RecordingIndex()
    query = BusinessContextQuery(
        "发票",
        {"line_items": [{"item_name": "项目 S-001"}]},
        (),
    )
    candidate = {
        "id": "contract-001",
        "document_type": "合同",
        "parties": {"buyer": {}, "seller": {}},
        "facts": {"project_code": "S-001"},
        "documents": [
            {
                "path": str(tmp_path / "contract.pdf"),
                "document_version_id": "contract-v1",
                "content_hash": "a" * 64,
                "media_type": "application/pdf",
                "page_count": 30,
            }
        ],
    }

    result = RetrievalService(StaticContext([candidate]), index).find_business_candidates(query)

    assert result.status == "needs_review"
    assert result.candidates == ()
    assert index.requests == []


def test_path_hint_alone_needs_review_and_never_indexes(tmp_path):
    index = RecordingIndex()
    candidate = {
        "id": "candidate-1",
        "parties": {"buyer": {}, "seller": {}},
        "facts": {},
        "path_hints": ["contracts/acme"],
        "documents": [{
            "path": str(tmp_path / "contract.pdf"),
            "document_version_id": "contract-v1",
            "content_hash": "a" * 64,
            "media_type": "application/pdf",
            "page_count": 30,
        }],
    }

    result = RetrievalService(
        StaticContext([candidate]), index
    ).find_business_candidates(
        BusinessContextQuery("invoice", {"path_hint": "contracts/acme"}, ())
    )

    assert result.status == "needs_review"
    assert result.candidates == ()
    assert index.requests == []


def test_duplicate_document_identity_is_indexed_once_at_service_boundary(tmp_path):
    index = RecordingIndex()
    document = {
        "path": str(tmp_path / "contract.pdf"),
        "document_version_id": "shared-version-v1",
        "content_hash": "a" * 64,
        "media_type": "application/pdf",
        "page_count": 30,
    }
    candidates = [
        {
            "id": "candidate-1",
            "parties": {"buyer": {"tax_id": "91310001"}, "seller": {}},
            "facts": {},
            "documents": [document],
        },
        {
            "id": "candidate-2",
            "parties": {"buyer": {"tax_id": "91310001"}, "seller": {}},
            "facts": {},
            "documents": [dict(document)],
        },
    ]

    result = RetrievalService(
        StaticContext(candidates), index
    ).find_business_candidates(
        BusinessContextQuery("invoice", {"buyer": {"tax_id": "91310001"}}, ())
    )

    assert result.status == "matched"
    assert [request.document_version_id for request in index.requests] == [
        "shared-version-v1"
    ]


def test_tax_id_conflict_requires_review_and_never_indexes(tmp_path):
    index = RecordingIndex()
    candidate = {
        "id": "candidate-1",
        "parties": {
            "buyer": {"name": "Acme Limited", "tax_id": "91310001"},
            "seller": {},
        },
        "facts": {},
        "documents": [{
            "path": str(tmp_path / "contract.pdf"),
            "document_version_id": "contract-v1",
            "content_hash": "a" * 64,
            "media_type": "application/pdf",
            "page_count": 30,
        }],
    }

    result = RetrievalService(
        StaticContext([candidate]), index
    ).find_business_candidates(
        BusinessContextQuery(
            "invoice",
            {"buyer": {"name": "Acme Limited", "tax_id": "99990000"}},
            (),
        )
    )

    assert result.status == "needs_review"
    assert result.candidates == ()
    assert result.conflicts[0]["field"] == "buyer.tax_id"
    assert result.conflicts[0]["code"] == "BUSINESS_CONTEXT.CONFLICT"
    assert "91310001" not in str(result.conflicts)
    assert "99990000" not in str(result.conflicts)
    assert index.requests == []


def test_blocked_structure_index_is_diagnostic_not_evidence(tmp_path):
    class BlockedIndex(RecordingIndex):
        def index(self, request):
            self.requests.append(request)
            return StructureIndexResult(
                "blocked",
                "recording",
                "",
                (),
                "INDEX.PROVIDER_UNAVAILABLE",
                "hidden provider detail",
            )

    index = BlockedIndex()
    candidate = {
        "id": "candidate-1",
        "parties": {"buyer": {"tax_id": "91310001"}, "seller": {}},
        "facts": {},
        "documents": [{
            "path": str(tmp_path / "contract.pdf"),
            "document_version_id": "contract-v1",
            "content_hash": "a" * 64,
            "media_type": "application/pdf",
            "page_count": 30,
        }],
    }

    result = RetrievalService(
        StaticContext([candidate]), index
    ).find_business_candidates(
        BusinessContextQuery("invoice", {"buyer": {"tax_id": "91310001"}}, ())
    )

    assert not any(ref.get("kind") == "structure_index" for ref in result.evidence_refs)
    assert result.diagnostics[-1] == {
        "code": "BUSINESS_CONTEXT.STRUCTURE_INDEX_FAILED",
        "candidate_id": "candidate-1",
        "provider": "recording",
        "status": "blocked",
        "error_code": "INDEX.PROVIDER_UNAVAILABLE",
    }


def test_unrelated_tax_id_mismatch_does_not_block_a_real_candidate(tmp_path):
    index = RecordingIndex()
    document = {
        "path": str(tmp_path / "contract.pdf"),
        "document_version_id": "contract-v1",
        "content_hash": "a" * 64,
        "media_type": "application/pdf",
        "page_count": 30,
    }
    candidates = [
        {
            "id": "real-candidate",
            "parties": {"buyer": {"tax_id": "91310001"}, "seller": {}},
            "facts": {},
            "documents": [document],
        },
        {
            "id": "unrelated-candidate",
            "parties": {"buyer": {"tax_id": "99990000"}, "seller": {}},
            "facts": {},
            "documents": [dict(document, document_version_id="unrelated-v1")],
        },
    ]

    result = RetrievalService(
        StaticContext(candidates), index
    ).find_business_candidates(
        BusinessContextQuery("invoice", {"buyer": {"tax_id": "91310001"}}, ())
    )

    assert result.status == "matched"
    assert [candidate["id"] for candidate in result.candidates] == ["real-candidate"]
    assert result.conflicts == ()
    assert [request.document_version_id for request in index.requests] == ["contract-v1"]


def test_invalid_document_before_valid_same_version_does_not_shadow_valid_identity(tmp_path):
    index = RecordingIndex()
    documents = [
        {
            "path": str(tmp_path / "invalid.pdf"),
            "document_version_id": "contract-v1",
            "content_hash": "not-a-sha256",
            "media_type": "application/pdf",
            "page_count": 30,
        },
        {
            "path": str(tmp_path / "valid.pdf"),
            "document_version_id": "contract-v1",
            "content_hash": "a" * 64,
            "media_type": "application/pdf",
            "page_count": 30,
        },
    ]
    candidate = {
        "id": "candidate-1",
        "parties": {"buyer": {"tax_id": "91310001"}, "seller": {}},
        "facts": {},
        "documents": documents,
    }

    result = RetrievalService(
        StaticContext([candidate]), index
    ).find_business_candidates(
        BusinessContextQuery("invoice", {"buyer": {"tax_id": "91310001"}}, ())
    )

    assert [request.content_hash for request in index.requests] == ["a" * 64]
    assert result.diagnostics[-1]["code"] == "BUSINESS_CONTEXT.DOCUMENT_INVALID"


def test_conflicting_valid_identity_for_same_version_is_not_indexed(tmp_path):
    index = RecordingIndex()
    candidate = {
        "id": "candidate-1",
        "parties": {"buyer": {"tax_id": "91310001"}, "seller": {}},
        "facts": {},
        "documents": [
            {
                "path": str(tmp_path / "first.pdf"),
                "document_version_id": "contract-v1",
                "content_hash": "a" * 64,
                "media_type": "application/pdf",
                "page_count": 30,
            },
            {
                "path": str(tmp_path / "second.pdf"),
                "document_version_id": "contract-v1",
                "content_hash": "b" * 64,
                "media_type": "application/pdf",
                "page_count": 30,
            },
        ],
    }

    result = RetrievalService(
        StaticContext([candidate]), index
    ).find_business_candidates(
        BusinessContextQuery("invoice", {"buyer": {"tax_id": "91310001"}}, ())
    )

    assert result.status == "matched"
    assert index.requests == []
    assert result.diagnostics[-1]["code"] == "BUSINESS_CONTEXT.DOCUMENT_IDENTITY_CONFLICT"
