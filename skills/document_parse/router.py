"""解析路由 — 判断 source 类型,选 executor。

URL 优先判断(其他 executor 不接 URL),文件路径按扩展名匹配。
"""

import os
import re
from typing import List, Optional, Tuple

from integrations.executors.base import ParseExecutor
from integrations.executors import (
    DocxExecutor,
    XlsxExecutor,
    UrlExecutor,
    WpsExecutor,
    PdfExecutor,
)


def build_default_executors() -> List[ParseExecutor]:
    """构造默认 executor 列表(按优先级排序)。"""
    return [
        DocxExecutor(),
        XlsxExecutor(),
        WpsExecutor(),
        PdfExecutor(),
        UrlExecutor(),  # URL 最后兜底(因为 URL 路径可能含 .docx 等扩展名)
    ]


def route(
    source: str,
    executors: Optional[List[ParseExecutor]] = None,
) -> Tuple[ParseExecutor, str]:
    """根据 source 选 executor。

    Args:
        source: file_path 或 url

    Returns:
        (executor, file_type) — file_type 是 ".docx" / ".xlsx" / ".wps" / ".pdf" / "url"

    Raises:
        NoExecutorError: 没有 executor 能处理这个 source
    """
    if executors is None:
        executors = build_default_executors()

    # URL 路径优先(URL 可能含 .docx 等扩展名,但 UrlExecutor 专门处理 http/https 前缀)
    if re.match(r"^https?://", source):
        for ex in executors:
            if ex.name == "url":
                return ex, "url"

    # 文件路径:按扩展名 + can_handle 匹配
    file_type = os.path.splitext(source)[1].lower()
    for ex in executors:
        if ex.can_handle(source):
            return ex, file_type

    raise NoExecutorError(
        f"No executor can handle source: {source} "
        f"(supported types: {[t for ex in executors for t in ex.get_supported_types()]})"
    )


class NoExecutorError(Exception):
    """没有 executor 能处理这个 source。"""
    pass
