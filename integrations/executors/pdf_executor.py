"""PDF 文件解析执行器。接口契约暂未实现。

现状:
- 已有 data_cleaning_tools.extract_pdf(走 OCR 引擎链 Vision/RapidOCR/EasyOCR/Tesseract)
- 本 skill 暂未拆分,后续把 extract_pdf 的"提取结构化数据"部分迁到本 executor
- "OCR 引擎调度"留在 data_cleaning_tools(基础设施,不在本 executor 范围内)

TODO(迁移路径):
1. 看 data_cleaning_tools.extract_pdf 现有实现
2. 拆出"打开 PDF → 提取每页文本"这部分(纯技术,PyMuPDF / pdfplumber)到 PdfExecutor
3. "扫描页 → OCR 引擎链"留给 data_cleaning_tools(基础设施)
4. 业务字段抽取留给 skill 的 3-layer fallback

调用方会收到:
  status=blocked, implementation_status=stub
不抛异常
"""

import os
from typing import Any, Dict, List

from integrations.executors.base import ParseExecutor


class PdfExecutor(ParseExecutor):
    """PDF 文件解析执行器(接口契约暂未实现)。"""

    name = "pdf"

    def can_handle(self, source: str) -> bool:
        if self._is_url(source):
            return False
        return source.lower().endswith(".pdf")

    def extract(self, source: str) -> Dict[str, Any]:
        """暂未实现。返回 status=blocked + executor_not_implemented。"""
        # 文件存在性检查
        try:
            self._check_source_exists(source)
        except FileNotFoundError as e:
            return self._blocked(str(e), implementation_status="stub")

        return self._blocked(
            "PdfExecutor not implemented yet. TODO: "
            "1) Migrate data_cleaning_tools.extract_pdf's text extraction layer here; "
            "2) OCR fallback stays in data_cleaning_tools (infrastructure, not this executor); "
            "3) Business field extraction stays in skill 3-layer fallback. "
            "See integrations/executors/pdf_executor.py docstring.",
            implementation_status="stub",
        )

    def get_supported_types(self) -> List[str]:
        return [".pdf"]
