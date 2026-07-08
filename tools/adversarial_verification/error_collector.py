"""Error Case Collector — 收集人工修正为错误案例，用于 V1.1 测试回流。

Risk Mitigation R3：错误案例永久保留到 V1.1（追加式写入 jsonl，不删除、不轮转）。

状态持久化:
  adversarial_verification/error_cases/error_registry.jsonl  ← 追加式记录

错误类型枚举（ERROR_TYPES）:
- field_extraction_incomplete: 字段提取不完整（遗漏字段）
- field_extraction_inaccurate: 字段提取不准确（值错误）
- classification_error: 文档分类错误
- archive_plan_error: 归档路径错误
- cross_doc_inconsistency: 跨文档不一致（V1.1+）
- ocr_quality_issue: OCR 质量问题
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


ERROR_TYPES = [
    "field_extraction_incomplete",
    "field_extraction_inaccurate",
    "classification_error",
    "archive_plan_error",
    "cross_doc_inconsistency",
    "ocr_quality_issue",
]


class ErrorCaseCollector:
    """错误案例收集器：在 apply_human_review 中自动触发。

    输入: workspace_dir + run_id + decision（人工修正）+ extracted_before（提取前快照）
    输出: case dict（已追加到 jsonl），None（如果无需记录）
    """

    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.registry_path = os.path.join(
            workspace_dir, "adversarial_verification", "error_cases", "error_registry.jsonl"
        )
        os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)

    def collect(
        self,
        run_id: str,
        decision: Dict[str, Any],
        extracted_before: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        记录一次人工修正为错误案例。

        如果 decision 中的字段与 extracted_before 不同，则生成错误案例。
        """
        facts = decision.get("facts") or {}
        if not facts:
            return None

        # 对比提取前和修正后的字段
        if extracted_before:
            diff_fields = self._compute_diff(extracted_before.get("fields", {}), facts)
        else:
            diff_fields = list(facts.keys())

        if not diff_fields:
            return None

        # 推断错误类型
        error_type = self._infer_error_type(diff_fields, decision)

        case = {
            "schema_version": "adversarial.error_case.v1",
            "case_id": f"EC_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}",
            "timestamp": datetime.now().isoformat(),
            "run_id": run_id,
            "error_type": error_type,
            "error_subtype": self._infer_subtype(error_type, diff_fields, decision),
            "source_file": decision.get("source_file", ""),
            "project_name": decision.get("project_name", ""),
            "extracted_fields": extracted_before.get("fields", {}) if extracted_before else {},
            "corrected_fields": {k: facts[k] for k in diff_fields if k in facts},
            "corrected_by": "human_review",
            "reason": decision.get("reason", ""),
        }

        # 追加写入（永久保留到 V1.1）
        with open(self.registry_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

        return case

    def get_error_stats(self) -> Dict[str, Any]:
        """按错误类型和字段聚类统计"""
        if not os.path.exists(self.registry_path):
            return {"total": 0, "by_type": {}, "by_field": {}}

        stats = {"total": 0, "by_type": {}, "by_field": {}}
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        case = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    stats["total"] += 1
                    et = case.get("error_type", "unknown")
                    stats["by_type"][et] = stats["by_type"].get(et, 0) + 1
                    for field in case.get("corrected_fields", {}).keys():
                        stats["by_field"][field] = stats["by_field"].get(field, 0) + 1
        except Exception:
            pass

        return stats

    def _compute_diff(self, before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
        """找出前后差异的字段"""
        diff = []
        for key, value in after.items():
            if key not in before or before.get(key) != value:
                diff.append(key)
        return diff

    def _infer_error_type(self, diff_fields: List[str], decision: Dict[str, Any]) -> str:
        """根据差异字段推断错误类型"""
        reason = decision.get("reason", "").lower()
        if "分类" in reason or "类型" in reason:
            return "classification_error"
        if "归档" in reason or "路径" in reason:
            return "archive_plan_error"
        if "ocr" in reason or "识别" in reason or "质量" in reason:
            return "ocr_quality_issue"
        # 如果新增字段 → 遗漏
        if any(f in ("project_name", "budget", "customer", "deadline", "sales_owner") for f in diff_fields):
            return "field_extraction_incomplete"
        return "field_extraction_inaccurate"

    def _infer_subtype(self, error_type: str, diff_fields: List[str], decision: Dict[str, Any]) -> str:
        """推断更细粒度的子类型"""
        if error_type == "field_extraction_incomplete" and diff_fields:
            return f"missing_{diff_fields[0]}"
        if error_type == "field_extraction_inaccurate" and diff_fields:
            return f"incorrect_{diff_fields[0]}"
        return "general"
