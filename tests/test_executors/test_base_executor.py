"""ParseExecutor 抽象基类 + helpers 测试。"""

import os
import tempfile
import unittest

from integrations.executors.base import ParseExecutor


class _TestExecutor(ParseExecutor):
    """最小实现,用于测试抽象基类。"""
    name = "test"
    def can_handle(self, source):
        return True
    def extract(self, source):
        return {
            "status": "success",
            "raw_data": {},
            "error": None,
            "implementation_status": "implemented",
        }
    def get_supported_types(self):
        return [".test"]


class TestParseExecutorHelpers(unittest.TestCase):

    def setUp(self):
        self.ex = _TestExecutor()

    # ---- _is_url ----

    def test_is_url_https(self):
        self.assertTrue(ParseExecutor._is_url("https://example.com"))

    def test_is_url_http(self):
        self.assertTrue(ParseExecutor._is_url("http://example.com/page"))

    def test_is_url_false_for_file_path(self):
        self.assertFalse(ParseExecutor._is_url("/tmp/file.docx"))

    def test_is_url_false_for_file_scheme(self):
        self.assertFalse(ParseExecutor._is_url("file:///tmp/file.docx"))

    # ---- _validate_url ----

    def test_validate_url_https_ok(self):
        ParseExecutor._validate_url("https://example.com/page")  # 不抛

    def test_validate_url_http_ok(self):
        ParseExecutor._validate_url("http://example.com")  # 不抛

    def test_validate_url_rejects_ftp(self):
        with self.assertRaises(ValueError) as ctx:
            ParseExecutor._validate_url("ftp://example.com")
        self.assertIn("http/https", str(ctx.exception))

    def test_validate_url_rejects_no_hostname(self):
        with self.assertRaises(ValueError) as ctx:
            ParseExecutor._validate_url("https:///just-a-path")
        self.assertIn("hostname", str(ctx.exception))

    # ---- _check_source_exists ----

    def test_check_source_exists_raises_for_missing(self):
        with self.assertRaises(FileNotFoundError):
            self.ex._check_source_exists("/nonexistent/file.docx")

    def test_check_source_exists_ok(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            self.ex._check_source_exists(path)  # 不抛
        finally:
            os.unlink(path)

    # ---- _blocked helper ----

    def test_blocked_default_impl_status(self):
        r = ParseExecutor._blocked("test error")
        self.assertEqual(r["status"], "blocked")
        self.assertEqual(r["error"], "test error")
        self.assertEqual(r["implementation_status"], "implemented")
        self.assertEqual(r["raw_data"], {})

    def test_blocked_explicit_stub_status(self):
        r = ParseExecutor._blocked("test", implementation_status="stub")
        self.assertEqual(r["implementation_status"], "stub")
