"""WpsExecutor stub 测试(接口契约)。"""

import os
import tempfile
import unittest

from integrations.executors import WpsExecutor


class TestWpsExecutorStub(unittest.TestCase):

    def setUp(self):
        self.ex = WpsExecutor()
        self.path = None

    def tearDown(self):
        if self.path and os.path.exists(self.path):
            os.unlink(self.path)

    def test_can_handle_wps(self):
        self.assertTrue(self.ex.can_handle("/tmp/a.wps"))
        self.assertTrue(self.ex.can_handle("/tmp/a.WPS"))

    def test_cannot_handle_other(self):
        self.assertFalse(self.ex.can_handle("/tmp/a.docx"))
        self.assertFalse(self.ex.can_handle("/tmp/a.xlsx"))
        self.assertFalse(self.ex.can_handle("https://example.com/a.wps"))

    def test_extract_returns_blocked_stub(self):
        with tempfile.NamedTemporaryFile(suffix=".wps", delete=False) as f:
            f.write(b"fake wps content")
            self.path = f.name
        result = self.ex.extract(self.path)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["implementation_status"], "stub")
        # 错误信息包含选型选项
        self.assertIn("antiword", result["error"])
        self.assertIn("LibreOffice", result["error"])

    def test_extract_checks_source_exists(self):
        result = self.ex.extract("/nonexistent.wps")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("not found", result["error"].lower())

    def test_get_supported_types(self):
        self.assertEqual(self.ex.get_supported_types(), [".wps"])
