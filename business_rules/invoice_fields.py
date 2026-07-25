"""Deterministic, invoice-only field extraction.

The invoice column called ``项目名称`` names goods or services.  It is kept as
``line_items[].item_name`` and is intentionally never interpreted as a
project-management project name.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from contracts.invoice_schema import INVOICE_ALLOWED_FIELDS


def extract_invoice_fields(text: str) -> Dict[str, Any]:
    """Extract counterparty, amounts, and line items from invoice text only."""
    fields: Dict[str, Any] = {}
    invoice_number = _first_value(text, r"(?:发票号码|发票号)\s*[:：]\s*([^\n]+)")
    if invoice_number:
        fields["invoice_number"] = invoice_number

    invoice_date = _normalize_date(_first_value(text, r"(?:开票日期|发票日期)\s*[:：]\s*([^\n]+)"))
    if invoice_date:
        fields["invoice_date"] = invoice_date

    buyer = _party(text, "购买方|收票方")
    if buyer:
        fields["buyer"] = buyer
    seller = _party(text, "销售方")
    if seller:
        fields["seller"] = seller

    untaxed_amount = _money_value(text, r"(?:不含税金额|金额|合计金额)")
    if untaxed_amount:
        fields["untaxed_amount"] = untaxed_amount
    tax_amount = _money_value(text, r"税额")
    if tax_amount:
        fields["tax_amount"] = tax_amount
    total_amount = _money_value(text, r"(?:价税合计(?:（小写）|\(小写\))?|合计金额(?:（小写）|\(小写\))?)")
    if total_amount:
        fields["total_amount"] = total_amount

    line_items = _line_items(text)
    if line_items:
        fields["line_items"] = line_items

    return {key: value for key, value in fields.items() if key in INVOICE_ALLOWED_FIELDS}


def _first_value(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _party(text: str, role_pattern: str) -> Dict[str, str]:
    party: Dict[str, str] = {}
    name = _first_value(text, rf"(?:{role_pattern})(?:名称)?\s*[:：]\s*([^\n]+)")
    tax_id = _first_value(
        text,
        rf"(?:{role_pattern})(?:统一社会信用代码|纳税人识别号|税号)\s*[:：]\s*([^\n]+)",
    )
    if name:
        party["name"] = name
    if tax_id:
        party["tax_id"] = re.sub(r"\s+", "", tax_id)
    return party


def _money_value(text: str, label_pattern: str) -> str:
    value = _first_value(text, rf"(?:{label_pattern})\s*[:：]\s*[￥¥]?\s*([0-9][0-9,]*(?:\.\d{{1,2}})?)")
    if not value:
        return ""
    normalized = value.replace(",", "")
    if "." not in normalized:
        return f"{normalized}.00"
    integer, decimal = normalized.split(".", 1)
    return f"{integer}.{decimal[:2].ljust(2, '0')}"


def _normalize_date(value: str) -> str:
    match = re.search(r"(\d{4})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})", value)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _line_items(text: str) -> List[Dict[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if "项目名称" not in line:
            continue
        if index + 1 >= len(lines):
            return []
        cells = [cell.strip() for cell in re.split(r"\s*\|\s*|\s+", lines[index + 1]) if cell.strip()]
        if not cells:
            return []
        item: Dict[str, str] = {"item_name": cells[0]}
        if len(cells) > 1:
            item["specification"] = cells[1]
        return [item]
    return []
