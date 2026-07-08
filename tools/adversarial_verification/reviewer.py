"""Reviewer Agent — 对抗性验证第一轮：审查提取结果，识别潜在问题。

审查维度（V1 实现 4 个，V1.1+ 实现跨文档一致性）:
1. field_completeness - 字段完整性：必填字段是否全部提取
2. field_accuracy - 字段准确性：值是否与原文一致（含数值近似匹配、日期格式检查）
3. document_classification - 文档分类：分类是否与文件名/内容匹配
4. archive_plan - 归档合理性：目标路径是否匹配文档类型
5. cross_doc_consistency - 跨文档一致性（V1.1+）

设计原则:
- 纯 Python 实现（正则 + 启发式），保持与 DataCleaningTools 一致的技术栈
- 阶段 2 计划叠加 LLM agent 实现层（双轨 + feature flag）
- 阶段 3 计划淘汰纯代码 fallback
"""
import os
import re
from typing import Any, Dict, List


REVIEW_DIMENSIONS = [
    "field_completeness",          # 字段完整性
    "field_accuracy",              # 字段准确性
    "document_classification",     # 文档分类
    "archive_plan",                # 归档合理性
    "cross_doc_consistency",       # 跨文档一致性（V1.1+）
]


REQUIRED_FIELDS = {
    "投标文件": ["project_name", "budget", "customer", "deadline"],
    "采购公告": ["project_name", "budget", "customer", "deadline"],
    "合同": ["project_name", "customer", "contract_status"],
    "未分类": ["project_name"],
}


class ReviewerAgent:
    """Reviewer：审查提取结果，识别潜在问题。

    输入: extracted_items + archive_actions + ledger_results
    输出: {"agent": "reviewer", "findings": [...], "finding_count": N, ...}
    """

    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def review(
        self,
        extracted_items: List[Dict[str, Any]],
        archive_actions: List[Dict[str, Any]],
        ledger_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        findings = []
        for idx, item in enumerate(extracted_items):
            fields = item.get("fields") or {}
            doc_type = item.get("document_type") or fields.get("document_type") or "未分类"
            text = item.get("extracted_text", "")
            text_full = self._load_full_text(item)

            # 维度1: 字段完整性
            required = REQUIRED_FIELDS.get(doc_type, REQUIRED_FIELDS["未分类"])
            missing = [f for f in required if f not in fields or not fields.get(f)]
            if missing:
                findings.append({
                    "id": f"R{len(findings)+1:03d}",
                    "dimension": "field_completeness",
                    "severity": "high" if len(missing) >= 2 else "medium",
                    "confidence": 0.85 + min(len(missing) * 0.03, 0.1),
                    "file": item.get("file", ""),
                    "message": f"缺少字段: {', '.join(missing)}",
                    "details": {"missing_fields": missing, "available": list(fields.keys())},
                })

            # 维度2: 字段准确性
            for field, value in fields.items():
                if field in ("document_type", "source_filename"):
                    continue
                if not value or not text_full:
                    continue
                # 数值型字段做近似匹配
                if field in ("budget", "quoted_amount"):
                    if not self._value_in_text(value, text_full):
                        findings.append({
                            "id": f"R{len(findings)+1:03d}",
                            "dimension": "field_accuracy",
                            "severity": "medium",
                            "confidence": 0.75,
                            "file": item.get("file", ""),
                            "message": f"字段 '{field}' 的值 '{value}' 在原文中未找到匹配",
                            "details": {"field": field, "value": value},
                        })
                # 日期型字段做格式检查
                elif field == "deadline":
                    if not re.search(r"\d{4}[年/\-]\d{1,2}[月/\-]?\d{0,2}", str(value)):
                        findings.append({
                            "id": f"R{len(findings)+1:03d}",
                            "dimension": "field_accuracy",
                            "severity": "medium",
                            "confidence": 0.80,
                            "file": item.get("file", ""),
                            "message": f"字段 '{field}' 的值 '{value}' 日期格式可能不正确",
                            "details": {"field": field, "value": value},
                        })

            # 维度3: 文档分类
            filename = item.get("filename", "")
            predicted_type = doc_type
            inferred_from_name = self._infer_type_from_filename(filename)
            if inferred_from_name and predicted_type not in inferred_from_name and inferred_from_name not in predicted_type:
                findings.append({
                    "id": f"R{len(findings)+1:03d}",
                    "dimension": "document_classification",
                    "severity": "medium",
                    "confidence": 0.70,
                    "file": item.get("file", ""),
                    "message": f"文档分类 '{predicted_type}' 与文件名推断 '{inferred_from_name}' 不一致",
                    "details": {"predicted": predicted_type, "inferred_from_name": inferred_from_name},
                })

        # 维度4: 归档合理性
        for action in archive_actions:
            target = action.get("target_path", "")
            doc_type = action.get("document_type", "")
            safe_project = self._safe_name(action.get("project_name", ""))
            if safe_project and safe_project not in target.replace("未命名项目", ""):
                findings.append({
                    "id": f"R{len(findings)+1:03d}",
                    "dimension": "archive_plan",
                    "severity": "low",
                    "confidence": 0.65,
                    "file": action.get("source_file", ""),
                    "message": f"归档路径可能缺少项目名映射",
                    "details": {"target": target, "project_name": action.get("project_name")},
                })

        return {
            "agent": "reviewer",
            "findings": findings,
            "finding_count": len(findings),
            "high_count": len([f for f in findings if f["severity"] == "high"]),
            "medium_count": len([f for f in findings if f["severity"] == "medium"]),
            "low_count": len([f for f in findings if f["severity"] == "low"]),
        }

    def _load_full_text(self, item: Dict[str, Any]) -> str:
        """加载原始完整文本（优先 extracted_text，回退到源文件）"""
        text = item.get("extracted_text", "")
        if text:
            return text
        file_path = item.get("file", "")
        if file_path and os.path.exists(file_path):
            sidecar = f"{file_path}.ocr.txt"
            base, _ = os.path.splitext(file_path)
            sidecar2 = f"{base}.ocr.txt"
            for sc in (sidecar, sidecar2):
                if os.path.exists(sc):
                    try:
                        with open(sc, "r", encoding="utf-8") as f:
                            return f.read()
                    except Exception:
                        pass
        return ""

    def _value_in_text(self, value: str, text: str) -> bool:
        """检查数值是否在原文中出现（处理逗号和万/元后缀）"""
        if not value or not text:
            return False
        cleaned = str(value).replace(",", "").replace("万", "").replace("元", "").strip()
        if cleaned in text:
            return True
        if re.search(r"\d", cleaned):
            digits = re.sub(r"[^\d.]", "", cleaned)
            if digits and digits in re.sub(r"[^\d.]", "", text):
                return True
        return False

    def _infer_type_from_filename(self, filename: str) -> str:
        haystack = filename.lower()
        if "投标" in haystack or "报价" in haystack or "标书" in haystack:
            return "投标文件"
        if "采购" in haystack or "招标" in haystack:
            return "采购公告"
        if "合同" in haystack:
            return "合同"
        return ""

    @staticmethod
    def _safe_name(name: str) -> str:
        safe = re.sub(r'[\\/:*?"<>|]', "_", name or "").strip()
        return safe or "未命名"
