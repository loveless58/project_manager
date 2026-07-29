# synthetic fixture data only
from __future__ import annotations

SYN_DOC_REF = "1234567890" * 2
INVOICE_LABEL = "发票" + "号码"

from skills.document_management.document_facts.skill import DocumentFactsSkill


def test_invoice_skill_handles_native_pdf_text_with_values_detached_from_labels() -> None:
    text = f"""电子发票（增值税专用发票）
{INVOICE_LABEL}：
开票日期：
购 买 方 信 息
统一社会信用代码/纳税人识别号：
销 售 方 信 息
统一社会信用代码/纳税人识别号：
名称：
名称：
{SYN_DOC_REF}
2026年07月03日
合成服务有限公司
913100000000000001
合成采购股份有限公司
913100000000000002
¥45283.02
¥2716.98
¥48000.00
*生产生活服务*技术开发
与服务
6%
"""
    doc = {"status": "success", "text": text, "pages": [{"page": 1, "text": text}]}

    facts = DocumentFactsSkill().extract(doc)

    assert facts["document_type"]["value"] == "invoice"
    assert facts["invoice_number"]["value"] == SYN_DOC_REF
    assert facts["seller_name"]["value"] == "合成服务有限公司"
    assert facts["buyer_name"]["value"] == "合成采购股份有限公司"
    assert facts["seller_tax_id"]["value"] == "913100000000000001"
    assert facts["buyer_tax_id"]["value"] == "913100000000000002"
    assert facts["total_amount"]["value"] == "48000.00"
    assert facts["untaxed_amount"]["value"] == "45283.02"
    assert facts["tax_amount"]["value"] == "2716.98"
    assert facts["service_description"]["value"] == "技术开发与服务"
