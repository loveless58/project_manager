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
