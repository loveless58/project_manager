from datetime import date, datetime
from typing import Any, Dict, List, Optional


class BidProjectRuleEngine:
    """Deterministic business rules for bid project state and next actions."""

    REQUIRED_FIELDS = ["project_name", "customer_name", "sales_owner"]

    def __init__(self, today: Optional[str] = None):
        self.today = self._parse_date(today) or date.today()

    def evaluate(
        self,
        facts: Dict[str, Any],
        fact_meta: Optional[Dict[str, Any]] = None,
        conflicts: Optional[List[Dict[str, Any]]] = None,
        evidence_index: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        fact_meta = fact_meta or {}
        conflicts = conflicts or []
        evidence_index = evidence_index or []

        business_stage = self._business_stage(facts)
        display_status = self._display_status(business_stage)
        missing_fields = [field for field in self.REQUIRED_FIELDS if not facts.get(field)]
        conflict_fields = sorted({str(item.get("field")) for item in conflicts if item.get("status", "open") == "open" and item.get("field")})
        low_confidence_fields = self._low_confidence_fields(fact_meta)

        risk_reasons: List[str] = []
        next_actions: List[str] = []
        data_quality_flags: List[str] = []

        self._deadline_rules(facts, risk_reasons, next_actions)
        self._bond_rules(facts, risk_reasons, next_actions)
        self._stage_actions(business_stage, facts, next_actions)

        if missing_fields:
            data_quality_flags.append("关键字段缺失")
            next_actions.append("补充关键项目字段")
        if conflict_fields:
            data_quality_flags.append("存在冲突字段")
            next_actions.append("人工复核冲突字段")
        if low_confidence_fields:
            data_quality_flags.append("存在低置信度字段")

        risk_level = self._risk_level(risk_reasons, business_stage, missing_fields, conflict_fields)

        return {
            "schema_version": "bid_project.business_judgement.v1",
            "business_stage": business_stage,
            "display_status": display_status,
            "risk_level": risk_level,
            "risk_reasons": self._unique(risk_reasons),
            "next_actions": self._unique(next_actions),
            "missing_fields": missing_fields,
            "data_quality_flags": self._unique(data_quality_flags),
            "conflict_fields": conflict_fields,
            "low_confidence_fields": low_confidence_fields,
            "human_review_required": bool(missing_fields or conflict_fields or risk_level in {"high", "unknown"}),
            "crm_required": business_stage not in {"closed_lost", "unknown"},
            "bpm_required": business_stage in {"won_pending_contract", "execution"} and not facts.get("bpm_contract_code"),
            "evidence_count": len(evidence_index),
            "evaluated_at": datetime.now().isoformat(),
        }

    def _business_stage(self, facts: Dict[str, Any]) -> str:
        bid_status = str(facts.get("bid_status", ""))
        contract_status = str(facts.get("contract_status", ""))
        registration_status = str(facts.get("registration_status", ""))

        if "弃标" in bid_status:
            return "closed_lost"
        if "已签" in contract_status or facts.get("contract_code"):
            return "execution"
        if "已中标" in bid_status:
            return "won_pending_contract"
        if "待报名" in registration_status:
            return "pending_registration"
        if "已报名" in registration_status or "待开标" in bid_status:
            return "bidding"
        return "unknown"

    def _display_status(self, business_stage: str) -> str:
        if business_stage == "closed_lost":
            return "已弃标"
        if business_stage in {"won_pending_contract", "execution"}:
            return "已中标"
        return "参与中"

    def _deadline_rules(self, facts: Dict[str, Any], risk_reasons: List[str], next_actions: List[str]) -> None:
        registration_deadline = self._parse_date(facts.get("registration_deadline"))
        bid_open_time = self._parse_date(facts.get("bid_open_time"))
        registration_status = str(facts.get("registration_status", ""))
        bid_status = str(facts.get("bid_status", ""))

        if registration_deadline and registration_deadline < self.today and "待报名" in registration_status:
            risk_reasons.append("报名截止已过但状态仍为待报名")
            next_actions.append("确认报名是否完成")
        if bid_open_time and bid_open_time < self.today and "待开标" in bid_status:
            risk_reasons.append("开标时间已过但状态仍为待开标")
            next_actions.append("跟进开标结果")

    def _bond_rules(self, facts: Dict[str, Any], risk_reasons: List[str], next_actions: List[str]) -> None:
        if facts.get("bid_bond_amount") and str(facts.get("bid_bond_paid", "")) not in {"是", "已支付"}:
            risk_reasons.append("投标保证金存在但未确认支付")
            next_actions.append("确认保证金支付状态")

    def _stage_actions(self, business_stage: str, facts: Dict[str, Any], next_actions: List[str]) -> None:
        if business_stage == "pending_registration":
            next_actions.append("确认报名是否完成")
        elif business_stage == "bidding":
            next_actions.append("跟进开标结果")
        elif business_stage == "won_pending_contract":
            next_actions.append("准备合同签约")
            if not facts.get("bpm_contract_code"):
                next_actions.append("补充 BPM 合同号")
        elif business_stage == "execution":
            next_actions.append("跟踪执行里程碑")
            if not facts.get("bpm_contract_code"):
                next_actions.append("补充 BPM 合同号")

    def _risk_level(
        self,
        risk_reasons: List[str],
        business_stage: str,
        missing_fields: List[str],
        conflict_fields: List[str],
    ) -> str:
        if any("已过" in reason for reason in risk_reasons):
            return "high"
        if conflict_fields:
            return "high"
        if risk_reasons:
            return "medium"
        if business_stage == "won_pending_contract":
            return "medium"
        if missing_fields:
            return "unknown"
        return "low"

    def _low_confidence_fields(self, fact_meta: Dict[str, Any]) -> List[str]:
        fields = []
        for field, meta in fact_meta.items():
            try:
                score = float(meta.get("score", 1))
            except (TypeError, ValueError):
                score = 1
            if score < 0.70:
                fields.append(field)
        return sorted(fields)

    def _parse_date(self, value: Any) -> Optional[date]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except ValueError:
                continue
        return None

    def _unique(self, items: List[str]) -> List[str]:
        seen = set()
        result = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result
