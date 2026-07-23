"""ParseExecutor 抽象基类。所有 executor 必须实现 can_handle / extract / get_supported_types。

设计原则:
- executor 不 import business_rules / business_knowledge(纯技术)
- executor 不抛 NotImplementedError 给 skill(自己在内部处理,返回 status=blocked)
- 标准输出格式:
  {
    "status": "success | blocked | needs_review",
    "raw_data": {"paragraphs", "tables", "metadata", "raw_text"},
    "error": "..." | None,
    "implementation_status": "implemented | stub"
  }
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List
import os
import re
from urllib.parse import urlparse


class ParseExecutor(ABC):
    """解析执行器抽象基类。"""

    name: str = ""  # 子类必须设置,如 "docx"

    @abstractmethod
    def can_handle(self, source: str) -> bool:
        """判断是否处理这个 source(file_path 或 url)。"""
        ...

    @abstractmethod
    def extract(self, source: str) -> Dict[str, Any]:
        """提取原始数据,返回标准格式(见模块 docstring)。"""
        ...

    @abstractmethod
    def get_supported_types(self) -> List[str]:
        """返回支持的扩展名列表 ['.docx'] 或 ['.html'] 或 ['.wps']。"""
        ...

    # ---- helpers (子类可复用) ----

    def _check_source_exists(self, source: str) -> None:
        """检查 source 文件是否存在。"""
        if not os.path.exists(source):
            raise FileNotFoundError(f"Source not found: {source}")

    @staticmethod
    def _is_url(source: str) -> bool:
        """判断是否是 URL(http/https)。"""
        return bool(re.match(r"^https?://", source))

    @staticmethod
    def _validate_url(url: str) -> None:
        """校验 URL 合法性(http/https only)。"""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"URL must be http/https: {url}")
        if not parsed.netloc:
            raise ValueError(f"URL missing hostname: {url}")

    @staticmethod
    def _blocked(error: str, implementation_status: str = "implemented") -> Dict[str, Any]:
        """构造标准 blocked 响应。"""
        return {
            "status": "blocked",
            "raw_data": {},
            "error": error,
            "implementation_status": implementation_status,
        }
