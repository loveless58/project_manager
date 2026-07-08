"""Devil's Advocate Agent — 对抗性验证第二轮：对 Reviewer 结论提出系统性反驳。

反驳策略:
1. assumption_reversal - 假设反转：原结论假设可能基于不成立的隐含假设
2. boundary_challenge - 边界挑战：分类标准边界不清，可能同时属于多个类别
3. omission_question - 遗漏质疑：'缺失'可能意味着原文确实没有该信息
4. logic_contradiction - 逻辑矛盾：依赖未声明的假设
5. confidence_attack - 置信度攻击：跨文档一致性检查置信度不足

设计原则:
- 只反驳中等及以上强度的 finding（severity != low 或 confidence >= 0.75）
- finding 的 confidence > 0.85 时，反驳强度降为 weak（防止反驳过度）
- 反驳强度（strong/moderate/weak）由 finding 的 severity 和 confidence 综合决定
"""
from typing import Any, Dict


class DevilsAdvocateAgent:
    """Devil's Advocate：对 Reviewer 结论提出系统性反驳。

    输入: reviewer_report（来自 ReviewerAgent.review）
    输出: {"agent": "devils_advocate", "challenges": [...], "challenge_count": N, ...}
    """

    CHALLENGE_STRATEGIES = [
        "assumption_reversal",
        "boundary_challenge",
        "omission_question",
        "logic_contradiction",
        "confidence_attack",
    ]

    def challenge(self, reviewer_report: Dict[str, Any]) -> Dict[str, Any]:
        findings = reviewer_report.get("findings", [])
        challenges = []

        for finding in findings:
            fid = finding["id"]
            dimension = finding.get("dimension", "")
            severity = finding.get("severity", "low")
            confidence = finding.get("confidence", 0.5)

            # 只反驳中等及以上强度的 finding
            if severity == "low" and confidence < 0.75:
                continue

            challenge_type = self._select_strategy(dimension, finding)
            strength = self._assess_challenge_strength(finding)

            # 如果 finding 的 confidence > 0.85，反驳强度降低
            if confidence > 0.85:
                strength = "weak"

            challenges.append({
                "id": f"C{len(challenges)+1:03d}",
                "target_finding_id": fid,
                "challenge_type": challenge_type,
                "strength": strength,
                "confidence": round(max(0.50, min(0.95, 1.0 - confidence + 0.20)), 2),
                "argument": self._build_argument(finding, challenge_type),
            })

        return {
            "agent": "devils_advocate",
            "challenges": challenges,
            "challenge_count": len(challenges),
            "strong_count": len([c for c in challenges if c["strength"] == "strong"]),
        }

    def _select_strategy(self, dimension: str, finding: Dict[str, Any]) -> str:
        mapping = {
            "field_completeness": "omission_question",
            "field_accuracy": "assumption_reversal",
            "document_classification": "boundary_challenge",
            "archive_plan": "logic_contradiction",
            "cross_doc_consistency": "confidence_attack",
        }
        return mapping.get(dimension, "assumption_reversal")

    def _assess_challenge_strength(self, finding: Dict[str, Any]) -> str:
        severity = finding.get("severity", "low")
        if severity == "high":
            return "moderate"
        return "strong" if finding.get("confidence", 0) < 0.80 else "weak"

    def _build_argument(self, finding: Dict[str, Any], strategy: str) -> str:
        field = finding.get("details", {}).get("field", "该字段")
        strategies = {
            "assumption_reversal": f"原结论假设 '{field}' 的值是错误的，但可能原文本身表述模糊，提取器做的是合理推断。",
            "boundary_challenge": f"分类标准边界不清：'{field}' 可能同时属于多个类别，不应以单一标签判定错误。",
            "omission_question": f"'缺失'可能意味着原文确实没有该信息，而非提取器遗漏。需检查原文。",
            "logic_contradiction": f"归档路径的合理性依赖于未声明的假设（项目命名规则），该假设可能不成立。",
            "confidence_attack": f"跨文档一致性检查置信度不足，命名变体（如缩写/全称）可能未被考虑。",
        }
        return strategies.get(strategy, "对 finding 的有效性提出质疑。")
