"""XlsxExecutor 真实样本测试。"""

import os
import tempfile
import unittest

import openpyxl

from integrations.executors import XlsxExecutor


class TestXlsxExecutor(unittest.TestCase):

    def setUp(self):
        self.ex = XlsxExecutor()
        self.path = None

    def tearDown(self):
        if self.path and os.path.exists(self.path):
            os.unlink(self.path)

    def _create_test_xlsx(self, sheets_data):
        """sheets_data: [{name, rows}, ...]"""
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            self.path = f.name
        wb = openpyxl.Workbook()
        # 删除默认 sheet
        wb.remove(wb.active)
        for sd in sheets_data:
            ws = wb.create_sheet(sd["name"])
            for row in sd["rows"]:
                ws.append(row)
        wb.save(self.path)

    # ---- can_handle ----

    def test_can_handle_xlsx(self):
        self.assertTrue(self.ex.can_handle("/tmp/a.xlsx"))

    def test_cannot_handle_docx(self):
        self.assertFalse(self.ex.can_handle("/tmp/a.docx"))

    def test_cannot_handle_url(self):
        self.assertFalse(self.ex.can_handle("https://example.com/a.xlsx"))

    # ---- extract single sheet ----

    def test_extract_single_sheet(self):
        self._create_test_xlsx([{
            "name": "项目清单",
            "rows": [["项目", "金额"], ["A", "100万"]],
        }])
        result = self.ex.extract(self.path)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["implementation_status"], "implemented")
        self.assertEqual(len(result["raw_data"]["sheets"]), 1)
        self.assertEqual(result["raw_data"]["sheets"][0]["name"], "项目清单")
        self.assertEqual(len(result["raw_data"]["sheets"][0]["rows"]), 2)

    # ---- extract multi sheets ----

    def test_extract_multi_sheets(self):
        self._create_test_xlsx([
            {"name": "项目清单", "rows": [["项目A", "100万"]]},
            {"name": "投标人", "rows": [["公司X", "95万"]]},
        ])
        result = self.ex.extract(self.path)
        self.assertEqual(len(result["raw_data"]["sheets"]), 2)
        self.assertEqual(result["raw_data"]["metadata"]["sheet_count"], 2)
        self.assertEqual(result["raw_data"]["metadata"]["sheet_names"],
                         ["项目清单", "投标人"])
        # paragraphs 展平
        self.assertEqual(len(result["raw_data"]["paragraphs"]), 2)

    # ---- extract metadata ----

    def test_extract_metadata(self):
        self._create_test_xlsx([{
            "name": "S1", "rows": [["a", "b"]],
        }])
        result = self.ex.extract(self.path)
        self.assertIn("sheet_count", result["raw_data"]["metadata"])
        self.assertIn("file_size", result["raw_data"]["metadata"])
        self.assertGreater(result["raw_data"]["metadata"]["file_size"], 0)

    # ---- extract error ----

    def test_extract_file_not_found(self):
        result = self.ex.extract("/nonexistent.xlsx")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("not found", result["error"].lower())

    def test_extract_corrupted_file(self):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(b"not a real xlsx")
            self.path = f.name
        result = self.ex.extract(self.path)
        self.assertEqual(result["status"], "blocked")

    # ---- get_supported_types ----

    def test_get_supported_types(self):
        self.assertEqual(self.ex.get_supported_types(), [".xlsx"])
