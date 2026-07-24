"""AdversarialVerification 集成测试 + Frozen Snapshot 稳定性验证。

PRD §7.5.5 稳定性要求：
- frozen snapshot 测试：固定输入产生固定输出，10 次重放稳定率 ≥ 95%
- OCR 低置信度升级：直接 needs_human_review
"""
from tools.adversarial_verification import (
    OCR_CONFIDENCE_THRESHOLD,
    AdversarialVerification,
)


def test_ocr_low_confidence_escalates_to_human_review(tmp_workspace):
    """R2 缓解：OCR 置信度 < 阈值时直接升级 needs_human_review，跳过对抗验证。"""
    av = AdversarialVerification(workspace_dir=tmp_workspace)
    items = [{
        "file": "/tmp/low_quality.pdf",
        "filename": "low_quality.pdf",
        "document_type": "投标文件",
        "extracted_text": "模糊不清的内容",
        "fields": {"project_name": "合成项目005"},
        "ocr": {"pages": [{"confidence": 0.40}]},  # 低于 0.60 阈值
    }]
    result = av.run(run_id="test_run_001", extracted_items=items)

    assert result["overall_verdict"] == "needs_human_review"
    assert "OCR 置信度" in result["block_reason"]
    assert len(result["low_confidence_items"]) == 1
    # R2 应跳过对抗验证，三方报告都为空
    assert result["reviewer_report"]["finding_count"] == 0
    assert result["challenge_report"]["challenge_count"] == 0
    assert result["defense_report"]["defense_count"] == 0


def test_clean_input_passes(baseline_snapshot, tmp_workspace):
    """干净输入（frozen snapshot）应通过或 pass_with_warnings。"""
    av = AdversarialVerification(workspace_dir=tmp_workspace)
    snapshot_input = baseline_snapshot["_input"]
    expected = baseline_snapshot["_expected_output"]

    result = av.run(
        run_id="test_run_snapshot",
        extracted_items=snapshot_input["extracted_items"],
        archive_actions=snapshot_input["archive_actions"],
        ledger_results=snapshot_input["ledger_results"],
    )

    # frozen snapshot 稳定性：verdict 在可接受集合内
    assert result["overall_verdict"] in expected["_acceptable_verdicts"]
    # finding 数在可接受范围
    finding_count = result["reviewer_report"]["finding_count"]
    assert expected["min_finding_count"] <= finding_count <= expected["max_finding_count"]


def test_persistence_creates_files(baseline_snapshot, tmp_workspace):
    """R10 缓解：验证报告写入 runs/<run_id>/ + adversarial_verification/reports/。"""
    import os

    av = AdversarialVerification(workspace_dir=tmp_workspace)
    snapshot_input = baseline_snapshot["_input"]

    result = av.run(
        run_id="test_run_persist",
        extracted_items=snapshot_input["extracted_items"],
        archive_actions=snapshot_input["archive_actions"],
    )

    # 检查 runs/<run_id>/adversarial_verification.json
    run_report = os.path.join(tmp_workspace, "runs", "test_run_persist", "adversarial_verification.json")
    assert os.path.exists(run_report), f"应生成运行报告: {run_report}"

    # 检查月度归档
    archive_dir = os.path.join(tmp_workspace, "adversarial_verification", "reports")
    assert os.path.isdir(archive_dir), f"应生成月度归档目录: {archive_dir}"
    archive_files = os.listdir(archive_dir)
    assert len(archive_files) == 1, f"应有 1 个月度归档文件: {archive_files}"
    assert archive_files[0].endswith("_test_run_persist_verification.json")


def test_high_severity_findings_need_correction(tmp_workspace):
    """high severity confirmed finding → needs_correction。"""
    av = AdversarialVerification(workspace_dir=tmp_workspace)
    # 输入缺 3 个必填字段（project_name/budget/customer/deadline），reviewer 会判 high severity
    items = [{
        "file": "/tmp/bad.pdf",
        "filename": "bad.pdf",
        "document_type": "投标文件",
        "extracted_text": "少量内容",
        "fields": {"project_name": "合成项目005"},  # 只 1 个字段
        "ocr": {"pages": [{"confidence": 0.95}]},
    }]
    result = av.run(run_id="test_run_bad", extracted_items=items)

    # 应该被 reviewer 抓到 field_completeness high severity
    # Defender 没原始证据 → disputed
    # → 最终可能 needs_human_review 或 needs_correction
    assert result["overall_verdict"] in ("needs_correction", "needs_human_review")
    reviewer = result["reviewer_report"]
    assert reviewer["high_count"] >= 1, "应有 high severity finding"
