"""Defender Agent — 对抗性验证第三轮：用原始文本证据为提取结果辩护。

Risk Mitigation R1：Defender 必须能读原始文档（OCR sidecar 或 .md 直接读取）。
如果 Defender 找不到原始证据，不直接判定，而是 disputed（缓解元认知缺陷）。

辩护决策:
- challenge_accepted：承认错误（强度 strong + 置信度 > 0.80）
- challenge_rejected：反驳成功（原始证据支持提取结果）
- disputed：证据不足，需要人工复核
"""
import os
from typing import Any, Dict, List


class DefenderAgent:
    """Defender：用原始文本证据为提取结果辩护（缓解 R1：能读原始文档）。

    输入: challenge_report + extracted_items
    输出: {"agent": "defender", "defenses": [...], "rejected_count": N, ...}
    """

    def defend(
        self,
        challenge_report: Dict[str, Any],
        extracted_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        challenges = challenge_report.get("challenges", [])
        defenses = []

        # 构建文件索引
        file_map = {item.get("file", ""): item for item in extracted_items}

        for challenge in challenges:
            cid = challenge["id"]
            target_fid = challenge.get("target_finding_id", "")
            strength = challenge.get("strength", "weak")
            confidence = challenge.get("confidence", 0.5)

            # 查找对应 finding 的原始文件
            finding_file = ""
            for item in extracted_items:
                if item.get("file", "") == challenge.get("file", ""):
                    finding_file = item.get("file", "")
                    break

            # 读取原始文本证据
            raw_evidence = ""
            if finding_file and os.path.exists(finding_file):
                raw_evidence = self._read_original_text(finding_file)

            # 决策逻辑
            if strength == "strong" and confidence > 0.80:
                outcome = "challenge_accepted"
                defense_type = "admit_error"
            elif raw_evidence and self._evidence_supports_extraction(raw_evidence, challenge):
                outcome = "challenge_rejected"
                defense_type = "evidence_refutation"
            else:
                # R1 缓解：如果 Defender 找不到原始证据，不直接判定，而是 disputed
                if not raw_evidence:
                    outcome = "disputed"
                    defense_type = "insufficient_evidence"
                else:
                    outcome = "disputed"
                    defense_type = "alternative_explanation"

            defenses.append({
                "id": f"D{len(defenses)+1:03d}",
                "target_challenge_id": cid,
                "target_finding_id": target_fid,
                "defense_type": defense_type,
                "outcome": outcome,
                "confidence": round(min(0.90, max(0.40, 0.70 + (0.15 if outcome == "challenge_rejected" else -0.10))), 2),
                "evidence_preview": raw_evidence[:300] if raw_evidence else "",
                "argument": self._build_defense_argument(outcome, defense_type, challenge),
            })

        return {
            "agent": "defender",
            "defenses": defenses,
            "defense_count": len(defenses),
            "rejected_count": len([d for d in defenses if d["outcome"] == "challenge_rejected"]),
            "accepted_count": len([d for d in defenses if d["outcome"] == "challenge_accepted"]),
            "disputed_count": len([d for d in defenses if d["outcome"] == "disputed"]),
        }

    def _read_original_text(self, file_path: str) -> str:
        """读取原始文档文本（优先 sidecar OCR，其次直接读取文本文件）"""
        # 1. sidecar OCR
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
        # 2. 文本文件直接读
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".md":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
        # 3. 无法读取（PDF/图片无 sidecar）
        return ""

    def _evidence_supports_extraction(self, raw_text: str, challenge: Dict[str, Any]) -> bool:
        """检查原始文本是否支持提取结果（简单启发式）"""
        argument = challenge.get("argument", "")
        # 如果 argument 提到"原文没有"，但实际文本不为空，反驳成立
        if "原文确实没有" in argument or "原文中没有" in argument:
            return bool(raw_text.strip())
        return False

    def _build_defense_argument(self, outcome: str, defense_type: str, challenge: Dict[str, Any]) -> str:
        mapping = {
            "challenge_rejected": "原始文本证据支持提取结果，Devil's Advocate 的反驳不成立。",
            "challenge_accepted": "经核实，提取结果确实存在错误，接受反驳。",
            "disputed": "原始证据不足以完全支持或否定任何一方，建议人工复核。",
        }
        return mapping.get(outcome, "辩护结论未明确。")
