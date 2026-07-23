"""Fast unit tests for the PageIndex client.

The PageIndex CLI is an optional external dependency.  These tests therefore
exercise input validation and pure helpers without requiring a local runtime.
Slow end-to-end tests are enabled only when all explicit environment variables
are supplied by the caller.
"""

import json
import os
import sys
import unittest
from pathlib import Path

import pytest


INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "integrations" / "pageindex"
sys.path.insert(0, str(INTEGRATIONS_DIR.parent))

from pageindex.pageindex_client import PageIndexClient, PageIndexError


PAGEINDEX_TEST_DIR = os.environ.get("PAGEINDEX_TEST_DIR", "")
FEDERAL_RESERVE_PDF = os.environ.get("PAGEINDEX_TEST_PDF", "")
FEDERAL_RESERVE_CACHE = os.environ.get("PAGEINDEX_TEST_CACHE", "")
TS_PDF = os.environ.get("PAGEINDEX_TEST_SCAN_PDF", "")
SLOW_TESTS_ENABLED = os.environ.get("PAGEINDEX_RUN_SLOW_TESTS", "0") == "1"


def _require_file(path: str) -> None:
    if not os.path.isfile(path):
        raise unittest.SkipTest(f"File not found: {path}")


def _client() -> PageIndexClient:
    """Create a client for pure/helper tests without probing PageIndex."""
    return PageIndexClient(pageindex_dir=str(Path.cwd()))


class TestParsePages(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_parse_single_page(self):
        self.assertEqual(self.client._parse_pages("12"), [12])

    def test_parse_range(self):
        self.assertEqual(self.client._parse_pages("5-7"), [5, 6, 7])

    def test_parse_comma_separated(self):
        self.assertEqual(self.client._parse_pages("3,8"), [3, 8])

    def test_parse_complex_format(self):
        self.assertEqual(self.client._parse_pages("1-3,5,8-10"), [1, 2, 3, 5, 8, 9, 10])

    def test_parse_strips_whitespace(self):
        self.assertEqual(self.client._parse_pages(" 5 , 8 "), [5, 8])

    def test_parse_invalid_range_raises(self):
        with self.assertRaises(ValueError):
            self.client._parse_pages("10-5")

    def test_parse_duplicate_removed(self):
        self.assertEqual(self.client._parse_pages("5,5,5"), [5])


class TestEnvironment(unittest.TestCase):
    def test_pageindex_client_requires_explicit_directory(self):
        with pytest.raises(PageIndexError, match="pageindex_dir must be configured"):
            PageIndexClient()

    def test_constructor_defers_environment_validation(self):
        client = PageIndexClient(pageindex_dir="/nonexistent/path")

        with self.assertRaises(PageIndexError) as context:
            client.check_environment()

        self.assertIn("Python", str(context.exception))

    def test_custom_path_is_resolved(self):
        configured = str(Path.cwd())
        client = PageIndexClient(pageindex_dir=configured)
        self.assertEqual(client.pageindex_dir, str(Path(configured).resolve()))


class TestIndexInputValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_missing_pdf_returns_failed_before_environment_check(self):
        result = self.client.index_pdf("/nonexistent/file.pdf")

        self.assertEqual(result["status"], "failed")
        self.assertIn("not found", result["error"].lower())

    def test_missing_markdown_returns_failed_before_environment_check(self):
        result = self.client.index_md("/nonexistent/file.md")

        self.assertEqual(result["status"], "failed")
        self.assertIn("not found", result["error"].lower())

    def test_markdown_wrong_extension_returns_failed_before_environment_check(self):
        result = self.client.index_md("/some/file.txt")

        self.assertEqual(result["status"], "failed")
        self.assertIn(".md", result["error"])


class TestPureHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_missing_pdf_for_content_raises_without_environment_check(self):
        with self.assertRaises(PageIndexError):
            self.client.get_page_content("/nonexistent/file.pdf", "1")

    def test_find_nodes_by_title_does_not_require_pageindex_runtime(self):
        index_result = {
            "structure": [
                {
                    "title": "Monetary Policy",
                    "node_id": "0001",
                    "start_index": 1,
                    "end_index": 2,
                    "summary": "summary",
                    "nodes": [
                        {
                            "title": "Operations",
                            "node_id": "0002",
                            "start_index": 3,
                            "end_index": 4,
                            "summary": "nested",
                        }
                    ],
                }
            ]
        }

        matches = self.client.find_nodes_by_title(index_result, "monetary|operations")

        self.assertEqual([node["node_id"] for node in matches], ["0001", "0002"])


@unittest.skipUnless(SLOW_TESTS_ENABLED, "set PAGEINDEX_RUN_SLOW_TESTS=1 to enable")
class TestIndexPdfSlow(unittest.TestCase):
    def setUp(self) -> None:
        if not PAGEINDEX_TEST_DIR:
            raise unittest.SkipTest("set PAGEINDEX_TEST_DIR for PageIndex slow tests")
        self.client = PageIndexClient(PAGEINDEX_TEST_DIR)

    def test_index_federal_reserve_pdf(self):
        _require_file(FEDERAL_RESERVE_PDF)
        result = self.client.index_pdf(FEDERAL_RESERVE_PDF)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["engine"], "pageindex")
        self.assertTrue(result["doc_id"])
        self.assertTrue(os.path.isfile(result["structure_json_path"]))

    def test_read_cached_structure(self):
        _require_file(FEDERAL_RESERVE_CACHE)
        with open(FEDERAL_RESERVE_CACHE, encoding="utf-8") as source:
            cached = json.load(source)

        matches = self.client.find_nodes_by_title(
            {"structure": cached["structure"]}, "Monetary"
        )

        self.assertGreater(len(matches), 0)

    def test_reads_scan_pdf_page_content(self):
        _require_file(TS_PDF)
        pages = self.client.get_page_content(TS_PDF, "1")

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page"], 1)


if __name__ == "__main__":
    unittest.main()
