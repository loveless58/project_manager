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
