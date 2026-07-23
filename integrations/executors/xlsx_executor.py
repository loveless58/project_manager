"""Xlsx (.xlsx) 解析执行器。纯技术,无业务规则。

输出 raw_data 包含:
- paragraphs: 每行作为段落
- tables: 所有 sheet 的 rows 展平
- sheets: 保留 sheet 层级 [{name, rows}]
- metadata: {sheet_count, sheet_names, file_size}
- raw_text: 所有行(每行用 | 分隔单元格)
"""

import os
from typing import Any, Dict, List

from integrations.executors.base import ParseExecutor


class XlsxExecutor(ParseExecutor):
    """Excel (.xlsx) 解析器。"""

    name = "xlsx"

    def can_handle(self, source: str) -> bool:
        if self._is_url(source):
            return False
        return source.lower().endswith(".xlsx")

    def extract(self, source: str) -> Dict[str, Any]:
        try:
            self._check_source_exists(source)
        except FileNotFoundError as e:
            return self._blocked(str(e))

        try:
            import openpyxl  # type: ignore
        except ImportError:
            return self._blocked(
                "openpyxl not installed. Run: pip install openpyxl"
            )

        try:
            wb = openpyxl.load_workbook(source, data_only=True)

            sheets = []
            all_text_lines: List[str] = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows: List[List[str]] = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) if c is not None else "" for c in row]
                    if any(c.strip() for c in cells):
                        rows.append(cells)
                        all_text_lines.append(" | ".join(cells))
                if rows:
                    sheets.append({"name": sheet_name, "rows": rows})

            metadata = {
                "sheet_count": len(wb.sheetnames),
                "sheet_names": list(wb.sheetnames),
                "file_size": os.path.getsize(source),
            }

            raw_text = "\n".join(all_text_lines)

            return {
                "status": "success",
                "raw_data": {
                    "paragraphs": all_text_lines,  # 每行作为段落
                    "tables": [s["rows"] for s in sheets],  # 展平
                    "sheets": sheets,  # 保留 sheet 层级
                    "metadata": metadata,
                    "raw_text": raw_text,
                },
                "error": None,
                "implementation_status": "implemented",
            }
        except Exception as e:
            return self._blocked(f"xlsx parse error: {type(e).__name__}: {e}")

    def get_supported_types(self) -> List[str]:
        return [".xlsx"]
