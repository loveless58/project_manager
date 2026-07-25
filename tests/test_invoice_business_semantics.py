import os


def test_invoice_item_is_not_business_project(tmp_path):
    """Changing invoice item parsing to generic project extraction must fail this test."""
    from tools.data_cleaning_tools import DataCleaningTools

    text = (
        "电子发票\n"
        "购买方名称：合成甲方有限公司\n"
        "销售方名称：合成乙方有限公司\n"
        "项目名称 规格型号\n"
        "技术服务 标准版"
    )

    fields = DataCleaningTools(workspace_dir=str(tmp_path))._extract_fields_for_document(text, "发票")

    assert "project_name" not in fields
    assert "project_code" not in fields
    assert "deadline" not in fields
    assert "bid_status" not in fields
    assert "lifecycle_stage" not in fields
    assert fields["buyer"]["name"] == "合成甲方有限公司"
    assert fields["seller"]["name"] == "合成乙方有限公司"
    assert fields["line_items"][0]["item_name"] == "技术服务"
    assert fields["line_items"][0]["specification"] == "标准版"


def test_project_governance_markdown_reuses_parsed_classification_for_archive_plan(tmp_path):
    """Changing archive planning to reclassify a parsed governance document must fail this test."""
    from business_rules.archive_decision import evaluate_archive_decision
    from tools.data_cleaning_tools import DataCleaningTools

    source = tmp_path / "PRD-project-manager-ocr.md"
    source.write_text("# PRD\nproject_manager classification design", encoding="utf-8")
    tools = DataCleaningTools(workspace_dir=str(tmp_path / "workspace"))

    extracted = tools.extract_document(str(source))
    parsed_classification = extracted["classification"]
    decision = evaluate_archive_decision(
        source_file=str(source),
        extracted=extracted,
        business_judgement={},
        project_files_dir=os.path.join(str(tmp_path), "项目文件"),
    )

    assert parsed_classification == {
        "document_type": "项目治理文档",
        "business_domain": "internal_project",
        "project_phase": None,
        "archive_phase": None,
        "confidence": 0.98,
        "evidence": ["filename:PRD-project-manager-ocr.md", "text:project_manager"],
        "requires_review": True,
    }
    assert decision["classification"] == parsed_classification
    assert decision["document_type"] == parsed_classification["document_type"]
    assert decision["archive_phase"] == parsed_classification["archive_phase"]

def test_invoice_inline_fields_respect_label_boundaries():
    from business_rules.invoice_fields import extract_invoice_fields

    fields = extract_invoice_fields(
        "发票号码：SYN-INVOICE-001 | 开票日期：2026-07-25 | 购买方名称：合成甲方有限公司 | 购买方税号：SYN-BUY-001\n"
        "销售方名称：合成乙方有限公司 | 销售方税号：SYN-SELL-001"
    )

    assert fields["invoice_number"] == "SYN-INVOICE-001"
    assert fields["invoice_date"] == "2026-07-25"
    assert fields["buyer"] == {"name": "合成甲方有限公司", "tax_id": "SYN-BUY-001"}
    assert fields["seller"] == {"name": "合成乙方有限公司", "tax_id": "SYN-SELL-001"}


def test_invoice_table_keeps_all_items_and_skips_separator_and_total():
    from business_rules.invoice_fields import extract_invoice_fields

    fields = extract_invoice_fields(
        "| 项目名称 | 规格型号 | 金额 |\n"
        "| --- | --- | --- |\n"
        "| 技术服务 | 标准版 | 100.00 |\n"
        "| 实施服务 | 高级版 | 200.00 |\n"
        "| 合计 | | 300.00 |"
    )

    assert [item["item_name"] for item in fields["line_items"]] == ["技术服务", "实施服务"]
    assert [item["specification"] for item in fields["line_items"]] == ["标准版", "高级版"]


def test_archive_blocks_missing_or_malformed_classification():
    from business_rules.archive_decision import evaluate_archive_decision

    decision = evaluate_archive_decision(
        source_file="PRD-project-manager-ocr.md",
        extracted={"filename": "PRD-project-manager-ocr.md", "document_type": "发票", "extract_method": "metadata_passthrough", "fields": {"project_name": "合成项目"}, "classification": {"document_type": "发票", "business_domain": "finance", "project_phase": [], "archive_phase": [], "confidence": 4, "evidence": "bad", "requires_review": False}},
        business_judgement={},
        project_files_dir="/tmp/project-files",
    )

    assert decision["target_path"] is None
    assert decision["human_review_required"] is True
    assert "invalid_document_classification" in decision["blockers"]


def test_archive_obeys_passed_classification_not_filename_or_text():
    from business_rules.archive_decision import evaluate_archive_decision

    classification = {"document_type": "发票", "business_domain": "finance", "project_phase": None, "archive_phase": None, "confidence": 0.91, "evidence": ["test"], "requires_review": True}
    decision = evaluate_archive_decision(
        source_file="PRD-project-manager-ocr.md",
        extracted={"filename": "PRD-project-manager-ocr.md", "extracted_text": "project_manager", "document_type": "项目治理文档", "fields": {}, "classification": classification},
        business_judgement={},
        project_files_dir="/tmp/project-files",
    )

    assert decision["classification"] == classification
    assert decision["document_type"] == "发票"
    assert decision["subject_type"] != "internal_project"


def test_invoice_split_label_rows_do_not_cross_field_boundaries():
    from business_rules.invoice_fields import extract_invoice_fields

    fields = extract_invoice_fields(
        "发票号码\nSYN-INVOICE-002\n开票日期\n2026年7月25日\n"
        "购买方名称\n合成甲方有限公司\n购买方税号\nSYN-BUY-002\n"
        "销售方名称\n合成乙方有限公司\n销售方税号\nSYN-SELL-002"
    )

    assert fields["invoice_number"] == "SYN-INVOICE-002"
    assert fields["invoice_date"] == "2026-07-25"
    assert fields["buyer"] == {"name": "合成甲方有限公司", "tax_id": "SYN-BUY-002"}
    assert fields["seller"] == {"name": "合成乙方有限公司", "tax_id": "SYN-SELL-002"}
