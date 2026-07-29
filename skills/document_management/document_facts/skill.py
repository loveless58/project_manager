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
_INVOICE_TYPE = re.compile(r"((?:增值税)?(?:电子|数电)?(?:普通|专用)?发票)")
_TAX_ID = {
    "buyer_tax_id": re.compile(r"(?:购方|购买方|购货方)\s*(?:税号|纳税人识别号)\s*[：:]\s*([A-Za-z0-9]{8,32})"),
    "seller_tax_id": re.compile(r"(?:销方|销售方|销货方)\s*(?:税号|纳税人识别号)\s*[：:]\s*([A-Za-z0-9]{8,32})"),
}
_UNTAXED = re.compile(r"(?:不含税金额|金额)\s*[：:]?\s*(?:[¥￥]|CNY)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
_TAX_AMOUNT = re.compile(r"税额\s*[：:]?\s*(?:[¥￥]|CNY)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
_TAX_RATE = re.compile(r"税率\s*[：:]?\s*([0-9]+(?:\.[0-9]+)?%)")
_REMARKS = re.compile(r"备注\s*[：:]?\s*([^\n\r]+)")


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
        invoice_number = _value(_INVOICE_NUMBER, normalized) or _first_invoice_number(normalized)
        currency_values = _currency_values(normalized)
        total = _value(_TOTAL, normalized) or (currency_values[-1] if currency_values else None)
        if not _INVOICE_MARKER.search(normalized) or not (invoice_number or total):
            return _unknown()

        pages = structured_document.get("pages")
        facts: dict[str, dict[str, Any]] = {
            "document_type": _fact("invoice", 0.99, "发票", pages),
        }
        invoice_type = _value(_INVOICE_TYPE, normalized)
        if invoice_type:
            facts["invoice_type"] = _fact(invoice_type, 0.95, invoice_type, pages)
        if invoice_number:
            facts["invoice_number"] = _fact(invoice_number, 0.99, invoice_number, pages)
        issue_date = _date(normalized)
        if issue_date:
            facts["issue_date"] = _fact(issue_date, 0.95, issue_date.replace("-", "年", 1).replace("-", "月", 1) + "日", pages)
        for name in ("buyer_name", "seller_name"):
            party = _party(name, normalized)
            if party:
                facts[name] = _fact(party, 0.95, party, pages)
        for name, pattern in _TAX_ID.items():
            tax_id = _value(pattern, normalized)
            if tax_id:
                facts[name] = _fact(tax_id, 0.95, tax_id, pages)
        for name, pattern in (("untaxed_amount", _UNTAXED), ("tax_amount", _TAX_AMOUNT)):
            raw_amount = _value(pattern, normalized)
            amount = _amount(raw_amount) if raw_amount else None
            if amount is not None:
                facts[name] = _fact(amount, 0.95, raw_amount, pages)
        tax_rate = _value(_TAX_RATE, normalized) or _detached_tax_rate(normalized)
        if tax_rate:
            facts["tax_rate"] = _fact(tax_rate, 0.9, tax_rate, pages)
        remarks = _value(_REMARKS, normalized)
        if remarks:
            facts["remarks"] = _fact(remarks, 0.85, remarks, pages)
        if total:
            amount = _amount(total)
            if amount is not None:
                facts["total_amount"] = _fact(amount, 0.95, total, pages)
        service = _service(normalized)
        if service:
            facts["service_description"] = _fact(service, 0.9, service, pages)
        _apply_detached_layout_fallback(facts, normalized, pages, currency_values)
        return facts



def _first_invoice_number(text: str) -> str | None:
    match = re.search(r"(?<![A-Za-z0-9])([0-9]{18,20})(?![A-Za-z0-9])", text)
    return match.group(1) if match else None

def _currency_values(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"[¥￥]\s*([0-9][0-9,]*(?:\.\d{1,2})?)", text)]

def _apply_detached_layout_fallback(facts: dict[str, dict[str, Any]], text: str, pages: object, values: list[str]) -> None:
    companies = re.findall(r"([^\n\r]*?(?:股份有限公司|有限公司|公司))", text)
    tax_ids = re.findall(r"(?<![A-Za-z0-9])([0-9A-Z]{18})(?![A-Za-z0-9])", text)
    if len(companies) >= 2:
        for name, value in (("seller_name", _clean(companies[0])), ("buyer_name", _clean(companies[1]))): facts.setdefault(name, _fact(value, 0.75, value, pages))
    if len(tax_ids) >= 2:
        for name, value in (("seller_tax_id", tax_ids[0]), ("buyer_tax_id", tax_ids[1])): facts.setdefault(name, _fact(value, 0.75, value, pages))
    if len(values) >= 3:
        for name, raw in (("untaxed_amount", values[0]), ("tax_amount", values[1]), ("total_amount", values[-1])):
            amount = _amount(raw)
            if amount is not None: facts.setdefault(name, _fact(amount, 0.75, raw, pages))
    match = re.search(r"\*[^*\n]+\*([^\n]+)\n\s*([^\n]+)", text)
    if match and "service_description" not in facts:
        value = _clean(match.group(1) + match.group(2))
        facts["service_description"] = _fact(value, 0.75, value, pages)


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
    return None if value in {"规格型号", "单 位", "数量", "单价", "金额", "税率/征收率"} else (value or None)


def _detached_tax_rate(text: str) -> str | None:
    values = re.findall(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?%)(?![0-9])", text)
    return values[-1] if values else None


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
