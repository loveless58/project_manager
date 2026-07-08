"""OCR Aliases — OCR 提取层 → 账本层 字段名映射。

数据来源：基于工作台 2 个真实 extracted.json 反向抽取。
设计原则：渐进式重构（风险 2）——不替换原 hardcode，只声明映射规则，
执行由 data_cleaning_tools.py 后续集成。

历史：
- 2026-07-08 创建。
- OCR 提取层（extracted_fields）字段：buyer / seller / date / amount /
  project_no / payment_purpose / penalty_rate / service_rate。
- 账本层（项目记录.md / index.json）字段：customer / bid_deadline / amount /
  bid_code / project_code / 等。
- 字段名完全不同，必须有显式映射。
"""

from typing import Dict, List, Optional

# ========================================================================
# 文档子类型（按真实数据：居间协议 / 标书费回单 / 合同 / 招标公告 / ...）
# ========================================================================

DOCUMENT_SUBTYPES = {
    "居间协议",        # 工作台样本 1：8% 服务费率
    "标书费回单",      # 工作台样本 2：800 元凭证
    "合作协议",
    "商务综合服务协议",
    "合同",
    "招标公告",
    "中标公告",
    "变更公告",
    "投标文件",
    "报名材料",
    "招标文件",
}

# ========================================================================
# OCR 字段 → 账本字段 的基础映射（不考虑文档子类型）
# ========================================================================

OCR_TO_LEDGER_BASE: Dict[str, str] = {
    # 主体类
    "buyer": "customer",       # 合同/协议的甲方
    "seller": "customer",      # 合同/协议的乙方（次选）
    "payer": "customer",       # 凭证类的付款人
    "payee": "customer",       # 凭证类的收款人（次选）

    # 金额类
    "amount": "amount",
    "bid_bond_amount": "bid_fee",  # 保证金 → 标书费（业务上等价）
    "service_rate": "service_rate",
    "penalty_rate": "penalty_rate",

    # 日期类（按文档子类型区分 → 见 ALIASES_BY_SUBTYPE）
    "date": "_date_",          # 占位，需按子类型路由

    # 编号类
    "project_no": "_project_no_",  # 占位，按阶段路由
}

# ========================================================================
# 按文档子类型的细分映射
# ========================================================================
# date 字段在不同文档里有不同含义：
#   - 居间协议/合同：合同签署日期 → ledger 没有专门字段，存入 document_date
#   - 标书费回单：付款日期 → 不存 ledger
#   - 招标公告：发布/截止日期 → bid_deadline
# project_no 字段在不同阶段不同：
#   - 投标阶段：招标编号 → bid_code
#   - 执行阶段：项目编号 → project_code
#   - 凭证类：业务参考号 → business_reference_no

ALIASES_BY_SUBTYPE: Dict[str, Dict[str, str]] = {
    "居间协议": {
        "date": "document_date",          # 协议签署日期
        "project_no": "bid_code",          # 协议编号 → 招标编号
    },
    "合作协议": {
        "date": "document_date",
        "project_no": "bid_code",
    },
    "商务综合服务协议": {
        "date": "document_date",
        "project_no": "bid_code",
    },
    "标书费回单": {
        "date": "payment_date",            # 凭证类特有
        "project_no": "business_reference_no",  # 业务参考号（不直接对应账本）
        "amount": "bid_fee",               # 凭证金额 → 标书费
    },
    "合同": {
        "date": "contract_date",
        "project_no": "project_code",       # 合同阶段用项目编号
    },
    "招标公告": {
        "date": "bid_deadline",            # 公告中的截止日期
        "project_no": "bid_code",
    },
    "中标公告": {
        "date": "bid_announcement_date",
        "project_no": "bid_code",
    },
    "投标文件": {
        "date": "bid_deadline",
        "project_no": "bid_code",
    },
    "招标文件": {
        "date": "bid_deadline",
        "project_no": "bid_code",
    },
    "报名材料": {
        "date": "registration_deadline",
        "project_no": "bid_code",
    },
    "变更公告": {
        "date": "document_date",
    },
}

# ========================================================================
# 公开 API
# ========================================================================

def resolve_ocr_field(
    ocr_field: str,
    document_subtype: Optional[str] = None,
) -> Optional[str]:
    """把 OCR 字段名解析为账本字段名。

    Args:
        ocr_field: OCR 提取的字段名，如 "buyer" / "date" / "project_no"
        document_subtype: 文档子类型，如 "居间协议" / "标书费回单"
                         （来自 document_type 字段）

    Returns:
        账本字段名（zh 或 en 形式由调用方决定）。
        无法映射返回 None（不抛异常）。

    Examples:
        >>> resolve_ocr_field("buyer")
        'customer'
        >>> resolve_ocr_field("date", "招标公告")
        'bid_deadline'
        >>> resolve_ocr_field("date", "标书费回单")
        'payment_date'
        >>> resolve_ocr_field("unknown_field")
        'unknown_field'
    """
    # 1. 优先查子类型专属映射
    if document_subtype and document_subtype in ALIASES_BY_SUBTYPE:
        sub_aliases = ALIASES_BY_SUBTYPE[document_subtype]
        if ocr_field in sub_aliases:
            return sub_aliases[ocr_field]

    # 2. 查基础映射
    if ocr_field in OCR_TO_LEDGER_BASE:
        target = OCR_TO_LEDGER_BASE[ocr_field]
        # 占位符（_date_ / _project_no_）表示按子类型路由但无默认
        if not target.startswith("_"):
            return target

    # 3. 兜底：保持原字段名（让上层决定如何处理）
    return ocr_field


def list_mappable_fields(document_subtype: Optional[str] = None) -> List[str]:
    """列出所有可映射的 OCR 字段名。

    Args:
        document_subtype: 文档子类型（可选，用于筛选子类型特有字段）

    Returns:
        OCR 字段名列表
    """
    fields = list(OCR_TO_LEDGER_BASE.keys())
    if document_subtype and document_subtype in ALIASES_BY_SUBTYPE:
        for field in ALIASES_BY_SUBTYPE[document_subtype]:
            if field not in fields:
                fields.append(field)
    return fields
