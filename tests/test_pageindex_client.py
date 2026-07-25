"""Fast unit tests for the PageIndex client.

The PageIndex CLI is an optional external dependency. These tests therefore
exercise input validation and pure helpers without requiring a local runtime.
Slow end-to-end tests are enabled only when all explicit environment variables
are supplied by the caller.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import pytest


INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "integrations" / "pageindex"
sys.path.insert(0, str(INTEGRATIONS_DIR.parent))

from pageindex.pageindex_client import PageIndexClient, PageIndexError


@pytest.fixture(autouse=True)
def _explicit_pdf_page_count_adapter(monkeypatch):
    """Keep external-boundary doubles independent of the physical PDF parser."""
    monkeypatch.setattr(
        PageIndexClient, "_pdf_page_count", staticmethod(lambda path: 1)
    )


SLOW_TESTS_ENABLED = os.environ.get("PAGEINDEX_RUN_SLOW_TESTS", "0") == "1"
SLOW_TEST_PATH_TYPES = {
    "PAGEINDEX_TEST_DIR": "directory",
    "PAGEINDEX_TEST_PDF": "file",
    "PAGEINDEX_TEST_CACHE": "file",
    "PAGEINDEX_TEST_SCAN_PDF": "file",
}


class PageIndexSlowTestConfigurationError(RuntimeError):
    """Raised when explicitly enabled PageIndex integration tests lack inputs."""


def _client() -> PageIndexClient:
    """Create a client for pure/helper tests without probing PageIndex."""
    return PageIndexClient(pageindex_dir=str(Path.cwd()))


def _client_with_runtime_files(tmp_path: Path) -> PageIndexClient:
    client = PageIndexClient(pageindex_dir=str(tmp_path))
    python_bin = Path(client.python_bin)
    python_bin.parent.mkdir(parents=True)
    python_bin.touch()
    Path(client.cli_script).touch()
    return client


def validate_slow_test_configuration(environ=None):
    environment = os.environ if environ is None else environ
    configured = {}
    errors = []
    for variable, expected_type in SLOW_TEST_PATH_TYPES.items():
        raw_value = environment.get(variable, "").strip()
        if not raw_value:
            errors.append(f"{variable} must be set")
            continue
        path = Path(raw_value).expanduser().resolve()
        is_expected_type = path.is_dir() if expected_type == "directory" else path.is_file()
        if not is_expected_type:
            errors.append(f"{variable} must be an existing {expected_type}: {path}")
            continue
        configured[variable] = path
    if errors:
        raise PageIndexSlowTestConfigurationError(
            "Invalid PageIndex slow-test configuration:\n- " + "\n- ".join(errors)
        )
    return configured


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


def test_check_environment_rejects_runtime_that_cannot_start(tmp_path, monkeypatch):
    client = _client_with_runtime_files(tmp_path)
    calls = []

    def cannot_start(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("broken executable")

    monkeypatch.setattr(subprocess, "run", cannot_start)

    with pytest.raises(PageIndexError) as error:
        client.check_environment()

    assert str(error.value) == "PageIndex runtime probe could not be started."
    assert str(tmp_path) not in str(error.value)
    assert len(calls) == 1


def test_index_pdf_returns_stable_failure_when_runtime_cannot_start(tmp_path, monkeypatch):
    client = _client_with_runtime_files(tmp_path)
    source_path = tmp_path / "source.pdf"
    source_path.touch()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python 3.12", "")
        raise OSError("broken executable")

    monkeypatch.setattr(subprocess, "run", run)

    result = client.index_pdf(str(source_path))

    assert result["status"] == "failed"
    assert result["error"] == "PageIndex execution could not be started."
    assert str(tmp_path) not in result["error"]
    assert len(calls) == 2


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
    @classmethod
    def setUpClass(cls) -> None:
        configuration = validate_slow_test_configuration()
        cls.client = PageIndexClient(str(configuration["PAGEINDEX_TEST_DIR"]))
        cls.pdf_path = str(configuration["PAGEINDEX_TEST_PDF"])
        cls.cache_path = configuration["PAGEINDEX_TEST_CACHE"]
        cls.scan_pdf_path = str(configuration["PAGEINDEX_TEST_SCAN_PDF"])

    def test_index_federal_reserve_pdf(self):
        result = self.client.index_pdf(self.pdf_path)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["engine"], "pageindex")
        self.assertTrue(result["doc_id"])
        self.assertTrue(os.path.isfile(result["structure_json_path"]))

    def test_read_cached_structure(self):
        with self.cache_path.open(encoding="utf-8") as source:
            cached = json.load(source)

        matches = self.client.find_nodes_by_title(
            {"structure": cached["structure"]}, "Monetary"
        )

        self.assertGreater(len(matches), 0)

    def test_reads_scan_pdf_page_content(self):
        pages = self.client.get_page_content(self.scan_pdf_path, "1")

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page"], 1)


if __name__ == "__main__":
    unittest.main()
