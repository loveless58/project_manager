import hashlib
from pathlib import Path

from skills.file_organizer.document_parse import DocumentParseSkill


class _ForbiddenOcr:
    def extract(self, path):
        raise AssertionError("native parse should finish before OCR")


class _SuccessfulOcr:
    def extract(self, path):
        return {
            "status": "success",
            "provider": "mineru",
            "text": "项目编号：PRJ-001\n扫描合同正文",
            "pages": [{"page": 1, "text": "扫描合同正文", "confidence": 0.9}],
            "attempts": [{"provider": "rapidocr", "status": "blocked"}],
        }


SOURCE_REF = {
    "binding_id": "incoming",
    "logical_uri": "business://incoming/contract.docx",
    "storage_provider": "local",
    "object_key": "contract.docx",
}


def test_parse_skill_prefers_native_parser_and_preserves_source_reference(tmp_path):
    source = tmp_path / "contract.docx"
    source.write_bytes(b"native document")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    native_calls = []

    def native_parser(path):
        native_calls.append(Path(path).name)
        return {
            "status": "success",
            "parser": "native_docx",
            "text": "合同编号：CT-001\n项目编号：PRJ-001",
            "pages": [],
            "tables": [],
        }

    result = DocumentParseSkill(native_parser=native_parser, ocr_chain=_ForbiddenOcr()).parse(
        str(source), source_ref=SOURCE_REF
    )

    assert native_calls == ["contract.docx"]
    assert result["status"] == "success"
    assert result["parser"] == "native_docx"
    assert result["content_hash"] == expected_hash
    assert result["source_ref"] == SOURCE_REF
    assert result["fields"]["project_code"] == "PRJ-001"


def test_parse_skill_uses_declared_ocr_chain_when_native_parse_is_blocked(tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"image only pdf")

    result = DocumentParseSkill(
        native_parser=lambda path: {"status": "blocked", "reason": "NATIVE.NO_TEXT"},
        ocr_chain=_SuccessfulOcr(),
    ).parse(str(source), source_ref={**SOURCE_REF, "object_key": "scan.pdf"})

    assert result["status"] == "success"
    assert result["parser"] == "mineru"
    assert result["fields"]["project_code"] == "PRJ-001"
    assert result["pages"][0]["page"] == 1


def test_parse_skill_returns_stable_review_result_when_no_parser_can_extract_text(tmp_path):
    source = tmp_path / "empty.pdf"
    source.write_bytes(b"empty")

    result = DocumentParseSkill(
        native_parser=lambda path: {"status": "blocked", "reason": "NATIVE.NO_TEXT"},
        ocr_chain=lambda: None,
    ).parse(str(source), source_ref={**SOURCE_REF, "object_key": "empty.pdf"})

    assert result["status"] == "needs_review"
    assert result["reason"] == "OCR.PROVIDERS_UNAVAILABLE"
