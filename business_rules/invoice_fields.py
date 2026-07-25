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
    rows = _invoice_rows(text)
    number = _label_value(rows, ("发票号码", "发票号"))
    if number:
        fields["invoice_number"] = number

    invoice_date = _normalize_date(_label_value(rows, ("开票日期", "发票日期")))
    if invoice_date:
        fields["invoice_date"] = invoice_date

    buyer = _party_from_rows(rows, ("购买方", "收票方"))
    if buyer:
        fields["buyer"] = buyer
    seller = _party_from_rows(rows, ("销售方",))
    if seller:
        fields["seller"] = seller

    untaxed_amount = _money_from_rows(rows, ("不含税金额", "金额", "合计金额"))
    if untaxed_amount:
        fields["untaxed_amount"] = untaxed_amount
    tax_amount = _money_from_rows(rows, ("税额",))
    if tax_amount:
        fields["tax_amount"] = tax_amount
    total_amount = _money_from_rows(rows, ("价税合计（小写）", "价税合计(小写)", "价税合计", "合计金额（小写）", "合计金额(小写)"))
    if total_amount:
        fields["total_amount"] = total_amount

    line_items = _line_items_from_rows(rows)
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

def _invoice_rows(text: str) -> List[List[str]]:
    rows: List[List[str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cells = [cell.strip() for cell in line.split("|") if cell.strip()]
        if "|" not in line:
            cells = [line]
        rows.append(cells)
    return rows


def _label_value(rows: List[List[str]], labels: tuple[str, ...]) -> str:
    for row_index, row in enumerate(rows):
        for cell_index, cell in enumerate(row):
            for label in labels:
                if not cell.startswith(label):
                    continue
                suffix = cell[len(label):].strip()
                if suffix.startswith((":", "：")):
                    return suffix[1:].strip()
                if suffix:
                    continue
                if cell_index + 1 < len(row):
                    return row[cell_index + 1].strip()
                if row_index + 1 < len(rows) and rows[row_index + 1]:
                    return rows[row_index + 1][0].strip()
    return ""


def _party_from_rows(rows: List[List[str]], roles: tuple[str, ...]) -> Dict[str, str]:
    party: Dict[str, str] = {}
    name_labels = tuple(f"{role}名称" for role in roles)
    tax_labels = tuple(f"{role}{suffix}" for role in roles for suffix in ("统一社会信用代码", "纳税人识别号", "税号"))
    name = _label_value(rows, name_labels)
    tax_id = _label_value(rows, tax_labels)
    if name:
        party["name"] = name
    if tax_id:
        party["tax_id"] = re.sub(r"[ \t]+", "", tax_id)
    return party

def _money_from_rows(rows: List[List[str]], labels: tuple[str, ...]) -> str:
    value = _label_value(rows, labels)
    match = re.search(r"[0-9][0-9,]*(?:\.\d{1,2})?", value)
    if not match:
        return ""
    normalized = match.group(0).replace(",", "")
    if "." not in normalized:
        return f"{normalized}.00"
    integer, decimal = normalized.split(".", 1)
    return f"{integer}.{decimal[:2].ljust(2, '0')}"


def _line_items_from_rows(rows: List[List[str]]) -> List[Dict[str, str]]:
    for header_index, raw_header in enumerate(rows):
        header = _expanded_cells(raw_header)
        if "项目名称" not in header:
            continue
        item_index = header.index("项目名称")
        specification_index = header.index("规格型号") if "规格型号" in header else None
        items: List[Dict[str, str]] = []
        for raw_row in rows[header_index + 1:]:
            cells = _expanded_cells(raw_row)
            if not cells or _separator_row(cells):
                continue
            if any(marker in cells[0] for marker in ("合计", "价税", "税额")):
                break
            if item_index >= len(cells) or cells[item_index] in {"项目名称", "名称"}:
                continue
            item = {"item_name": cells[item_index]}
            if specification_index is not None and specification_index < len(cells) and cells[specification_index]:
                item["specification"] = cells[specification_index]
            items.append(item)
        return items
    return []


def _expanded_cells(cells: List[str]) -> List[str]:
    return cells if len(cells) > 1 else [part for part in cells[0].split() if part]


def _separator_row(cells: List[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r"[-:： ]+", cell) for cell in cells)
