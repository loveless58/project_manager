"""
Adversarial Verification — 数据清洗闭环对抗性验证模块

职责：
- 在 prepare_file_organization_run 执行后自动触发
- Reviewer Agent：审查提取结果的字段完整性、准确性、分类、归档合理性、跨文档一致性
- Devil's Advocate：对 Reviewer 结论提出系统性反驳
- Defender Agent：用原始文本证据为提取结果辩护
- 主 Agent 聚合三方结论，生成 verification_result
- ErrorCaseCollector：在 apply_human_review 中收集人工修正为错误案例

设计原则（风险缓解驱动）：
1. Defender 必须能读原始文档（缓解 R1：元认知缺陷）
2. OCR 置信度低时直接升级 needs_human_review（缓解 R2：认知上限）
3. 错误案例永久保留（缓解 R3：90天归档先于回流）
4. 归档阻断消息含可读 finding 摘要（缓解 R7：用户无上下文）
5. 验证报告按月份归档（缓解 R10：文件膨胀）

状态持久化（文件系统）：
  runs/<run_id>/adversarial_verification.json  ← 验证报告
  adversarial_verification/reports/<YYYY-MM>_<run_id>_verification.json  ← 月度归档
  adversarial_verification/error_cases/error_registry.jsonl  ← 追加式错误记录（永久保留到 V1.1）
"""
import os
import json
import re
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime


# ── 审查维度配置 ─────────────────────────────────────────────
REVIEW_DIMENSIONS = [
    "field_completeness",   # 字段完整性：必填字段是否全部提取
    "field_accuracy",       # 字段准确性：值是否与原文一致
    "document_classification",  # 文档分类：classify_document 是否匹配内容
    "archive_plan",         # 归档合理性：目标路径与文档类型是否匹配
    "cross_doc_consistency",    # 跨文档一致性（V1.1+）
]

# 必填字段列表（按文档类型）
REQUIRED_FIELDS = {
    "投标文件": ["project_name", "budget", "customer", "deadline"],
    "采购公告": ["project_name", "budget", "customer", "deadline"],
    "合同": ["project_name", "customer", "contract_status"],
    "未分类": ["project_name"],
}

# OCR 质量阈值：低于此值直接跳过对抗验证，升级为 needs_human_review
OCR_CONFIDENCE_THRESHOLD = 0.60


class ReviewerAgent:
    """Reviewer：审查提取结果，识别潜在问题"""

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

            # 维度2: 字段准确性（值是否在原文中出现）
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
            # 归档路径应包含项目名和文档类型
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
        # 尝试读取 sidecar OCR 或原始文本
        if file_path and os.path.exists(file_path):
            # 尝试读取 .ocr.txt sidecar
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
        # 清理值
        cleaned = str(value).replace(",", "").replace("万", "").replace("元", "").strip()
        if cleaned in text:
            return True
        # 尝试模糊匹配（数值可能拆分）
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


class DevilsAdvocateAgent:
    """Devil's Advocate：对 Reviewer 结论提出系统性反驳"""

    CHALLENGE_STRATEGIES = [
        "assumption_reversal",   # 假设反转
        "boundary_challenge",    # 边界挑战
        "omission_question",     # 遗漏质疑
        "logic_contradiction",   # 逻辑矛盾
        "confidence_attack",     # 置信度攻击
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

            # 模拟：如果 finding 的 confidence > 0.85，反驳强度降低
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


class DefenderAgent:
    """Defender：用原始文本证据为提取结果辩护（缓解 R1：能读原始文档）"""

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


class AdversarialVerification:
    """对抗性验证循环执行器：Reviewer → Devil's Advocate → Defender → Resolution"""

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
        """
        执行完整的三轮对抗性验证循环。
        
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
        
        规则：
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


# ── Error Case Collector ─────────────────────────────────────

ERROR_TYPES = [
    "field_extraction_incomplete",
    "field_extraction_inaccurate",
    "classification_error",
    "archive_plan_error",
    "cross_doc_inconsistency",
    "ocr_quality_issue",
]


class ErrorCaseCollector:
    """错误案例收集器：在 apply_human_review 中自动触发"""

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


# ── 归档门控检查辅助函数 ─────────────────────────────────────

def check_archive_gate(workspace_dir: str, run_id: str) -> Dict[str, Any]:
    """
    检查归档是否被对抗性验证阻断。
    
    缓解 R7：阻断消息含可读 finding 摘要。
    返回 dict，若 blocked 则包含详细阻断原因。
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
