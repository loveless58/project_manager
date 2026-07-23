"""WPS (.wps) 文件解析执行器。接口契约暂未实现。

WPS 文件是国产 Office 套件的私有格式,有几种解析路径(待你拍板选型):

A. antiword:只能读 .doc,不能读 .wps(legacy 二进制格式,大概率不行)
B. wps2text / LibreOffice headless 转 docx 后读:需要装 LibreOffice(体积大,~500MB)
   路径:`libreoffice --headless --convert-to docx file.wps` → DocxExecutor().read()
C. WPS 官方 python SDK:确认是否有(可能需要企业版授权)
D. olefile + 手工解析 .wps 二进制结构:复杂,易错,不推荐

决策需要的信息:
- 你的工作目录里有没有 .wps 文件?先跑样本看哪种工具能读
- WPS 是高频需求还是边缘场景?决定值得不值得花这个工程量

调用方会收到:
  status=blocked, implementation_status=stub
不抛异常
"""

import os
from typing import Any, Dict, List

from integrations.executors.base import ParseExecutor


class WpsExecutor(ParseExecutor):
    """WPS (.wps) 文件解析执行器(接口契约暂未实现)。"""

    name = "wps"

    def can_handle(self, source: str) -> bool:
        if self._is_url(source):
            return False
        return source.lower().endswith(".wps")

    def extract(self, source: str) -> Dict[str, Any]:
        """暂未实现。返回 status=blocked + executor_not_implemented。"""
        # 文件存在性检查(即使 stub 也做)
        try:
            self._check_source_exists(source)
        except FileNotFoundError as e:
            return self._blocked(str(e), implementation_status="stub")

        return self._blocked(
            "WpsExecutor not implemented yet. TODO (pick one): "
            "A) antiword (only reads .doc, NOT .wps); "
            "B) LibreOffice headless convert .wps -> .docx, then DocxExecutor (RECOMMENDED, ~500MB); "
            "C) official WPS python SDK (check availability); "
            "D) olefile + manual parse (complex, not recommended). "
            "Decision needed: do you have real .wps samples to test? high-frequency or edge case? "
            "See integrations/executors/wps_executor.py docstring.",
            implementation_status="stub",
        )

    def get_supported_types(self) -> List[str]:
        return [".wps"]
