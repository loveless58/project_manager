#!/usr/bin/env python3
import builtins
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TEST_DIR)
sys.path.insert(0, PROJECT_DIR)


class TestFileReadiness(unittest.TestCase):
    def test_readable_file_returns_ready(self):
        from common.file_readiness import probe_readable_file

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source.txt"
            path.write_text("hello", encoding="utf-8")

            result = probe_readable_file(str(path))

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["exists"])
        self.assertTrue(result["is_file"])
        self.assertTrue(result["readable"])
        self.assertEqual(result["blocked_reason"], "")

    def test_missing_file_returns_source_missing(self):
        from common.file_readiness import probe_readable_file

        result = probe_readable_file(r"E:\SynologyDrive\missing.pdf")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_reason"], "source_missing")
        self.assertFalse(result["exists"])

    def test_directory_as_file_returns_not_file(self):
        from common.file_readiness import probe_readable_file

        with tempfile.TemporaryDirectory() as td:
            result = probe_readable_file(td)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_reason"], "source_not_file")
        self.assertTrue(result["exists"])
        self.assertFalse(result["is_file"])

    def test_cloud_placeholder_error_returns_cloud_blocked_reason(self):
        from common.file_readiness import probe_readable_file

        real_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if str(path).endswith("placeholder.pdf"):
                raise OSError("The cloud operation was unsuccessful.")
            return real_open(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "placeholder.pdf"
            path.write_bytes(b"placeholder")
            with patch("builtins.open", fake_open):
                result = probe_readable_file(str(path))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_reason"], "cloud_placeholder_or_sync_failure")
        self.assertFalse(result["readable"])
        self.assertIn("cloud operation", result["error"].lower())

    def test_writable_dir_probe_returns_ready(self):
        from common.file_readiness import probe_writable_dir

        with tempfile.TemporaryDirectory() as td:
            result = probe_writable_dir(td)

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["writable"])


if __name__ == "__main__":
    unittest.main()
