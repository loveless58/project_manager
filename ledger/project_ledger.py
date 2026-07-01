import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional


SOURCE_WEIGHTS = {
    "human_correction": 1.00,
    "crm_readback": 0.95,
    "bpm_readback": 0.95,
    "contract_document": 0.92,
    "winning_notice": 0.92,
    "official_document": 0.90,
    "bid_document": 0.90,
    "system_export": 0.85,
    "local_file": 0.80,
    "xlsx_summary": 0.75,
    "web_source": 0.70,
    "md_note": 0.65,
    "ocr_extract": 0.55,
    "llm_inference": 0.35,
}

FIELD_POLICIES = {
    "customer_name": {"auto_accept_threshold": 0.88, "risk_level": "P0"},
    "contract_amount": {"auto_accept_threshold": 0.95, "risk_level": "P0"},
    "project_code": {"auto_accept_threshold": 0.90, "risk_level": "P1"},
    "crm_project_code": {"auto_accept_threshold": 0.90, "risk_level": "P1"},
    "bid_open_time": {"auto_accept_threshold": 0.86, "risk_level": "P1"},
    "registration_deadline": {"auto_accept_threshold": 0.86, "risk_level": "P1"},
    "project_name": {"auto_accept_threshold": 0.82, "risk_level": "P1"},
    "bid_status": {"auto_accept_threshold": 0.72, "risk_level": "P2"},
    "document_type": {"auto_accept_threshold": 0.70, "risk_level": "P2"},
}

DEFAULT_POLICY = {"auto_accept_threshold": 0.75, "risk_level": "P2"}


class ProjectLedger:
    """Read, reconcile, and write one project's shared evidence ledger."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def apply_patch(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        project_name = self._project_name_from_patch(patch)
        project_dir = self._project_dir(project_name)
        os.makedirs(project_dir, exist_ok=True)

        state_path = os.path.join(project_dir, "project_ledger.json")
        state = self._load_state(state_path, project_name)

        source_type = patch.get("source_type") or "local_file"
        facts = patch.get("facts") or {}
        evidence = patch.get("evidence") or []
        submitted_at = datetime.now().isoformat()

        decisions: Dict[str, Dict[str, Any]] = {}
        for field, value in facts.items():
            if value is None or str(value).strip() == "":
                continue
            candidate = self._candidate(field, value, source_type, patch, evidence, submitted_at)
            state["candidate_facts"].append(candidate)
            decision = self._reconcile_field(state, candidate)
            decisions[field] = decision

        for item in evidence:
            state["evidence_index"].append(self._normalize_evidence(item, source_type, submitted_at))

        state["updated_at"] = submitted_at
        state["change_log"].append({
            "timestamp": submitted_at,
            "actor": patch.get("actor", "data_cleaning_file_organization"),
            "skill": patch.get("skill", "data_cleaning_file_organization"),
            "source_type": source_type,
            "fields": list(facts.keys()),
            "decisions": decisions,
        })

        markdown_path = os.path.join(project_dir, "项目总览.md")
        self._save_json(state_path, state)
        self._save_markdown(markdown_path, state)
        self._append_decision_log(project_dir, state, decisions, patch, submitted_at)

        return {
            "schema_version": "project.ledger.result.v1",
            "status": "success",
            "project_name": project_name,
            "project_dir": project_dir,
            "state_path": state_path,
            "markdown_path": markdown_path,
            "current_facts": state["current_facts"],
            "decisions": decisions,
            "conflicts": state["conflicts"],
        }

    def _project_name_from_patch(self, patch: Dict[str, Any]) -> str:
        facts = patch.get("facts") or {}
        return patch.get("project_name") or facts.get("project_name") or "未命名项目"

    def _project_dir(self, project_name: str) -> str:
        safe = re.sub(r'[<>:"/\\|?*\s]+', "_", project_name).strip("_")
        return os.path.join(self.base_dir, safe or "unnamed_project")

    def _load_state(self, state_path: str, project_name: str) -> Dict[str, Any]:
        if os.path.exists(state_path):
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        now = datetime.now().isoformat()
        return {
            "schema_version": "project_ledger.v1",
            "project_name": project_name,
            "created_at": now,
            "updated_at": now,
            "current_facts": {},
            "fact_meta": {},
            "candidate_facts": [],
            "evidence_index": [],
            "conflicts": [],
            "change_log": [],
        }

    def _candidate(
        self,
        field: str,
        value: Any,
        source_type: str,
        patch: Dict[str, Any],
        evidence: List[Dict[str, Any]],
        submitted_at: str,
    ) -> Dict[str, Any]:
        matching_evidence = [e for e in evidence if e.get("field") in (None, field)]
        evidence_confidence = max([float(e.get("confidence", 0.75)) for e in matching_evidence] or [0.75])
        effective_source_type = self._effective_source_type(source_type, matching_evidence)
        source_weight = float(SOURCE_WEIGHTS.get(effective_source_type, SOURCE_WEIGHTS.get(source_type, 0.60)))
        score = round(source_weight * 0.6 + evidence_confidence * 0.4, 4)
        return {
            "field": field,
            "value": value,
            "source_type": source_type,
            "effective_source_type": effective_source_type,
            "source_weight": source_weight,
            "extract_confidence": evidence_confidence,
            "score": score,
            "source_ref": patch.get("source_ref") or self._first_source_ref(matching_evidence),
            "submitted_at": submitted_at,
            "skill": patch.get("skill", "data_cleaning_file_organization"),
        }

    def _effective_source_type(self, source_type: str, evidence: List[Dict[str, Any]]) -> str:
        if source_type != "local_file":
            return source_type
        joined_refs = " ".join(str(item.get("source_ref", "")) for item in evidence)
        if any(k in joined_refs for k in ["合同", "contract"]):
            return "contract_document"
        if any(k in joined_refs for k in ["中标通知", "winning"]):
            return "winning_notice"
        if any(k in joined_refs for k in ["招标文件", "投标文件", "bid"]):
            return "bid_document"
        return source_type

    def _first_source_ref(self, evidence: List[Dict[str, Any]]) -> str:
        for item in evidence:
            if item.get("source_ref"):
                return item["source_ref"]
        return ""

    def _reconcile_field(self, state: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        field = candidate["field"]
        value = candidate["value"]
        policy = FIELD_POLICIES.get(field, DEFAULT_POLICY)
        current_value = state["current_facts"].get(field)
        current_meta = state["fact_meta"].get(field, {})

        if current_value is None:
            accepted = candidate["score"] >= policy["auto_accept_threshold"]
            status = "verified" if accepted else "extracted"
            state["current_facts"][field] = value
            state["fact_meta"][field] = {**candidate, "status": status, "risk_level": policy["risk_level"]}
            return {
                "field": field,
                "status": status,
                "selected_value": value,
                "score": candidate["score"],
                "reason": "first_candidate accepted into ledger",
                "requires_human_review": status != "verified",
            }

        if str(current_value) == str(value):
            if candidate["score"] >= float(current_meta.get("score", 0)):
                state["fact_meta"][field] = {**candidate, "status": "verified", "risk_level": policy["risk_level"]}
            return {
                "field": field,
                "status": "verified",
                "selected_value": current_value,
                "score": max(candidate["score"], float(current_meta.get("score", 0))),
                "reason": "candidate agrees with current fact",
                "requires_human_review": False,
            }

        current_score = float(current_meta.get("score", 0))
        if candidate["score"] > current_score and candidate["score"] >= policy["auto_accept_threshold"]:
            previous_value = current_value
            state["current_facts"][field] = value
            state["fact_meta"][field] = {**candidate, "status": "verified", "risk_level": policy["risk_level"]}
            state["conflicts"].append(self._conflict(field, previous_value, current_meta, candidate, "candidate_overrode_lower_score"))
            return {
                "field": field,
                "status": "verified",
                "selected_value": value,
                "previous_value": previous_value,
                "score": candidate["score"],
                "reason": "higher weighted candidate overrode current fact",
                "requires_human_review": False,
            }

        state["conflicts"].append(self._conflict(field, current_value, current_meta, candidate, "candidate_kept_as_conflict"))
        return {
            "field": field,
            "status": "conflict",
            "selected_value": current_value,
            "candidate_value": value,
            "score": candidate["score"],
            "current_score": current_score,
            "reason": "candidate conflicts with higher or equal confidence current fact",
            "requires_human_review": True,
        }

    def _conflict(self, field: str, current_value: Any, current_meta: Dict[str, Any], candidate: Dict[str, Any], reason: str) -> Dict[str, Any]:
        return {
            "field": field,
            "current_value": current_value,
            "current_source_type": current_meta.get("source_type"),
            "current_score": current_meta.get("score"),
            "candidate_value": candidate["value"],
            "candidate_source_type": candidate["source_type"],
            "candidate_score": candidate["score"],
            "reason": reason,
            "created_at": datetime.now().isoformat(),
            "status": "open",
        }

    def _normalize_evidence(self, item: Dict[str, Any], source_type: str, submitted_at: str) -> Dict[str, Any]:
        return {
            "field": item.get("field", ""),
            "source_type": item.get("source_type", source_type),
            "source_ref": item.get("source_ref", ""),
            "extract_method": item.get("extract_method", ""),
            "confidence": item.get("confidence", 0.75),
            "summary": item.get("summary", ""),
            "created_at": submitted_at,
        }

    def _save_json(self, path: str, state: Dict[str, Any]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _save_markdown(self, path: str, state: Dict[str, Any]) -> None:
        lines = [
            f"# 项目总览：{state['project_name']}",
            "",
            "---",
            f"schema_version: {state['schema_version']}",
            f"project_name: {state['project_name']}",
            f"updated_at: {state['updated_at']}",
            "---",
            "",
            "## 1. 当前事实",
            "| 字段 | 当前值 | 状态 | 来源类型 | 分数 |",
            "|---|---|---|---|---:|",
        ]
        for field, value in sorted(state["current_facts"].items()):
            meta = state["fact_meta"].get(field, {})
            lines.append(f"| {field} | {value} | {meta.get('status', '')} | {meta.get('source_type', '')} | {meta.get('score', '')} |")

        lines.extend(["", "## 2. 候选事实", "| 字段 | 候选值 | 来源类型 | 分数 | 来源引用 |", "|---|---|---|---:|---|"])
        for item in state["candidate_facts"]:
            lines.append(f"| {item['field']} | {item['value']} | {item['source_type']} | {item['score']} | {item.get('source_ref', '')} |")

        lines.extend(["", "## 3. 证据索引", "| 字段 | 来源类型 | 来源引用 | 提取方式 | 置信度 |", "|---|---|---|---|---:|"])
        for item in state["evidence_index"]:
            lines.append(f"| {item.get('field', '')} | {item.get('source_type', '')} | {item.get('source_ref', '')} | {item.get('extract_method', '')} | {item.get('confidence', '')} |")

        lines.extend(["", "## 4. 冲突与待确认", "| 字段 | 当前值 | 冲突值 | 原因 | 状态 |", "|---|---|---|---|---|"])
        for item in state["conflicts"]:
            lines.append(f"| {item['field']} | {item['current_value']} | {item['candidate_value']} | {item['reason']} | {item['status']} |")

        lines.extend(["", "## 5. 变更日志", "| 时间 | Skill | 来源类型 | 字段 |", "|---|---|---|---|"])
        for item in state["change_log"]:
            lines.append(f"| {item['timestamp']} | {item.get('skill', '')} | {item.get('source_type', '')} | {', '.join(item.get('fields', []))} |")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _append_decision_log(
        self,
        project_dir: str,
        state: Dict[str, Any],
        decisions: Dict[str, Dict[str, Any]],
        patch: Dict[str, Any],
        submitted_at: str,
    ) -> None:
        log_path = os.path.join(project_dir, "decision_log.jsonl")
        record = {
            "schema_version": "project_ledger.decision_log.v1",
            "timestamp": submitted_at,
            "project_name": state["project_name"],
            "source_type": patch.get("source_type", "local_file"),
            "decisions": decisions,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
