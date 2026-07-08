"""Ledger Namespace — 账本字段契约。

数据来源：/Users/zhang/Desktop/工作文件/项目文件/index.json（28 个项目真实样本）。
设计原则：数据驱动（不是凭空设计），反映真实业务用法。

历史：
- 2026-07-08 创建。从 28 个项目样本反向抽取字段命名约定。
- 之前 contracts/fields.py 用 4 字段硬必填（project_name/budget/customer/deadline）
  和真实业务脱节：customer 实际只 32% 填，bid_deadline 0% 填。
- 本模块不强制必填规则——只声明字段命名空间 + 填充率 + 来源。

和 contracts/fields.py 的关系：
- fields.py：保留原 4 字段硬必填规则（reviewer 在用）
- ledger_namespace.py：本模块，账本字段元数据（业务层用）
- 两者并存，渐进式重构，不强制替换。
"""

# ========================================================================
# 阶段命名（权威 = 项目丢标，按用户拍板）
# ========================================================================
# 历史：index.json 早期用 "项目归档"，实际目录用 "项目丢标"，governance
# contract 写 "项目归档"，data_cleaning_tools.py 关键词用 "项目丢标"。
# 用户拍板：以"项目丢标"为权威。
# 索引重生成时会用本命名。

PHASE_LABELS = ["项目投标", "项目执行", "项目丢标"]

# Legacy alias（渐进兼容，旧 index.json 仍可能用"项目归档"）
LEGACY_PHASE_ALIASES = {
    "项目归档": "项目丢标",
}

# ========================================================================
# 账本字段元数据（基于 28 个项目样本）
# ========================================================================
# 字段结构：
#   zh：项目记录.md 实际用的中文名（也是账本字段名）
#   en：英文代码（系统内部用）
#   fill_rate：28 项目中的填充率（"高"/"中"/"低"/"散落"/"未填"）
#   source：信息主要来源位置
#   note：备注（数据驱动的发现）

LEDGER_FIELDS = {
    "project_name": {
        "zh": "项目名称",
        "fill_rate": "高",
        "source": "项目记录.md 基本信息",
        "note": "所有项目必填，隐含 100%。",
    },
    "project_type": {
        "zh": "项目类型",
        "fill_rate": "高",
        "fill_rate_pct": 79,
        "source": "项目记录.md 基本信息 / index.json",
        "note": "产品/服务/设备/系统集成等枚举。填充率最高的非名称字段。",
    },
    "sales_person": {
        "zh": "负责销售",
        "fill_rate": "中",
        "fill_rate_pct": 43,
        "source": "index.json sales_person / 项目记录.md 基本信息",
        "note": "6 种写法：单姓名 / 双人带职级 / 占位符('—'/'待确认')。"
        "硬校验需规范化。",
    },
    "crm_id": {
        "zh": "CRM 编号",
        "fill_rate": "中",
        "fill_rate_pct": 36,
        "source": "index.json crm_id / 项目记录.md 基本信息",
        "note": "格式 'C000028186' 或 '待录入'。CRM 流转前常空。",
    },
    "customer": {
        "zh": "招标人/客户",
        "fill_rate": "低",
        "fill_rate_pct": 32,
        "source": "index.json customer / 项目记录.md 基本信息",
        "note": "早期招标阶段常未确定客户，76% 投标项目此字段空。"
        "不能作为硬必填。",
    },
    "amount": {
        "zh": "项目金额",
        "fill_rate": "低",
        "fill_rate_pct": 25,
        "source": "index.json amount / 项目记录.md 基本信息",
        "note": "8 种格式：'100万元' / '2,000,000 元' / '4,000,000 元（预算）' "
        "/'500万元' / '20万元' / '元' / '待确认' / 空。"
        "必须用模糊解析不能用单一正则。",
    },
    "bid_deadline": {
        "zh": "投标截止",
        "fill_rate": "散落",
        "fill_rate_pct": 0,
        "source": "项目记录.md 时间节点（不在 index.json）",
        "note": "index.json 0% 填，但项目记录.md 大量项目有。"
        "来源切换到项目记录.md。",
    },
    # === 投标阶段特有 ===
    "business_type": {
        "zh": "业务类型",
        "fill_rate": "中",
        "source": "项目记录.md 基本信息",
        "note": "公开招标 / 邀请招标 / 询价 / 单一来源 等。",
    },
    "bid_fee": {
        "zh": "标书费",
        "fill_rate": "中",
        "source": "项目记录.md 基本信息 / 标书费回单",
        "note": "投标阶段独有字段，所有投标项目都涉及。",
    },
    "registration_deadline": {
        "zh": "报名截止",
        "fill_rate": "中",
        "source": "项目记录.md 时间节点",
        "note": "早于投标截止，常和报名状态配合判断。",
    },
    "bid_open_time": {
        "zh": "开标时间",
        "fill_rate": "中",
        "source": "项目记录.md 时间节点",
        "note": "和投标截止可能同一天，可能不同。",
    },
    "registration_status": {
        "zh": "报名状态",
        "fill_rate": "中",
        "source": "项目记录.md 项目状态",
        "note": "枚举：待报名 / 已报名 / 已过。",
    },
    "bid_status": {
        "zh": "中标状态",
        "fill_rate": "中",
        "source": "项目记录.md 项目状态 / index.json",
        "note": "枚举：待开标 / 已中标 / 未中标 / 弃标。",
    },
    "contract_status": {
        "zh": "签约状态",
        "fill_rate": "中",
        "source": "项目记录.md 项目状态",
        "note": "枚举：未签约 / 已签 / 已过。",
    },
    "phase": {
        "zh": "当前阶段",
        "fill_rate": "高",
        "source": "项目记录.md 项目状态 / index.json phase",
        "note": "枚举：项目投标 / 项目执行 / 项目丢标（权威）。",
    },
    # === 执行阶段特有 ===
    "project_code": {
        "zh": "项目编号",
        "fill_rate": "执行高",
        "source": "项目记录.md 时间节点（中标后才有）",
        "note": "和招标编号(bid_code)不同——招标编号投标阶段用，项目编号中标后用。",
    },
    "approval_status": {
        "zh": "审批状态",
        "fill_rate": "执行高",
        "source": "项目记录.md 基本信息",
        "note": "执行阶段独有，投标阶段无此字段。",
    },
    "opportunity_status": {
        "zh": "商机状态",
        "fill_rate": "执行高",
        "source": "项目记录.md 基本信息",
        "note": "执行阶段独有。",
    },
    "expected_signing": {
        "zh": "预计签约",
        "fill_rate": "执行中",
        "source": "项目记录.md 基本信息",
        "note": "执行阶段独有。",
    },
    "bid_announcement_date": {
        "zh": "中标公示",
        "fill_rate": "执行中",
        "source": "项目记录.md 时间节点",
        "note": "执行阶段独有——项目从投标转执行的时点。",
    },
    # === 丢标阶段特有 ===
    "conclusion_report": {
        "zh": "结项报告",
        "fill_rate": "丢标高",
        "source": "项目记录.md 交付物清单",
        "note": "丢标/归档阶段独有。",
    },
    "financial_settlement": {
        "zh": "财务结算",
        "fill_rate": "丢标高",
        "source": "项目记录.md 里程碑管理",
        "note": "丢标/归档阶段独有。",
    },
}

# ========================================================================
# 字段别名映射（处理多写法）
# ========================================================================
# 用户在 index.json 用了不同写法，本表统一映射到标准 zh 字段名。

FIELD_ALIASES = {
    "sales_person": {
        "邹迅": "邹迅",
        "陈丞": "陈丞",
        "高应山": "高应山",
        "—": None,  # 空值
        "待确认": None,  # 占位
        "赵月伟（联系人） / 田晓光（销售总监）": "赵月伟;田晓光",  # 双人拆分
    },
    # 注：完整别名表由 contracts/parsers.py 处理（parsers.py 用正则做）
}


def get_field_meta(en_name: str) -> dict:
    """获取账本字段元数据。"""
    return LEDGER_FIELDS.get(en_name, {})


def get_zh_name(en_name: str) -> str:
    """获取字段中文名（账本字段名）。"""
    meta = LEDGER_FIELDS.get(en_name, {})
    return meta.get("zh", en_name)


def get_en_name(zh_name: str) -> str:
    """反向查找：从中文名找英文代码。"""
    for en, meta in LEDGER_FIELDS.items():
        if meta.get("zh") == zh_name:
            return en
    return zh_name
