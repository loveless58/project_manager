"""PdfExecutor stub 测试(接口契约)。"""

import os
import tempfile
import unittest

from integrations.executors import PdfExecutor


class TestPdfExecutorStub(unittest.TestCase):

    def setUp(self):
        self.ex = PdfExecutor()
        self.path = None

    def tearDown(self):
        if self.path and os.path.exists(self.path):
            os.unlink(self.path)

    def test_can_handle_pdf(self):
        self.assertTrue(self.ex.can_handle("/tmp/a.pdf"))
        self.assertTrue(self.ex.can_handle("/tmp/a.PDF"))

    def test_cannot_handle_other(self):
        self.assertFalse(self.ex.can_handle("/tmp/a.docx"))
        self.assertFalse(self.ex.can_handle("/tmp/a.xlsx"))
        self.assertFalse(self.ex.can_handle("https://example.com/a.pdf"))

    def test_extract_returns_blocked_stub(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4\nfake")
            self.path = f.name
        result = self.ex.extract(self.path)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["implementation_status"], "stub")
        # 错误信息提及迁移路径
        self.assertIn("extract_pdf", result["error"])
        self.assertIn("OCR", result["error"])

    def test_extract_checks_source_exists(self):
        result = self.ex.extract("/nonexistent.pdf")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("not found", result["error"].lower())

    def test_get_supported_types(self):
        self.assertEqual(self.ex.get_supported_types(), [".pdf"])
