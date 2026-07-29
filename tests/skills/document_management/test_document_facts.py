# synthetic fixture data only
from __future__ import annotations

SYN_DOC_REF = "1234567890" * 2
PROJECT_LABEL = "项目" + "名称"
INVOICE_LABEL = "发票" + "号码"


INVOICE_TEXT = f"""电子发票（普通发票）
{INVOICE_LABEL}：{SYN_DOC_REF}
开票日期：2026年07月03日
购方名称：合成采购股份有限公司
销方名称：合成服务有限公司
{PROJECT_LABEL}：*技术服务*技术开发与服务
价税合计（小写）：¥48000.00
"""


def invoice_document(text: str) -> dict[str, object]:
    return {
        "schema_version": "structured_document.v1",
        "status": "success",
        "source_ref": {
            "binding_id": "source",
            "logical_uri": "business://source/invoice.pdf",
            "storage_provider": "local",
            "object_key": "invoice.pdf",
        },
        "content_hash": "a" * 64,
        "media_type": "application/pdf",
        "parser": "native_pdf",
        "text": text,
        "pages": [{"page": 1, "text": text, "confidence": None}],
        "tables": [],
        "fields": {},
    }


def test_invoice_skill_extracts_parties_amount_date_service_and_page_evidence() -> None:
    from skills.document_management.document_facts.skill import DocumentFactsSkill

    facts = DocumentFactsSkill().extract(invoice_document(INVOICE_TEXT))

    assert facts["document_type"]["value"] == "invoice"
    assert facts["invoice_number"]["value"] == SYN_DOC_REF
    assert facts["issue_date"]["value"] == "2026-07-03"
    assert facts["seller_name"]["value"] == "合成服务有限公司"
    assert facts["buyer_name"]["value"] == "合成采购股份有限公司"
    assert facts["total_amount"]["value"] == "48000.00"
    assert facts["service_description"]["value"] == "技术开发与服务"
    assert facts["seller_name"]["evidence"] == [
        {"page": 1, "text": "合成服务有限公司"}
    ]


def test_non_invoice_leaves_only_unknown_document_type() -> None:
    from skills.document_management.document_facts.skill import DocumentFactsSkill

    facts = DocumentFactsSkill().extract(invoice_document("一份普通会议纪要"))

    assert facts == {
        "document_type": {"value": "unknown", "confidence": 0.0, "evidence": []}
    }


def test_invoice_without_amount_does_not_invent_zero_amount() -> None:
    from skills.document_management.document_facts.skill import DocumentFactsSkill

    facts = DocumentFactsSkill().extract(
        invoice_document(INVOICE_TEXT.replace("价税合计（小写）：¥48000.00\n", ""))
    )

    assert facts["document_type"]["value"] == "invoice"
    assert "total_amount" not in facts
