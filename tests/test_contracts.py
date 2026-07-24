import unittest
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ContractParserTests(unittest.TestCase):
    def test_parse_amount_normalizes_common_project_amount_formats(self):
        from contracts.parsers import parse_amount

        self.assertEqual(parse_amount("100万元"), (1_000_000.0, None))
        self.assertEqual(parse_amount("2,000,000 元"), (2_000_000.0, None))
        self.assertEqual(parse_amount("1.5亿元"), (150_000_000.0, None))
        self.assertEqual(parse_amount("待确认"), (None, None))
        self.assertEqual(parse_amount("—"), (None, None))

        amount, label = parse_amount("4,000,000 元（预算）")
        self.assertEqual(amount, 4_000_000.0)
        self.assertEqual(label, "预算")

    def test_parse_sales_person_normalizes_single_and_multi_owner_values(self):
        from contracts.parsers import parse_sales_person

        self.assertEqual(parse_sales_person("虚构甲"), ["虚构甲"])
        self.assertEqual(parse_sales_person("虚构乙 / 虚构丙"), ["虚构乙", "虚构丙"])
        self.assertEqual(parse_sales_person("虚构丁（联系人） / 虚构戊（销售总监）"), ["虚构丁", "虚构戊"])
        self.assertEqual(parse_sales_person("待确认"), [])
        self.assertEqual(parse_sales_person("—"), [])

    def test_extract_deadlines_from_project_record_markdown(self):
        from contracts.parsers import extract_deadlines, normalize_date

        self.assertEqual(normalize_date("2026年5月6日"), "2026-05-06")
        text = """
## 时间节点
- 报名截止：2026-05-20
- 投标截止/开标：2026-05-26 09:30

## 里程碑管理
| 里程碑 | 截止日期 | 状态 |
| 报名截止 | 2026-05-20 | pending |
| 投标截止/开标 | 2026-05-26 | pending |
"""

        result = extract_deadlines(text)

        self.assertEqual(result["registration_deadline"], "2026-05-20")
        self.assertEqual(result["bid_deadline"], "2026-05-26")
        self.assertEqual(result["bid_open_time"], "2026-05-26")


class ContractAliasTests(unittest.TestCase):
    def test_resolve_ocr_field_routes_date_and_project_number_by_subtype(self):
        from contracts.ocr_aliases import resolve_ocr_field

        self.assertEqual(resolve_ocr_field("buyer"), "customer")
        self.assertEqual(resolve_ocr_field("date", "招标公告"), "bid_deadline")
        self.assertEqual(resolve_ocr_field("date", "标书费回单"), "payment_date")
        self.assertEqual(resolve_ocr_field("project_no", "合同"), "project_code")
        self.assertEqual(resolve_ocr_field("project_no", "招标公告"), "bid_code")
        self.assertEqual(resolve_ocr_field("unknown_field"), "unknown_field")

    def test_ledger_namespace_exposes_authoritative_phase_and_field_metadata(self):
        from contracts.ledger_namespace import (
            LEDGER_FIELDS,
            PHASE_LABELS,
            get_en_name,
            get_zh_name,
        )

        self.assertEqual(
            PHASE_LABELS,
            ["项目投标", "项目弃标", "项目丢标", "项目执行"],
        )
        self.assertGreaterEqual(len(LEDGER_FIELDS), 18)
        self.assertEqual(get_zh_name("project_name"), "项目名称")
        self.assertEqual(get_en_name("投标截止"), "bid_deadline")
        self.assertEqual(LEDGER_FIELDS["customer"]["fill_rate_pct"], 32)


if __name__ == "__main__":
    unittest.main()
