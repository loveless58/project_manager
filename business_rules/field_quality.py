import re
from typing import Any, Dict, Iterable, List, Tuple


CORE_BUSINESS_FIELDS = {
    "project_name",
    "customer_name",
    "customer",
    "sales_owner",
    "bid_status",
    "registration_status",
    "contract_status",
    "lifecycle_stage",
    "closed_reason_type",
    "project_code",
    "contract_code",
    "bpm_contract_code",
    "budget",
    "quoted_amount",
    "deadline",
    "registration_deadline",
    "bid_open_time",
    "document_date",
    "supplier_name",
}

STAGE_REQUIRED_FIELDS = {
    "closed": {"project_name", "bid_status", "lifecycle_stage", "closed_reason_type"},
    "closed_lost": {"project_name", "bid_status", "lifecycle_stage", "closed_reason_type"},
    "execution": {"project_name", "lifecycle_stage"},
    "executing": {"project_name", "lifecycle_stage"},
    "delivery": {"project_name", "lifecycle_stage"},
    "won_pending_contract": {"project_name", "bid_status"},
    "bidding": {"project_name", "registration_status", "bid_status"},
    "pending_registration": {"project_name", "registration_status"},
    "unknown": {"project_name"},
}

ORG_SUFFIXES = (
    "公司",
    "银行",
    "学院",
    "大学",
    "学校",
    "医院",
    "中心",
    "委员会",
    "局",
    "厅",
    "部",
    "院",
    "所",
    "集团",
    "政府",
    "办公室",
    "研究院",
)

NOISE_TOKENS = (
    "审核意见",
    "日期",
    "日      期",
    "联 系 人",
    "联系人",
    "联系电话",
    "电话",
    "邮箱",
    "地址",
    "签字",
    "盖章",
    "经办人",
    "制表",
    "复核",
    "备注",
)

TABLE_ROLE_TOKENS = (
    "建设单位",
    "监理单位",
    "施工单位",
    "设计单位",
    "供应商单位",
    "收款单位",
    "付款单位",
)

SENTENCE_TOKENS = (
    "确认为",
    "按照",
    "根据",
    "经甲方",
    "双方",
    "应当",
    "不得",
    "详见",
    "如下",
    "合同",
    "验收",
    "审核",
)


def required_fields_for_facts(facts: Dict[str, Any]) -> List[str]:
    stage = _infer_stage(facts)
    return sorted(STAGE_REQUIRED_FIELDS.get(stage, STAGE_REQUIRED_FIELDS["unknown"]))


def filter_business_facts(
    facts: Dict[str, Any],
    *,
    source_type: str = "",
    source_path: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    accepted: Dict[str, Any] = {}
    rejected: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []

    for field, value in facts.items():
        normalized = _normalize_value(value)
        if not normalized:
            rejected.append(_review_item(field, value, "empty_value"))
            continue
        if field not in CORE_BUSINESS_FIELDS:
            ignored.append(_review_item(field, value, "not_required_for_business_judgement"))
            continue

        status, reason, clean_value = _validate_field(field, normalized)
        if status == "accepted":
            accepted[field] = clean_value
            if field == "customer_name":
                accepted.setdefault("customer", clean_value)
            elif field == "customer":
                accepted.setdefault("customer_name", clean_value)
        else:
            rejected.append(_review_item(field, value, reason, clean_value))

    required = required_fields_for_facts(accepted)
    missing_required = [field for field in required if not accepted.get(field)]
    quality = {
        "schema_version": "business_field_quality.v1",
        "source_type": source_type,
        "source_path": source_path,
        "required_fields": required,
        "missing_required_fields": missing_required,
        "accepted_fields": sorted(accepted.keys()),
        "rejected_fields": rejected,
        "ignored_fields": ignored,
        "has_rejections": bool(rejected),
    }
    return accepted, quality


def invalid_ledger_field(field: str, value: Any) -> bool:
    if value is None:
        return False
    normalized = _normalize_value(value)
    if not normalized:
        return True
    status, _, _ = _validate_field(field, normalized)
    return status != "accepted"


def _infer_stage(facts: Dict[str, Any]) -> str:
    lifecycle_stage = str(facts.get("lifecycle_stage") or "")
    if lifecycle_stage:
        return lifecycle_stage
    bid_status = str(facts.get("bid_status") or "")
    if any(token in bid_status for token in ("弃标", "丢标", "未中标")):
        return "closed"
    if "已中标" in bid_status:
        return "won_pending_contract"
    registration_status = str(facts.get("registration_status") or "")
    if "待报名" in registration_status:
        return "pending_registration"
    if "已报名" in registration_status:
        return "bidding"
    return "unknown"


def _validate_field(field: str, value: str) -> Tuple[str, str, str]:
    if _looks_like_noise(value):
        return "rejected", "label_or_table_noise", value
    if field in {"project_name"}:
        return _validate_project_name(value)
    if field in {"customer_name", "customer"}:
        return _validate_customer_name(value)
    if field == "sales_owner":
        return _validate_sales_owner(value)
    if field in {"bid_status", "registration_status", "contract_status", "lifecycle_stage", "closed_reason_type"}:
        return _validate_status_field(field, value)
    if len(value) > 160:
        return "rejected", "value_too_long_for_business_fact", value
    return "accepted", "", value


def _validate_project_name(value: str) -> Tuple[str, str, str]:
    clean = _strip_known_prefix(value)
    if len(clean) < 2:
        return "rejected", "project_name_too_short", clean
    if len(clean) > 80:
        return "rejected", "project_name_too_long", clean
    if _sentence_like(clean):
        return "rejected", "project_name_sentence_like", clean
    return "accepted", "", clean


def _validate_customer_name(value: str) -> Tuple[str, str, str]:
    clean = _strip_known_prefix(value)
    if len(clean) < 2:
        return "rejected", "customer_name_too_short", clean
    if len(clean) > 80:
        return "rejected", "customer_name_too_long", clean
    if _looks_like_table_role_noise(clean):
        return "rejected", "customer_name_table_role_noise", clean
    if _sentence_like(clean):
        return "rejected", "customer_name_sentence_like", clean
    if any(clean.endswith(suffix) or suffix in clean for suffix in ORG_SUFFIXES):
        return "accepted", "", clean
    if re.fullmatch(r"[A-Za-z0-9_\-]{4,}", clean):
        return "accepted", "", clean
    return "rejected", "customer_name_missing_org_signal", clean


def _validate_sales_owner(value: str) -> Tuple[str, str, str]:
    clean = _strip_known_prefix(value)
    clean = re.sub(r"\s+", "", clean)
    if len(clean) < 2:
        return "rejected", "sales_owner_too_short", clean
    if len(clean) > 4:
        return "rejected", "sales_owner_not_person_name", clean
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", clean):
        return "rejected", "sales_owner_not_person_name", clean
    return "accepted", "", clean


def _validate_status_field(field: str, value: str) -> Tuple[str, str, str]:
    allowed: Dict[str, Iterable[str]] = {
        "bid_status": ("已中标", "未中标", "已丢标", "丢标", "已弃标", "弃标", "待开标"),
        "registration_status": ("待报名", "已报名", "已弃标", "弃标"),
        "contract_status": ("已签约", "已签订", "未签约", "待签约"),
        "lifecycle_stage": ("closed", "closed_lost", "execution", "executing", "delivery"),
        "closed_reason_type": ("abandoned_by_us", "lost_to_competitor"),
    }
    if value in allowed.get(field, ()):
        return "accepted", "", value
    return "rejected", "unknown_status_value", value


def _normalize_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip())


def _strip_known_prefix(value: str) -> str:
    return re.sub(
        r"^(?:[-*]\s*)?(?:项目名称|采购名称|标的名称|客户名称|客户|招标人|采购人|甲方|委托人|负责销售|销售负责人|客户经理|负责人)\s*[:：]?\s*",
        "",
        value,
    ).strip()


def _looks_like_noise(value: str) -> bool:
    if any(token in value for token in NOISE_TOKENS):
        return True
    if value.count("|") >= 2 or value.count("：") >= 3 or value.count(":") >= 3:
        return True
    return False


def _looks_like_table_role_noise(value: str) -> bool:
    role_hits = sum(1 for token in TABLE_ROLE_TOKENS if token in value)
    if role_hits >= 2:
        return True
    return bool(role_hits and len(value) > 40 and len(re.findall(r"[\u4e00-\u9fff]+(?:公司|中心|学院|大学|银行|院|所)", value)) >= 2)


def _sentence_like(value: str) -> bool:
    if any(token in value for token in SENTENCE_TOKENS):
        return True
    return len(re.findall(r"[，。；；,.]", value)) >= 2


def _review_item(field: str, value: Any, reason: str, normalized: str = "") -> Dict[str, Any]:
    return {
        "field": field,
        "value": value,
        "normalized_value": normalized or _normalize_value(value),
        "reason": reason,
    }
