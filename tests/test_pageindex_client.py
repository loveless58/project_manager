"""
Tests for PageIndex client (integrations/pageindex/pageindex_client.py).

设计原则:
- 默认跑 fast tests (pure Python, 不调 LLM, 几秒)
- slow tests (调 PageIndex subprocess + LLM, 65s/次) 默认 skip,
  需要 PAGEINDEX_RUN_SLOW_TESTS=1 环境变量启用
"""
import json
import os
import sys
import unittest
from pathlib import Path

# 把 integrations/pageindex 加进 path
INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "integrations" / "pageindex"
sys.path.insert(0, str(INTEGRATIONS_DIR.parent))

from pageindex.pageindex_client import PageIndexClient, PageIndexError


FEDERAL_RESERVE_PDF = "/Users/zhang/Desktop/工作文件/PageIndex/examples/documents/2023-annual-report-truncated.pdf"
FEDERAL_RESERVE_CACHE = "/Users/zhang/Desktop/工作文件/PageIndex/results/2023-annual-report-truncated_structure.json"
TS_PDF = "/Users/zhang/Desktop/工作文件/TS软件外包云泰智汇电子26722V.pdf"

SLOW_TESTS_ENABLED = os.environ.get("PAGEINDEX_RUN_SLOW_TESTS", "0") == "1"


def _require_file(path: str):
    """Skip test if path doesn't exist."""
    if not os.path.isfile(path):
        raise unittest.SkipTest(f"File not found: {path}")


class TestParsePages(unittest.TestCase):
    """_parse_pages 纯逻辑测试, 无 IO, 无 subprocess。"""

    def setUp(self):
        self.client = PageIndexClient()

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
    """PageIndexClient 初始化 + 环境检查测试。"""

    def test_default_init_uses_expected_path(self):
        client = PageIndexClient()
        self.assertEqual(
            client.pageindex_dir,
            "/Users/zhang/Desktop/工作文件/PageIndex",
        )
        self.assertTrue(os.path.isfile(client.python_bin))
        self.assertTrue(os.path.isfile(client.cli_script))

    def test_bad_path_raises(self):
        with self.assertRaises(PageIndexError) as ctx:
            PageIndexClient(pageindex_dir="/nonexistent/path")
        self.assertIn("Python 解释器未找到", str(ctx.exception))

    def test_custom_path(self):
        client = PageIndexClient(pageindex_dir="/Users/zhang/Desktop/工作文件/PageIndex")
        self.assertEqual(client.pageindex_dir, "/Users/zhang/Desktop/工作文件/PageIndex")


class TestIndexPdf(unittest.TestCase):
    """index_pdf 输入验证 + 错误处理 (不调 LLM 的部分)。"""

    def setUp(self):
        self.client = PageIndexClient()

    def test_missing_file_returns_failed(self):
        result = self.client.index_pdf("/nonexistent/file.pdf")
        self.assertEqual(result["status"], "failed")
        self.assertIn("未找到", result["error"])

    def test_missing_md_file_returns_failed(self):
        result = self.client.index_md("/nonexistent/file.md")
        self.assertEqual(result["status"], "failed")
        self.assertIn("未找到", result["error"])

    def test_md_wrong_extension_returns_failed(self):
        result = self.client.index_md("/some/file.txt")
        self.assertEqual(result["status"], "failed")
        self.assertIn(".md", result["error"])


class TestGetPageContent(unittest.TestCase):
    """get_page_content 纯 Python 测试 (不调 LLM)。"""

    def setUp(self):
        self.client = PageIndexClient()

    def test_returns_expected_schema(self):
        _require_file(FEDERAL_RESERVE_PDF)
        pages = self.client.get_page_content(FEDERAL_RESERVE_PDF, "1")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page"], 1)
        self.assertIsInstance(pages[0]["content"], str)
        self.assertGreater(len(pages[0]["content"]), 100)  # Federal Reserve 文本型 PDF

    def test_returns_empty_for_scan_pdf(self):
        """扫描件 PDF 无文字层, 返回空 content (不是 bug, 是设计)。"""
        _require_file(TS_PDF)
        pages = self.client.get_page_content(TS_PDF, "1")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page"], 1)
        # 扫描件 PDF 没有文字层, content 长度 < 50
        self.assertLess(len(pages[0]["content"]), 50)

    def test_missing_file_raises(self):
        with self.assertRaises(PageIndexError):
            self.client.get_page_content("/nonexistent/file.pdf", "1")

    def test_page_range(self):
        _require_file(FEDERAL_RESERVE_PDF)
        pages = self.client.get_page_content(FEDERAL_RESERVE_PDF, "1-3")
        self.assertEqual(len(pages), 3)
        self.assertEqual([p["page"] for p in pages], [1, 2, 3])


class TestFindNodesByTitle(unittest.TestCase):
    """find_nodes_by_title 用 cache 的 structure.json 测试 (不调 LLM)。"""

    def setUp(self):
        self.client = PageIndexClient()
        _require_file(FEDERAL_RESERVE_CACHE)
        with open(FEDERAL_RESERVE_CACHE) as f:
            cached = json.load(f)
        self.index_result = {
            "doc_name": cached["doc_name"],
            "structure": cached["structure"],
            "structure_json_path": FEDERAL_RESERVE_CACHE,
        }

    def test_finds_monetary_node(self):
        matches = self.client.find_nodes_by_title(self.index_result, "Monetary")
        self.assertGreater(len(matches), 0)
        first = matches[0]
        self.assertIn("title", first)
        self.assertIn("node_id", first)
        self.assertIn("start_index", first)
        self.assertIn("end_index", first)
        self.assertIn("Monetary", first["title"])

    def test_finds_case_insensitive(self):
        matches = self.client.find_nodes_by_title(self.index_result, "monetary")
        self.assertGreater(len(matches), 0)

    def test_no_match_returns_empty(self):
        matches = self.client.find_nodes_by_title(self.index_result, "NonExistentKeyword123")
        self.assertEqual(len(matches), 0)

    def test_empty_structure_returns_empty(self):
        matches = self.client.find_nodes_by_title({"structure": []}, "anything")
        self.assertEqual(len(matches), 0)


@unittest.skipUnless(SLOW_TESTS_ENABLED, "set PAGEINDEX_RUN_SLOW_TESTS=1 to enable")
class TestIndexPdfSlow(unittest.TestCase):
    """慢测试: 真实 subprocess 调 PageIndex .venv + LLM, 单测 ~65s。"""

    def setUp(self):
        self.client = PageIndexClient()

    def test_index_federal_reserve_pdf(self):
        """Federal Reserve 50 页 PDF, 端到端跑通 index_pdf。"""
        _require_file(FEDERAL_RESERVE_PDF)
        result = self.client.index_pdf(FEDERAL_RESERVE_PDF)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["engine"], "pageindex")
        self.assertEqual(result["doc_name"], "2023-annual-report-truncated.pdf")
        self.assertTrue(len(result["doc_id"]) > 0)
        self.assertTrue(os.path.isfile(result["structure_json_path"]))
        self.assertGreaterEqual(len(result["structure"]), 3)  # 至少 3 个顶层节点

        # 验证顶层节点 schema
        first_node = result["structure"][0]
        for key in ("title", "node_id", "start_index", "end_index"):
            self.assertIn(key, first_node, f"missing key: {key}")

        # 验证耗时合理 (30-180s)
        self.assertGreater(result["elapsed_seconds"], 30)
        self.assertLess(result["elapsed_seconds"], 180)


if __name__ == "__main__":
    unittest.main()
