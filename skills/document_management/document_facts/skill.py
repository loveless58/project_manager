"""Deterministic, evidence-carrying facts extraction for parsed documents."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping


_INVOICE_MARKER = re.compile(r"(?:电子|数电|增值税)?\s*发票")
_INVOICE_NUMBER = re.compile(r"(?:发票\s*(?:号码|号)|票\s*号)\s*[：:]?\s*([0-9]{8,24})")
_ISSUE_DATE = re.compile(r"(?:开票日期|日期)\s*[：:]?\s*(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})")
_TOTAL = re.compile(r"(?:价税合计\s*[（(]?小写[）)]?|价税合计|合计)\s*[：:]?\s*(?:[¥￥]|CNY)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
_DIRECT_PARTY = {
    "buyer_name": re.compile(r"(?:购方|购买方|购货方)\s*(?:名称)?\s*[：:]\s*([^\n\r]+)"),
    "seller_name": re.compile(r"(?:销方|销售方|销货方)\s*(?:名称)?\s*[：:]\s*([^\n\r]+)"),
}
_SECTION_PARTY = {
    "buyer_name": re.compile(r"(?:购买方|购方|购货方)\s*信息[\s\S]{0,240}?名称\s*[：:]\s*([^\n\r]+)"),
    "seller_name": re.compile(r"(?:销售方|销方|销货方)\s*信息[\s\S]{0,240}?名称\s*[：:]\s*([^\n\r]+)"),
}
_SERVICE = re.compile(r"(?:项目名称|货物或应税劳务、服务名称)\s*[：:]?\s*([^\n\r]+)")


class DocumentFactsSkill:
    """Extract bounded invoice facts only after a parser produced document text.

    The Skill returns candidate facts with page evidence; it never infers a
    project, selects an archive target, or treats a file name as evidence.
    """

    def extract(self, structured_document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        """Return invoice facts when independent invoice signals coexist."""
        if not isinstance(structured_document, Mapping):
            return _unknown()
        text = structured_document.get("text")
        if structured_document.get("status") != "success" or not isinstance(text, str):
            return _unknown()
        normalized = _normalize(text)
        invoice_number = _value(_INVOICE_NUMBER, normalized)
        total = _value(_TOTAL, normalized)
        if not _INVOICE_MARKER.search(normalized) or not (invoice_number or total):
            return _unknown()

        pages = structured_document.get("pages")
        facts: dict[str, dict[str, Any]] = {
            "document_type": _fact("invoice", 0.99, "发票", pages),
        }
        if invoice_number:
            facts["invoice_number"] = _fact(invoice_number, 0.99, invoice_number, pages)
        issue_date = _date(normalized)
        if issue_date:
            facts["issue_date"] = _fact(issue_date, 0.95, issue_date.replace("-", "年", 1).replace("-", "月", 1) + "日", pages)
        for name in ("buyer_name", "seller_name"):
            party = _party(name, normalized)
            if party:
                facts[name] = _fact(party, 0.95, party, pages)
        if total:
            amount = _amount(total)
            if amount is not None:
                facts["total_amount"] = _fact(amount, 0.95, total, pages)
        service = _service(normalized)
        if service:
            facts["service_description"] = _fact(service, 0.9, service, pages)
        return facts


def _unknown() -> dict[str, dict[str, Any]]:
    return {"document_type": {"value": "unknown", "confidence": 0.0, "evidence": []}}


def _normalize(text: str) -> str:
    return text.replace("\u3000", " ").replace("\r\n", "\n").replace("\r", "\n")


def _value(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return _clean(match.group(1)) if match else None


def _date(text: str) -> str | None:
    match = _ISSUE_DATE.search(text)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _party(name: str, text: str) -> str | None:
    for pattern in (_DIRECT_PARTY[name], _SECTION_PARTY[name]):
        match = pattern.search(text)
        if match:
            value = _clean(match.group(1))
            if value:
                return value
    return None


def _service(text: str) -> str | None:
    match = _SERVICE.search(text)
    if not match:
        return None
    value = _clean(match.group(1))
    if "*" in value:
        value = _clean(value.split("*")[-1])
    return value or None


def _amount(value: str) -> str | None:
    try:
        amount = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None
    return f"{amount:.2f}" if amount >= 0 else None


def _clean(value: str) -> str:
    return " ".join(value.strip().strip("：:").split())


def _fact(value: object, confidence: float, evidence_text: str, pages: object) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "evidence": _evidence(evidence_text, pages),
    }


def _evidence(value: str, pages: object) -> list[dict[str, Any]]:
    if not isinstance(pages, list):
        return []
    for page in pages:
        if not isinstance(page, Mapping):
            continue
        page_number, text = page.get("page"), page.get("text")
        if isinstance(page_number, int) and isinstance(text, str) and value in text:
            return [{"page": page_number, "text": value}]
    return []


__all__ = ["DocumentFactsSkill"]
