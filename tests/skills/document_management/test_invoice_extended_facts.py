from __future__ import annotations

from skills.document_management.document_facts.skill import DocumentFactsSkill


def test_invoice_skill_extracts_tax_ids_amount_breakdown_rate_and_remarks_with_evidence() -> None:
    text = """增值税电子普通发票
发票号码：26112000002732686171
购方名称：北京华胜天成科技股份有限公司
购方税号：91110000123456789A
销方名称：普华和诚（北京）信息有限公司
销方税号：91110108123456789B
金额：45283.02
税额：2716.98
税率：6%
价税合计（小写）：¥48000.00
备注：软件外包服务
"""
    document = {
        "schema_version": "structured_document.v1", "status": "success", "text": text,
        "source_ref": {"binding_id": "source", "logical_uri": "business://source/invoice.pdf", "storage_provider": "local", "object_key": "invoice.pdf"},
        "content_hash": "a" * 64, "media_type": "application/pdf", "parser": "native_pdf",
        "pages": [{"page": 1, "text": text, "confidence": None}], "tables": [], "fields": [], "attempts": [],
    }

    facts = DocumentFactsSkill().extract(document)

    assert facts["invoice_type"]["value"] == "增值税电子普通发票"
    assert facts["buyer_tax_id"]["value"] == "91110000123456789A"
    assert facts["seller_tax_id"]["value"] == "91110108123456789B"
    assert facts["untaxed_amount"]["value"] == "45283.02"
    assert facts["tax_amount"]["value"] == "2716.98"
    assert facts["tax_rate"]["value"] == "6%"
    assert facts["remarks"]["value"] == "软件外包服务"
    assert facts["tax_amount"]["evidence"] == [{"page": 1, "text": "2716.98"}]
