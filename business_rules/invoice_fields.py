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
        if "|" not in line:
            rows.append([line])
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if line.startswith("|"):
            cells = cells[1:]
        if line.endswith("|"):
            cells = cells[:-1]
        rows.append(cells)
    return rows


_ALL_INVOICE_LABELS = (
    "价税合计（小写）", "价税合计(小写)", "合计金额（小写）", "合计金额(小写)",
    "购买方统一社会信用代码", "购买方纳税人识别号", "收票方统一社会信用代码", "收票方纳税人识别号",
    "销售方统一社会信用代码", "销售方纳税人识别号", "购买方名称", "收票方名称", "销售方名称",
    "购买方税号", "收票方税号", "销售方税号", "不含税金额", "合计金额",
    "发票号码", "开票日期", "发票日期", "发票号", "项目名称", "规格型号", "价税合计", "税额", "金额",
)
_INVOICE_LABEL_PATTERN = re.compile(
    "|".join(re.escape(label) for label in sorted(_ALL_INVOICE_LABELS, key=len, reverse=True))
)


def _label_value(rows: List[List[str]], labels: tuple[str, ...]) -> str:
    for row_index, row in enumerate(rows):
        for cell_index, cell in enumerate(row):
            for segment in _inline_label_segments(cell):
                for label in labels:
                    if not segment.startswith(label):
                        continue
                    suffix = segment[len(label):].strip()
                    if suffix.startswith((":", "：")):
                        value = _truncate_at_next_label(suffix[1:].strip())
                    elif suffix:
                        continue
                    elif cell_index + 1 < len(row):
                        value = row[cell_index + 1].strip()
                    elif row_index + 1 < len(rows) and rows[row_index + 1]:
                        value = rows[row_index + 1][0].strip()
                    else:
                        value = ""
                    if _valid_label_value(label, value):
                        return value
    return ""


def _inline_label_segments(cell: str) -> List[str]:
    matches = list(_INVOICE_LABEL_PATTERN.finditer(cell))
    if not matches:
        return [cell]
    return [
        cell[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(cell)]
        for index, match in enumerate(matches)
    ]


def _truncate_at_next_label(value: str) -> str:
    boundaries = [
        match.start()
        for known_label in _ALL_INVOICE_LABELS
        for match in (re.search(re.escape(known_label), value),)
        if match and match.start() > 0
    ]
    return value[:min(boundaries)].strip() if boundaries else value.strip()


def _valid_label_value(label: str, value: str) -> bool:
    normalized = value.strip()
    if not normalized or _looks_like_invoice_label_or_table_row(normalized):
        return False
    if label in {"发票号码", "发票号"}:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", normalized))
    if label in {"开票日期", "发票日期"}:
        return bool(_normalize_date(normalized))
    return True


def _looks_like_invoice_label_or_table_row(value: str) -> bool:
    compact = value.strip()
    if compact.startswith(_ALL_INVOICE_LABELS):
        return True
    normalized = re.sub(r"\s+", "", compact)
    return (
        "项目名称" in normalized
        or "规格型号" in normalized
        or normalized.startswith(("合计", "价税", "税额"))
    )


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
        header_width = len(header)
        items: List[Dict[str, str]] = []
        for raw_row in rows[header_index + 1:]:
            cells = _expanded_cells(raw_row)
            if not cells or _separator_row(cells):
                continue
            if _total_row(cells):
                break
            cells = (cells + [""] * header_width)[:header_width]
            if cells[item_index] in {"", "项目名称", "名称"}:
                continue
            item = {"item_name": cells[item_index]}
            if specification_index is not None and cells[specification_index]:
                item["specification"] = cells[specification_index]
            items.append(item)
        return items
    return []


def _expanded_cells(cells: List[str]) -> List[str]:
    return cells if len(cells) > 1 else [part for part in cells[0].split() if part]


def _separator_row(cells: List[str]) -> bool:
    return bool(cells) and all(not cell or re.fullmatch(r"[-:： ]+", cell) for cell in cells)


def _total_row(cells: List[str]) -> bool:
    normalized = " ".join(cell.strip() for cell in cells if cell.strip())
    return normalized.startswith(("合计", "价税", "税额"))
