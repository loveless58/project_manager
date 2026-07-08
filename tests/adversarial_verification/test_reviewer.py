"""Reviewer Agent 单元测试。

验证 4 个审查维度在对应问题场景下能产出 finding：
1. field_completeness: 缺字段时产出 finding
2. field_accuracy: 数值不在原文时产出 finding
3. document_classification: 文件名推断与分类不一致时产出 finding
4. archive_plan: 归档路径缺项目名时产出 finding
"""
from tools.adversarial_verification import REQUIRED_FIELDS, ReviewerAgent


def test_required_fields_present():
    """验证 4 类文档的必填字段配置正确。"""
    assert "project_name" in REQUIRED_FIELDS["投标文件"]
    assert "budget" in REQUIRED_FIELDS["投标文件"]
    assert "contract_status" in REQUIRED_FIELDS["合同"]
    assert REQUIRED_FIELDS["未分类"] == ["project_name"]


def test_reviewer_detects_missing_fields():
    """维度1: 字段完整性 — 缺 project_name 和 budget 时产出 high severity finding。"""
    reviewer = ReviewerAgent(workspace_dir="/tmp")
    items = [{
        "file": "/tmp/test.pdf",
        "filename": "test.pdf",
        "document_type": "投标文件",
        "extracted_text": "随机内容",
        "fields": {},  # 完全缺字段
        "ocr": {"pages": [{"confidence": 0.95}]},
    }]
    report = reviewer.review(extracted_items=items, archive_actions=[], ledger_results=[])

    assert report["agent"] == "reviewer"
    assert report["finding_count"] >= 1
    completeness_findings = [
        f for f in report["findings"] if f["dimension"] == "field_completeness"
    ]
    assert len(completeness_findings) == 1
    # 缺 4 个字段（project_name/budget/customer/deadline）→ high severity
    assert completeness_findings[0]["severity"] == "high"
    assert "project_name" in completeness_findings[0]["details"]["missing_fields"]
    assert "budget" in completeness_findings[0]["details"]["missing_fields"]


def test_reviewer_detects_value_not_in_text():
    """维度2: 字段准确性 — budget 值在原文中找不到时产出 finding。"""
    reviewer = ReviewerAgent(workspace_dir="/tmp")
    # deadline 用完整日期格式（2026-09-30）满足 reviewer 日期正则要求，
    # 避免与 budget 测试目标混淆。
    items = [{
        "file": "/tmp/test.pdf",
        "filename": "test.pdf",
        "document_type": "投标文件",
        "extracted_text": "项目名称：测试\n客户：某公司\n截止日期：2026-09-30",  # 无 budget 信息
        "fields": {
            "project_name": "测试",
            "budget": "999",  # 原文中找不到
            "customer": "某公司",
            "deadline": "2026-09-30",
        },
        "ocr": {"pages": [{"confidence": 0.95}]},
    }]
    report = reviewer.review(extracted_items=items, archive_actions=[], ledger_results=[])

    accuracy_findings = [
        f for f in report["findings"] if f["dimension"] == "field_accuracy"
    ]
    # 应只有 1 个 budget 准确性 finding（deadline 格式合法，不会被标记）
    assert len(accuracy_findings) == 1
    assert accuracy_findings[0]["details"]["field"] == "budget"
    assert accuracy_findings[0]["details"]["value"] == "999"


def test_reviewer_detects_classification_mismatch():
    """维度3: 文档分类 — 文件名含"投标"但分类为"合同"时产出 finding。"""
    reviewer = ReviewerAgent(workspace_dir="/tmp")
    items = [{
        "file": "/tmp/test.pdf",
        "filename": "某项目投标书.pdf",  # 文件名暗示投标文件
        "document_type": "合同",  # 但分类为合同
        "extracted_text": "内容",
        "fields": {"project_name": "测试"},
        "ocr": {"pages": [{"confidence": 0.95}]},
    }]
    report = reviewer.review(extracted_items=items, archive_actions=[], ledger_results=[])

    classification_findings = [
        f for f in report["findings"] if f["dimension"] == "document_classification"
    ]
    assert len(classification_findings) == 1
    assert classification_findings[0]["details"]["predicted"] == "合同"
    assert "投标" in classification_findings[0]["details"]["inferred_from_name"]


def test_reviewer_detects_archive_path_missing_project_name():
    """维度4: 归档合理性 — 归档路径不含项目名时产出 finding。"""
    reviewer = ReviewerAgent(workspace_dir="/tmp")
    items = []
    actions = [{
        "source_file": "/tmp/test.pdf",
        "target_path": "/some/random/path/test.pdf",  # 不含项目名
        "document_type": "投标文件",
        "project_name": "测试项目",
    }]
    report = reviewer.review(extracted_items=items, archive_actions=actions, ledger_results=[])

    archive_findings = [
        f for f in report["findings"] if f["dimension"] == "archive_plan"
    ]
    assert len(archive_findings) == 1
    assert "缺少项目名" in archive_findings[0]["message"]


def test_reviewer_clean_input_produces_minimal_findings():
    """干净输入（字段完整 + 数值在文中 + 分类一致 + 归档路径含项目名）几乎无 finding。"""
    reviewer = ReviewerAgent(workspace_dir="/tmp")
    # deadline 用完整日期格式（2026-09-30），避免 reviewer 的日期格式误报。
    items = [{
        "file": "/tmp/clean.pdf",
        "filename": "clean_bid.pdf",
        "document_type": "投标文件",
        "extracted_text": "项目：某项目 预算：100万 客户：某公司 截止：2026年9月30日",
        "fields": {
            "project_name": "某项目",
            "budget": "100",
            "customer": "某公司",
            "deadline": "2026年9月30日",
        },
        "ocr": {"pages": [{"confidence": 0.95}]},
    }]
    actions = [{
        "source_file": "/tmp/clean.pdf",
        "target_path": "/项目文件/某项目/投标/clean_bid.pdf",
        "document_type": "投标文件",
        "project_name": "某项目",
    }]
    report = reviewer.review(extracted_items=items, archive_actions=actions, ledger_results=[])

    # 干净输入可能产出 0 finding，或低 severity finding（如 archive_plan）
    high_or_medium = [
        f for f in report["findings"]
        if f["severity"] in ("high", "medium")
    ]
    assert len(high_or_medium) == 0, f"干净输入不应产出中高严重度 finding: {high_or_medium}"
