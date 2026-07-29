from dataclasses import dataclass

from infrastructure.file_organizer.repository import FileOrganizationRepository
from skills.file_organizer.business_query import BusinessQuerySkill


@dataclass
class _Probe:
    status: str


class _ReadyIndex:
    name = "pageindex"

    def probe(self):
        return _Probe("ready")

    def index(self, request):
        return type(
            "Result",
            (),
            {
                "status": "success",
                "external_ref": "pageindex://test-index",
                "structure": ({"title": "第三章 付款条款", "page_start": 3, "page_end": 4},),
                "error_code": "",
            },
        )()


class _BlockedIndex:
    name = "pageindex"

    def probe(self):
        return _Probe("blocked")


def _document(*, text="付款条款见第三章", project_code="PRJ-001"):
    return {
        "schema_version": "structured_document.v1",
        "source_ref": {
            "binding_id": "incoming",
            "logical_uri": "business://incoming/contract.md",
            "storage_provider": "local",
            "object_key": "contract.md",
        },
        "content_hash": "a" * 64,
        "media_type": "text/markdown",
        "status": "success",
        "parser": "native_markdown",
        "text": text,
        "pages": [],
        "tables": [],
        "fields": {"project_code": project_code},
    }


def test_business_query_proposes_project_code_match_and_pageindex_evidence(database_path):
    with database_path.write_uow() as connection:
        repository = FileOrganizationRepository(connection)
        project_id = repository.create_project("项目 A", "PRJ-001")
        proposal = BusinessQuerySkill(
            repository, _ReadyIndex(), pageindex_min_text_length=1
        ).propose(_document(), goal="按项目整理", source_path="contract.md")

    assert proposal["status"] == "needs_confirmation"
    assert proposal["candidate_project"] == {
        "id": project_id,
        "name": "项目 A",
        "project_code": "PRJ-001",
    }
    assert proposal["pageindex_evidence"][0]["page"] == 3


def test_business_query_degrades_when_pageindex_is_unavailable(database_path):
    with database_path.write_uow() as connection:
        repository = FileOrganizationRepository(connection)
        proposal = BusinessQuerySkill(
            repository, _BlockedIndex(), pageindex_min_text_length=1
        ).propose(_document(project_code=""), goal="按项目整理", source_path="contract.md")

    assert proposal["status"] == "needs_review"
    assert proposal["pageindex_evidence"] == []
    assert "PAGEINDEX.UNAVAILABLE" in proposal["reasons"]
