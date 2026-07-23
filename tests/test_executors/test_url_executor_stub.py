"""UrlExecutor stub 测试(接口契约)。"""

import unittest

from integrations.executors import UrlExecutor


class TestUrlExecutorStub(unittest.TestCase):

    def setUp(self):
        self.ex = UrlExecutor()

    def test_can_handle_https(self):
        self.assertTrue(self.ex.can_handle("https://example.com"))

    def test_can_handle_http(self):
        self.assertTrue(self.ex.can_handle("http://example.com/page.html"))

    def test_cannot_handle_file_path(self):
        self.assertFalse(self.ex.can_handle("/tmp/file.html"))
        self.assertFalse(self.ex.can_handle("/tmp/file.docx"))

    def test_cannot_handle_other_schemes(self):
        self.assertFalse(self.ex.can_handle("ftp://example.com"))
        self.assertFalse(self.ex.can_handle("file:///tmp/file.html"))

    def test_extract_returns_blocked_stub(self):
        result = self.ex.extract("https://example.com")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["implementation_status"], "stub")
        self.assertIn("not implemented", result["error"].lower())
        self.assertIn("TODO", result["error"])

    def test_extract_validates_url_scheme(self):
        result = self.ex.extract("ftp://example.com")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("http/https", result["error"])

    def test_get_supported_types_includes_url_schemes(self):
        types = self.ex.get_supported_types()
        self.assertIn("https://", types)
        self.assertIn("http://", types)
        self.assertIn(".html", types)
        self.assertIn(".htm", types)
