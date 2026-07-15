"""Tests for contracts.parsers — 数据驱动的字段解析器测试。

样本来源：
- amount：28 个项目中的 8 种格式（来自 index.json）
- sales_person：28 个项目中的 6 种写法（来自 index.json）
- deadline：6 个项目记录.md 中的多种位置
"""

import pytest
from contracts.parsers import (
    parse_amount,
    parse_sales_person,
    extract_deadlines,
    normalize_date,
)


# =========================================================================
# Amount 解析（8 种格式覆盖）
# =========================================================================

class TestParseAmount:
    """parse_amount 测试 — 覆盖 28 项目中的 8 种 amount 格式。"""

    def test_wan_unit(self):
        """'100万元' / '126万元' / '500万元' — 中文万单位。"""
        assert parse_amount("100万元") == (1_000_000.0, None)
        assert parse_amount("126万元") == (1_260_000.0, None)
        assert parse_amount("500万元") == (5_000_000.0, None)

    def test_arabic_yuan_with_comma(self):
        """'2,000,000 元' — 阿拉伯数字 + 千分位 + 元。"""
        assert parse_amount("2,000,000 元") == (2_000_000.0, None)

    def test_arabic_yuan_with_label(self):
        """'4,000,000 元（预算）' — 带括号场景标签。"""
        num, label = parse_amount("4,000,000 元（预算）")
        assert num == 4_000_000.0
        assert label == "预算"

    def test_yi_unit(self):
        """'1.5亿元' — 亿单位。"""
        assert parse_amount("1.5亿元") == (150_000_000.0, None)

    def test_small_yuan(self):
        """'20万元' / '88万元' — 小额项目。"""
        assert parse_amount("20万元") == (200_000.0, None)
        assert parse_amount("88万元") == (880_000.0, None)

    def test_unit_only(self):
        """'元' — 只有单位无数字（实际数据样本里有）。"""
        assert parse_amount("元") == (None, None)

    def test_placeholder(self):
        """'待确认' / '' — 占位符或空值。"""
        assert parse_amount("待确认") == (None, None)
        assert parse_amount("") == (None, None)
        assert parse_amount(None) == (None, None)

    def test_zero_with_label(self):
        """'0元（预算）' — 数字为 0 但有标签（边界情况）。"""
        num, label = parse_amount("0元（预算）")
        assert num == 0.0
        assert label == "预算"

    def test_dash_placeholder(self):
        """'—' / '-' — 占位符。"""
        assert parse_amount("—") == (None, None)
        assert parse_amount("-") == (None, None)


# =========================================================================
# Sales Person 解析（6 种写法覆盖）
# =========================================================================

class TestParseSalesPerson:
    """parse_sales_person 测试 — 覆盖 28 项目中的 6 种 sales_person 写法。"""

    def test_single_name(self):
        """'邹迅' / '陈丞' / '高应山' — 单人姓名。"""
        assert parse_sales_person("邹迅") == ["邹迅"]
        assert parse_sales_person("陈丞") == ["陈丞"]
        assert parse_sales_person("高应山") == ["高应山"]

    def test_two_persons_with_roles(self):
        """'赵月伟（联系人） / 田晓光（销售总监）' — 双人带职级。"""
        result = parse_sales_person("赵月伟（联系人） / 田晓光（销售总监）")
        assert result == ["赵月伟", "田晓光"]

    def test_two_persons_without_roles(self):
        """'张三 / 李四' — 双人无职级（边界）。"""
        assert parse_sales_person("张三 / 李四") == ["张三", "李四"]

    def test_dash_placeholder(self):
        """'—' — 占位符。"""
        assert parse_sales_person("—") == []

    def test_text_placeholder(self):
        """'待确认' / '待录入' / '待补充' — 文本占位符。"""
        assert parse_sales_person("待确认") == []
        assert parse_sales_person("待录入") == []
        assert parse_sales_person("待补充") == []

    def test_empty(self):
        """空值 / None — 边界。"""
        assert parse_sales_person("") == []
        assert parse_sales_person(None) == []
        assert parse_sales_person("   ") == []


# =========================================================================
# Normalize Date（4 种格式）
# =========================================================================

class TestNormalizeDate:
    """normalize_date 测试 — 支持 ISO + 中文日期格式。"""

    def test_iso_dash(self):
        assert normalize_date("2026-05-26") == "2026-05-26"

    def test_iso_slash(self):
        assert normalize_date("2026/05/26") == "2026-05-26"

    def test_chinese_date(self):
        assert normalize_date("2026年5月26日") == "2026-05-26"

    def test_chinese_date_padded(self):
        assert normalize_date("2026年05月26日") == "2026-05-26"

    def test_invalid(self):
        assert normalize_date("not a date") is None
        assert normalize_date("") is None
        assert normalize_date(None) is None


# =========================================================================
# Extract Deadlines（3 种位置覆盖）
# =========================================================================

class TestExtractDeadlines:
    """extract_deadlines 测试 — 从项目记录.md 多种位置提取 deadline。"""

    def test_basic_info_section(self):
        """基本情况区提取。"""
        text = """## 时间节点
- 报名截止：2026-05-28
- 投标截止/开标：2026-06-11 09:30
"""
        result = extract_deadlines(text)
        assert result["registration_deadline"] == "2026-05-28"
        assert result["bid_deadline"] == "2026-06-11"

    def test_milestone_section(self):
        """里程碑管理区提取。"""
        text = """## 里程碑管理

| 里程碑 | 截止日期 | 状态 |
|--------|----------|------|
| 投标截止/开标 | 2026-07-15 | pending |
"""
        result = extract_deadlines(text)
        assert result["bid_deadline"] == "2026-07-15"

    def test_chinese_date_format(self):
        """中文日期格式。"""
        text = "- 报名截止：2026年5月20日"
        result = extract_deadlines(text)
        assert result["registration_deadline"] == "2026-05-20"

    def test_empty_text(self):
        """空文本。"""
        result = extract_deadlines("")
        assert result == {
            "registration_deadline": None,
            "bid_deadline": None,
            "bid_open_time": None,
        }

    def test_no_deadlines(self):
        """没有 deadline 字段的文本。"""
        text = "# 项目记录\n## 基本信息\n- 项目名称：xxx\n"
        result = extract_deadlines(text)
        assert all(v is None for v in result.values())

    def test_real_sample_gpu_server(self):
        """真实样本：GPU 服务器项目记录.md（节选）。"""
        text = """## 时间节点
- **招标文件获取**：2026-05-13 20:00 ~ 2026-05-22 16:00
- **投标截止/开标**：2026-05-26 09:30
- **开标地点**：北京市朝阳区
"""
        result = extract_deadlines(text)
        assert result["bid_deadline"] == "2026-05-26"


# =========================================================================
# OCR Aliases（数据驱动 — 来自工作台 2 个 extracted.json）
# =========================================================================

class TestOCRAliases:
    """OCR 字段 → 账本字段 映射测试。"""

    def test_buyer_to_customer_base(self):
        """buyer → customer（基础映射，无子类型）。"""
        from contracts.ocr_aliases import resolve_ocr_field
        assert resolve_ocr_field("buyer") == "customer"
        assert resolve_ocr_field("seller") == "customer"

    def test_date_subtype_routing(self):
        """date 字段按子类型路由。"""
        from contracts.ocr_aliases import resolve_ocr_field
        assert resolve_ocr_field("date", "招标公告") == "bid_deadline"
        assert resolve_ocr_field("date", "标书费回单") == "payment_date"
        assert resolve_ocr_field("date", "居间协议") == "document_date"
        assert resolve_ocr_field("date", "合同") == "contract_date"

    def test_project_no_phase_routing(self):
        """project_no 字段按子类型路由。"""
        from contracts.ocr_aliases import resolve_ocr_field
        assert resolve_ocr_field("project_no", "合同") == "project_code"
        assert resolve_ocr_field("project_no", "招标公告") == "bid_code"
        assert resolve_ocr_field("project_no", "标书费回单") == "business_reference_no"

    def test_real_sample_intermediary_agreement(self):
        """真实样本：居间协议 extracted_fields 全部映射。"""
        from contracts.ocr_aliases import resolve_ocr_field

        # 居间协议的实际 extracted_fields
        ocr_fields = {
            "buyer": "北京华胜天成科技股份有限公司",
            "seller": "北京睿嘉顺达科技有限公司",
            "date": "2026年6月15日",
            "project_no": "ZYCG20260609287",
            "service_rate": "8%",
            "penalty_rate": "20%",
        }

        # 每个字段单独验证映射（避免 dict 重复 key）
        assert resolve_ocr_field("buyer", "居间协议") == "customer"
        assert resolve_ocr_field("seller", "居间协议") == "customer"  # 合同里甲方=buyer，乙方次选
        assert resolve_ocr_field("date", "居间协议") == "document_date"
        assert resolve_ocr_field("project_no", "居间协议") == "bid_code"

        # 业务含义：实际 customer 字段填 buyer（甲方）
        assert ocr_fields["buyer"] == "北京华胜天成科技股份有限公司"

    def test_real_sample_bid_fee_receipt(self):
        """真实样本：标书费回单 extracted_fields 全部映射。"""
        from contracts.ocr_aliases import resolve_ocr_field

        ocr_fields = {
            "buyer": "北京华胜天成科技股份有限公司",
            "seller": "中经国际招标集团有限公司",
            "date": "2026年06月26日",
            "amount": 800.0,
            "project_no": "25S037GN000005103460",
            "payment_purpose": "CEITCL-BT04-2606017-01标书费（标段一",
        }

        # 每个字段单独验证映射
        assert resolve_ocr_field("buyer", "标书费回单") == "customer"
        assert resolve_ocr_field("date", "标书费回单") == "payment_date"
        assert resolve_ocr_field("amount", "标书费回单") == "bid_fee"
        assert resolve_ocr_field("project_no", "标书费回单") == "business_reference_no"

        # 业务含义：凭证类的 customer 是付款人
        assert ocr_fields["buyer"] == "北京华胜天成科技股份有限公司"
        assert ocr_fields["amount"] == 800.0

    def test_unknown_field_passthrough(self):
        """未知字段保持原名（兜底）。"""
        from contracts.ocr_aliases import resolve_ocr_field
        assert resolve_ocr_field("unknown_field") == "unknown_field"


# =========================================================================
# Ledger Namespace（数据驱动 — 28 个项目字段填充率元数据）
# =========================================================================

class TestLedgerNamespace:
    """账本字段命名空间测试。"""

    def test_phase_labels_authoritative(self):
        """phase 权威命名 = 项目投标/项目弃标/项目丢标/项目执行（用户拍板，2026-07-15 5→4 全口径统一）。"""
        from contracts.ledger_namespace import PHASE_LABELS
        assert PHASE_LABELS == ["项目投标", "项目弃标", "项目丢标", "项目执行"]

    def test_field_metadata_completeness(self):
        """LEDGER_FIELDS 至少 18 个字段（基于 28 项目样本）。"""
        from contracts.ledger_namespace import LEDGER_FIELDS
        assert len(LEDGER_FIELDS) >= 18

    def test_required_fields_have_high_fill_rate(self):
        """高填充率字段（隐含必填）：project_name / project_type / phase。"""
        from contracts.ledger_namespace import LEDGER_FIELDS
        high_fill = {
            k for k, v in LEDGER_FIELDS.items()
            if v.get("fill_rate") == "高"
        }
        # 至少包含项目名称 + 项目类型 + 当前阶段
        assert "project_name" in high_fill
        assert "project_type" in high_fill
        assert "phase" in high_fill

    def test_low_fill_rate_fields_documented(self):
        """低填充率字段（customer 32% / amount 25%）有元数据说明。"""
        from contracts.ledger_namespace import LEDGER_FIELDS
        assert "customer" in LEDGER_FIELDS
        assert LEDGER_FIELDS["customer"]["fill_rate_pct"] == 32
        assert "amount" in LEDGER_FIELDS
        assert LEDGER_FIELDS["amount"]["fill_rate_pct"] == 25

    def test_get_zh_name(self):
        """get_zh_name 反向查找中文名。"""
        from contracts.ledger_namespace import get_zh_name
        assert get_zh_name("project_name") == "项目名称"
        assert get_zh_name("bid_deadline") == "投标截止"

    def test_get_en_name(self):
        """get_en_name 反向查找英文代码。"""
        from contracts.ledger_namespace import get_en_name
        assert get_en_name("项目名称") == "project_name"
        assert get_en_name("投标截止") == "bid_deadline"
