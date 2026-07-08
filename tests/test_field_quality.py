import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from business_rules.field_quality import filter_business_facts, required_fields_for_facts


class FieldQualityTests(unittest.TestCase):
    def test_sales_owner_rejects_table_and_label_noise(self):
        for value in ["日      期： | 审核意见：⑭", "联 系 人：", "按合同约定完成验收并确认付款"]:
            accepted, quality = filter_business_facts({"project_name": "测试项目", "sales_owner": value})

            self.assertNotIn("sales_owner", accepted)
            self.assertTrue(any(item["field"] == "sales_owner" for item in quality["rejected_fields"]))

    def test_sales_owner_accepts_short_chinese_name(self):
        accepted, quality = filter_business_facts({"project_name": "测试项目", "sales_owner": "邹迅"})

        self.assertEqual(accepted["sales_owner"], "邹迅")
        self.assertFalse(any(item["field"] == "sales_owner" for item in quality["rejected_fields"]))

    def test_customer_name_normalizes_org_prefix_and_rejects_sentences(self):
        accepted, quality = filter_business_facts({
            "project_name": "测试项目",
            "customer_name": "甲方：郑州银行股份有限公司",
            "customer": "确认为中标标的。",
        })

        self.assertEqual(accepted["customer_name"], "郑州银行股份有限公司")
        self.assertTrue(any(item["field"] == "customer" for item in quality["rejected_fields"]))

    def test_customer_name_rejects_table_role_noise(self):
        accepted, quality = filter_business_facts({
            "project_name": "测试项目",
            "customer_name": "建设单位 国际防务学院教研保障中心 监理单位 北京赛迪时代信息产业股份有限公司",
        })

        self.assertNotIn("customer_name", accepted)
        self.assertTrue(any(item["field"] == "customer_name" for item in quality["rejected_fields"]))

    def test_execution_stage_requires_only_stage_evidence(self):
        required = required_fields_for_facts({
            "project_name": "执行项目",
            "lifecycle_stage": "execution",
        })

        self.assertEqual(required, ["lifecycle_stage", "project_name"])


if __name__ == "__main__":
    unittest.main()
