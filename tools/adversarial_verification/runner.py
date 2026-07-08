"""Adversarial Verification Runner — 主调度循环。

Risk Mitigations:
- R2: OCR 置信度 < 阈值时直接升级 needs_human_review，跳过对抗验证
- R7: 归档阻断消息含可读 finding 摘要（check_archive_gate 函数）
- R10: 验证报告按月份归档

状态持久化（文件系统）:
  runs/<run_id>/adversarial_verification.json  ← 验证报告
  adversarial_verification/reports/<YYYY-MM>_<run_id>_verification.json  ← 月度归档

主循环流程:
1. OCR 质量门控（任何 item 置信度 < 阈值 → 直接 needs_human_review）
2. Round 1: Reviewer 审查
3. Round 2: Devil's Advocate 反驳
4. Round 3: Defender 辩护
5. Resolution: 三方聚合（_resolve），输出 overall_verdict
6. 持久化（run_dir + 月度归档）
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from contracts.fields import OCR_CONFIDENCE_THRESHOLD
from .defender import DefenderAgent
from .devils_advocate import DevilsAdvocateAgent
from .reviewer import ReviewerAgent


class AdversarialVerification:
    """对抗性验证循环执行器：Reviewer → Devil's Advocate → Defender → Resolution。

    输入: workspace_dir + run_id + extracted_items + ledger_results + archive_actions
    输出: 完整的三轮对抗结果 + overall_verdict（pass / pass_with_warnings / needs_correction / needs_human_review）
    """

    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.reviewer = ReviewerAgent(workspace_dir)
        self.devils_advocate = DevilsAdvocateAgent()
        self.defender = DefenderAgent()

    def run(
        self,
        run_id: str,
        extracted_items: List[Dict[str, Any]],
        ledger_results: Optional[List[Dict[str, Any]]] = None,
        archive_actions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """执行完整的三轮对抗性验证循环。

        缓解 R2：如果任何 item 的 OCR 置信度 < OCR_CONFIDENCE_THRESHOLD，
        直接返回 needs_human_review，跳过对抗验证。
        """
        # R2 缓解：OCR 质量门控
        low_confidence_items = []
        for item in extracted_items:
            ocr = item.get("ocr") or {}
            pages = ocr.get("pages", [])
            if pages:
                avg_conf = sum(p.get("confidence", 0.85) for p in pages) / len(pages)
                if avg_conf < OCR_CONFIDENCE_THRESHOLD:
                    low_confidence_items.append({
                        "file": item.get("file", ""),
                        "ocr_confidence": round(avg_conf, 2),
                    })

        if low_confidence_items:
            return self._build_result(
                run_id=run_id,
                overall_verdict="needs_human_review",
                reviewer_report={"findings": [], "finding_count": 0, "high_count": 0, "medium_count": 0, "low_count": 0},
                challenge_report={"challenges": [], "challenge_count": 0, "strong_count": 0},
                defense_report={"defenses": [], "defense_count": 0, "rejected_count": 0, "accepted_count": 0, "disputed_count": 0},
                resolutions=[],
                block_reason=f"OCR 置信度低于阈值({OCR_CONFIDENCE_THRESHOLD})，跳过对抗验证，直接升级人工复核",
                low_confidence_items=low_confidence_items,
            )

        # Round 1: Reviewer
        reviewer_report = self.reviewer.review(
            extracted_items=extracted_items,
            archive_actions=archive_actions or [],
            ledger_results=ledger_results or [],
        )

        # Round 2: Devil's Advocate
        challenge_report = self.devils_advocate.challenge(reviewer_report)

        # Round 3: Defender
        defense_report = self.defender.defend(challenge_report, extracted_items)

        # Resolution: 聚合
        resolutions, overall_verdict = self._resolve(
            reviewer_report, challenge_report, defense_report
        )

        return self._build_result(
            run_id=run_id,
            overall_verdict=overall_verdict,
            reviewer_report=reviewer_report,
            challenge_report=challenge_report,
            defense_report=defense_report,
            resolutions=resolutions,
        )

    def _resolve(
        self,
        reviewer_report: Dict[str, Any],
        challenge_report: Dict[str, Any],
        defense_report: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        聚合三方结论，生成最终决议。

        规则:
        - 无 finding → pass
        - Devil's Advocate 提出有效反驳（strength ≥ moderate, confidence ≥ 0.70）
          且 Defender 无法有效反驳（outcome ≠ challenge_rejected 或 confidence < 0.70）
          → false_positive（Reviewer 误判）
        - 无反驳或 Defender 成功反驳 → confirmed
        - 三方意见分歧（disputed）→ disputed
        - 有 high severity confirmed → needs_correction
        - 仅 low/medium confirmed 或 pass_with_warnings → pass_with_warnings
        - 有 disputed → needs_human_review
        """
        findings = reviewer_report.get("findings", [])
        challenges = {c["target_finding_id"]: c for c in challenge_report.get("challenges", [])}
        defenses = {d["target_challenge_id"]: d for d in defense_report.get("defenses", [])}

        resolutions = []
        confirmed_high = 0
        confirmed_medium = 0
        has_disputed = False

        for finding in findings:
            fid = finding["id"]
            challenge = challenges.get(fid)

            if not challenge:
                # 无反驳 → confirmed
                resolution = "confirmed"
            else:
                # 查找对应 defense
                defense = None
                for d in defense_report.get("defenses", []):
                    if d.get("target_finding_id") == fid:
                        defense = d
                        break

                if not defense:
                    resolution = "confirmed"
                elif defense["outcome"] == "challenge_rejected" and defense["confidence"] >= 0.70:
                    resolution = "false_positive"
                elif defense["outcome"] == "challenge_accepted":
                    resolution = "confirmed"  # Defender 承认错误
                elif defense["outcome"] == "disputed":
                    resolution = "disputed"
                    has_disputed = True
                else:
                    # R1 缓解：Defender 无法找到证据 → disputed 而非直接判定
                    resolution = "disputed"
                    has_disputed = True

            resolutions.append({
                "finding_id": fid,
                "dimension": finding.get("dimension"),
                "severity": finding.get("severity"),
                "resolution": resolution,
            })

            if resolution == "confirmed":
                if finding.get("severity") == "high":
                    confirmed_high += 1
                elif finding.get("severity") == "medium":
                    confirmed_medium += 1

        # 计算 overall_verdict
        if not findings:
            overall_verdict = "pass"
        elif confirmed_high > 0:
            overall_verdict = "needs_correction"
        elif has_disputed:
            overall_verdict = "needs_human_review"
        elif confirmed_medium > 0:
            overall_verdict = "pass_with_warnings"
        else:
            overall_verdict = "pass"

        return resolutions, overall_verdict

    def _build_result(
        self,
        run_id: str,
        overall_verdict: str,
        reviewer_report: Dict[str, Any],
        challenge_report: Dict[str, Any],
        defense_report: Dict[str, Any],
        resolutions: List[Dict[str, Any]],
        block_reason: str = "",
        low_confidence_items: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        result = {
            "schema_version": "adversarial_verification.v1",
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "overall_verdict": overall_verdict,
            "reviewer_report": reviewer_report,
            "challenge_report": challenge_report,
            "defense_report": defense_report,
            "resolutions": resolutions,
        }
        if block_reason:
            result["block_reason"] = block_reason
        if low_confidence_items:
            result["low_confidence_items"] = low_confidence_items

        # 保存到 runs/<run_id>/
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        os.makedirs(run_dir, exist_ok=True)
        verification_path = os.path.join(run_dir, "adversarial_verification.json")
        with open(verification_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # 按月份归档（缓解 R10）
        month_key = datetime.now().strftime("%Y-%m")
        archive_dir = os.path.join(self.workspace_dir, "adversarial_verification", "reports")
        os.makedirs(archive_dir, exist_ok=True)
        archive_path = os.path.join(archive_dir, f"{month_key}_{run_id}_verification.json")
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return result


def check_archive_gate(workspace_dir: str, run_id: str) -> Dict[str, Any]:
    """检查归档是否被对抗性验证阻断（缓解 R7：阻断消息含可读 finding 摘要）。

    返回 dict，若 blocked 则包含详细阻断原因（top 3 findings 按 severity 排序）。
    """
    verification_path = os.path.join(workspace_dir, "runs", run_id, "adversarial_verification.json")
    if not os.path.exists(verification_path):
        return {"blocked": False, "message": "无对抗性验证记录，允许归档"}

    with open(verification_path, "r", encoding="utf-8") as f:
        verification = json.load(f)

    verdict = verification.get("overall_verdict", "")
    block_reason = verification.get("block_reason", "")

    if verdict == "needs_correction":
        # 收集 top 3 highest severity findings
        resolutions = verification.get("resolutions", [])
        confirmed = [r for r in resolutions if r.get("resolution") == "confirmed"]
        confirmed.sort(key=lambda x: {"high": 3, "medium": 2, "low": 1}.get(x.get("severity", "low"), 0), reverse=True)
        top_findings = confirmed[:3]

        finding_summaries = []
        for f in top_findings:
            finding_summaries.append(f"  - [{f.get('severity', '').upper()}] {f.get('dimension', '')}: {f.get('finding_id', '')}")

        msg_lines = [
            f"归档被阻断：对抗性验证结论为 '{verdict}'",
            f"",
            f"验证报告路径: {verification_path}",
            f"",
            f"Top 确认问题（需人工修正）:",
        ]
        msg_lines.extend(finding_summaries)
        msg_lines.append(f"")
        msg_lines.append(f"请在修正后重新运行 apply_human_review，或手动确认可接受风险后归档。")

        return {
            "blocked": True,
            "blockers": ["adversarial_verification_failed"],
            "verdict": verdict,
            "message": "\n".join(msg_lines),
            "verification_path": verification_path,
            "top_findings": top_findings,
        }

    if verdict == "needs_human_review":
        return {
            "blocked": True,
            "blockers": ["adversarial_verification_needs_human_review"],
            "verdict": verdict,
            "message": f"归档被阻断：对抗性验证发现争议项，需人工复核。\n验证报告: {verification_path}",
            "verification_path": verification_path,
        }

    if block_reason:
        return {
            "blocked": True,
            "blockers": ["adversarial_verification_blocked"],
            "message": f"归档被阻断：{block_reason}\n验证报告: {verification_path}",
            "verification_path": verification_path,
        }

    return {"blocked": False, "message": f"对抗性验证通过 ({verdict})"}
