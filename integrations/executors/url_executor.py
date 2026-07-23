"""URL 网页内容提取执行器。接口契约暂未实现。

TODO(实现路径):
1. 选型:`requests` + `beautifulsoup4` + `html2text` / `trafilatura`
2. 装包:`pip install beautifulsoup4 html2text`
3. 实现 fetch(url) + parse(html) + extract(url) 三层
4. timeout 30s + 重试 2 次 + 反爬 fallback(User-Agent)
5. 动态页(JS 渲染)需要 playwright/selenium(超出本 skill 范围,留后续)

调用方会收到:
  status=blocked, implementation_status=stub
不抛异常
"""

from typing import Any, Dict, List

from integrations.executors.base import ParseExecutor


class UrlExecutor(ParseExecutor):
    """URL 网页内容提取执行器(接口契约暂未实现)。"""

    name = "url"

    def can_handle(self, source: str) -> bool:
        return self._is_url(source)

    def extract(self, source: str) -> Dict[str, Any]:
        """暂未实现。返回 status=blocked + executor_not_implemented。"""
        # 先做 URL 合法性校验(这是已实现的部分,即使 stub 也做)
        try:
            self._validate_url(source)
        except ValueError as e:
            return self._blocked(str(e), implementation_status="stub")

        return self._blocked(
            "UrlExecutor not implemented yet. TODO: "
            "1) pip install beautifulsoup4 html2text; "
            "2) implement fetch(url) with timeout=30s + retry=2 + User-Agent; "
            "3) implement parse(html) -> title + text + links; "
            "4) implement extract(url) = fetch + parse + clean. "
            "See integrations/executors/url_executor.py docstring.",
            implementation_status="stub",
        )

    def get_supported_types(self) -> List[str]:
        return [".html", ".htm", "http://", "https://"]
