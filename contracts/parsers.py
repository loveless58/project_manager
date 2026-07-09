"""Parsers — 字段解析器。

数据来源：基于 28 个项目样本反向抽取的真实格式。
设计原则：渐进式重构（风险 3）——先支持 80% 常见格式，剩余 20% 返回 None。

历史：
- 2026-07-08 创建。
- 之前 data_cleaning_tools.py 没有显式解析器，硬编码 regex 在 extract_pdf 里。
- 本模块把所有字段解析逻辑集中，渐进替换原 hardcode。
"""

import re
from typing import Optional, Tuple

# ========================================================================
# Amount 解析（5+ 种格式 → 标准化金额 + 标签）
# ========================================================================
# 真实数据样本（28 项目）：
#   "100万元" / "126万元" / "2,000,000 元" / "20万元" / "500万元" /
#   "4,000,000 元（预算）" / "元" / "待确认" / 空
#
# 设计：
#   返回 (number, label) — number 是数字（单位统一为元），label 是
#   预算/概算/报价等场景标签（无标签则 None）。
#   解析失败返回 (None, None)，不抛异常（按"不依赖做硬阻断"）。

# 中文万/亿 → 元 的换算
_UNIT_MULTIPLIERS = {
    "万": 10_000,
    "亿": 100_000_000,
}

# 数字正则：阿拉伯数字 + 可选千分位 + 可选小数
_NUM_RE = r"([\d,]+(?:\.\d+)?)"

# 标签正则：括号内的场景标签（"预算"/"概算"/"报价"等）
_LABEL_RE = r"[（(]([^）)]+)[）)]"

# 金额解析规则（按优先级）
_AMOUNT_PATTERNS = [
    # 1. "数字 + 单位 + 元 + 标签"  如 "4,000,000 元（预算）"
    (re.compile(rf"^{_NUM_RE}\s*(万|亿)?\s*元\s*{_LABEL_RE}?"), "arabic_yuan"),
    # 2. "数字 + 万"（无元）       如 "100万元" / "500万元"
    (re.compile(rf"^{_NUM_RE}\s*万\s*元?$"), "wan"),
    # 3. "数字 + 亿"（无元）       如 "1.5亿元"
    (re.compile(rf"^{_NUM_RE}\s*亿\s*元?$"), "yi"),
    # 4. 仅数字 + 元（无单位）     如 "2000000元"
    (re.compile(rf"^{_NUM_RE}\s*元$"), "yuan_only"),
]


def parse_amount(raw: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """解析项目金额。

    Args:
        raw: 原始字符串，如 "100万元" / "4,000,000 元（预算）"

    Returns:
        (number_in_yuan, label) — number 是元为单位的数字，label 是
        场景标签（"预算"/"概算"/"报价" 等），无标签则 None。
        解析失败返回 (None, None)。

    Examples:
        >>> parse_amount("100万元")
        (1000000.0, None)
        >>> parse_amount("4,000,000 元（预算）")
        (4000000.0, '预算')
        >>> parse_amount("待确认")
        (None, None)
        >>> parse_amount("")
        (None, None)
    """
    if not raw or not raw.strip():
        return None, None

    text = raw.strip()
    # 空值/占位符
    if text in ("待确认", "待录入", "待补充", "—", "-", "元"):
        return None, None

    # 提取标签（"预算"/"概算"/"报价" 等）
    label_match = re.search(_LABEL_RE, text)
    label = label_match.group(1).strip() if label_match else None

    # 提取数字（去掉千分位逗号）
    num_match = re.search(_NUM_RE, text)
    if not num_match:
        return None, label

    try:
        num_str = num_match.group(1).replace(",", "")
        num = float(num_str)
    except (ValueError, AttributeError):
        return None, label

    # 单位换算
    if "亿" in text:
        num *= _UNIT_MULTIPLIERS["亿"]
    elif "万" in text:
        num *= _UNIT_MULTIPLIERS["万"]

    return num, label


# ========================================================================
# Sales Person 解析（6 种写法 → 标准化姓名列表）
# ========================================================================
# 真实数据样本（28 项目）：
#   "邹迅" / "陈丞" / "高应山" / "—" / "待确认" /
#   "赵月伟（联系人） / 田晓光（销售总监）"
#
# 设计：
#   返回 List[str] — 销售姓名列表（多人按顺序）。
#   空值返回 []，不抛异常。

# 单人姓名（中文 2-4 字）
_NAME_RE = r"[\u4e00-\u9fff]{2,4}"

# 职级后缀（联系人/销售总监/销售经理等）
_ROLE_SUFFIX_RE = r"[（(][^)）]*[）)]"

# 销售解析规则
_SALES_PATTERNS = [
    # 1. 双人带职级："姓名A（职级A）/ 姓名B（职级B）"  或 " / " 分隔
    re.compile(rf"({_NAME_RE})\s*{_ROLE_SUFFIX_RE}\s*[/／]\s*({_NAME_RE})\s*{_ROLE_SUFFIX_RE}?"),
    # 2. 双人无职级："姓名A / 姓名B"
    re.compile(rf"({_NAME_RE})\s*[/／]\s*({_NAME_RE})"),
    # 3. 单人："姓名"
    re.compile(rf"^({_NAME_RE})$"),
]


def parse_sales_person(raw: Optional[str]) -> list:
    """解析负责销售字段。

    Args:
        raw: 原始字符串，如 "邹迅" / "赵月伟（联系人） / 田晓光（销售总监）"

    Returns:
        List[str] — 销售姓名列表（多人按顺序）。
        空值/占位符返回 []。

    Examples:
        >>> parse_sales_person("邹迅")
        ['邹迅']
        >>> parse_sales_person("赵月伟（联系人） / 田晓光（销售总监）")
        ['赵月伟', '田晓光']
        >>> parse_sales_person("待确认")
        []
        >>> parse_sales_person("—")
        []
    """
    if not raw or not raw.strip():
        return []

    text = raw.strip()

    # 占位符
    if text in ("待确认", "待录入", "待补充", "—", "-", ""):
        return []

    # 1. 双人带职级
    m = _SALES_PATTERNS[0].search(text)
    if m:
        return [m.group(1), m.group(2)]

    # 2. 双人无职级
    m = _SALES_PATTERNS[1].search(text)
    if m:
        return [m.group(1), m.group(2)]

    # 3. 单人
    m = _SALES_PATTERNS[2].match(text)
    if m:
        return [m.group(1)]

    # 4. 兜底：去除职级后取前 2-4 字中文
    cleaned = re.sub(_ROLE_SUFFIX_RE, "", text).strip()
    cleaned = re.sub(r"[/／]", "", cleaned).strip()
    if re.match(rf"^{_NAME_RE}$", cleaned):
        return [cleaned]

    return []


# ========================================================================
# Deadline 提取（项目记录.md 时间节点 + 里程碑管理）
# ========================================================================
# 真实数据样本（项目记录.md 多种位置）：
#   "投标截止/开标：2026-05-26 09:30"   ← 基本信息
#   "开标时间：2026-06-18 09:30"          ← 基本信息
#   "报名截止：2026-05-28"                ← 基本信息
#   "| 投标截止/开标 | 2026-07-15 |"     ← 里程碑管理
#
# 设计：
#   输入整个 markdown 文本，返回按 deadline 类型分组的结果。
#   不抛异常，匹配失败字段为 None。

# 时间格式（兼容 ISO 和中文）
_ISO_DATE_RE = r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)"
# 注：{0,2} 在 f-string 里会被解析成表达式分隔符，所以用 {{0,2}} 转义
_ISO_DATETIME_RE = (
    rf"{_ISO_DATE_RE}\s*\d{{0,2}}:?\d{{0,2}}?"
)

# Deadline 类型 + 对应关键词
# 设计：兼容多种写法
#   "投标截止：2026-06-11"           ← 基本信息
#   "投标截止/开标：2026-06-11 09:30" ← 基本信息（真实数据样本）
#   "| 投标截止/开标 | 2026-07-15 |"  ← 里程碑管理
#   "开标时间：2026-06-18 09:30"      ← 基本信息（仅开标时间）

_DEADLINE_PATTERNS = {
    "registration_deadline": [
        re.compile(rf"报名截止[：:]\s*{_ISO_DATE_RE}"),
        re.compile(rf"报名截止\s*[|｜]\s*{_ISO_DATE_RE}"),
    ],
    "bid_deadline": [
        # 模式说明：投标截止后允许任意非冒号字符（兼容 markdown 加粗 ** 等标记）
        re.compile(rf"投标截止[^：:|｜]*[：:]\s*{_ISO_DATETIME_RE}"),
        # "| 投标截止... | 日期 |"  里程碑表格（兼容 markdown 加粗）
        re.compile(rf"投标截止[^：:|｜]*[|｜]\s*{_ISO_DATE_RE}"),
        # "开标时间：日期 时间"  只有开标时间
        re.compile(rf"开标时间[：:]\s*{_ISO_DATETIME_RE}"),
    ],
    "bid_open_time": [
        re.compile(rf"开标时间[：:]\s*{_ISO_DATETIME_RE}"),
        re.compile(rf"开标\s*[|｜]\s*{_ISO_DATE_RE}"),
        # "投标截止/开标：日期 时间"  真实数据样本（与 bid_deadline 共享日期）
        re.compile(rf"投标截止[^：:|｜]*开标[^：:|｜]*[：:]\s*{_ISO_DATETIME_RE}"),
    ],
}


def normalize_date(date_str: str) -> Optional[str]:
    """标准化日期格式 → YYYY-MM-DD。

    支持：2026-05-26 / 2026/05/26 / 2026年5月26日 / 2026年05月26日
    """
    if not date_str:
        return None

    # ISO 格式
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 中文格式
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return None


def extract_deadlines(markdown_text: str) -> dict:
    """从项目记录.md 文本提取 deadline 字段。

    Args:
        markdown_text: 项目记录.md 全文

    Returns:
        {
            "registration_deadline": "2026-05-28" or None,
            "bid_deadline": "2026-05-26" or None,
            "bid_open_time": "2026-05-26" or None,  # 仅日期部分
        }
        找不到的字段为 None。

    Examples:
        >>> text = "## 时间节点\\n- 报名截止：2026-05-28\\n- 投标截止：2026-06-11\\n"
        >>> sorted(extract_deadlines(text).items())
        [('bid_deadline', '2026-06-11'), ('bid_open_time', None), ('registration_deadline', '2026-05-28')]
    """
    if not markdown_text:
        return {key: None for key in _DEADLINE_PATTERNS}

    result = {}
    for field, patterns in _DEADLINE_PATTERNS.items():
        result[field] = None
        for pattern in patterns:
            m = pattern.search(markdown_text)
            if m:
                # 取第一个日期组
                date_str = None
                for g in m.groups():
                    if g and re.match(r"\d{4}", g):
                        date_str = g
                        break
                if date_str:
                    result[field] = normalize_date(date_str)
                    break

    return result
