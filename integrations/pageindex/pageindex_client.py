"""Client for the optional external PageIndex CLI.

The client separates configuration from capability validation and exposes only
stable, sanitized failures at its public boundary.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


_ERROR_MESSAGES = {
    "PAGEINDEX.CONFIG.MISSING": (
        "pageindex_dir must be configured explicitly through AppSettings"
    ),
    "PAGEINDEX.INPUT.PDF_NOT_FOUND": "PDF file not found.",
    "PAGEINDEX.INPUT.MARKDOWN_EXTENSION": (
        "Markdown file must use a .md or .markdown extension."
    ),
    "PAGEINDEX.INPUT.MARKDOWN_NOT_FOUND": "Markdown file not found.",
    "PAGEINDEX.RUNTIME.PYTHON_UNAVAILABLE": (
        "PageIndex Python interpreter is unavailable."
    ),
    "PAGEINDEX.RUNTIME.CLI_UNAVAILABLE": "PageIndex CLI script is unavailable.",
    "PAGEINDEX.RUNTIME.PROBE_TIMEOUT": "PageIndex runtime probe timed out.",
    "PAGEINDEX.RUNTIME.PROBE_START_FAILED": (
        "PageIndex runtime probe could not be started."
    ),
    "PAGEINDEX.RUNTIME.PROBE_FAILED": "PageIndex runtime probe failed.",
    "PAGEINDEX.RUNTIME.UNAVAILABLE": "PageIndex runtime is unavailable.",
    "PAGEINDEX.EXECUTION.TIMEOUT": "PageIndex execution timed out.",
    "PAGEINDEX.EXECUTION.START_FAILED": (
        "PageIndex execution could not be started."
    ),
    "PAGEINDEX.EXECUTION.FAILED": "PageIndex execution failed.",
    "PAGEINDEX.RESULT.MISSING": "PageIndex structure result was not generated.",
    "PAGEINDEX.RESULT.DECODE_FAILED": (
        "PageIndex structure result could not be decoded."
    ),
    "PAGEINDEX.RESULT.READ_FAILED": "PageIndex structure result could not be read.",
    "PAGEINDEX.RESULT.INVALID_JSON": "PageIndex structure result is invalid.",
    "PAGEINDEX.CONTENT.PDF_NOT_FOUND": "PDF file not found.",
    "PAGEINDEX.CONTENT.READ_FAILED": "PDF content could not be read.",
}


class PageIndexError(Exception):
    """Stable error raised when a local PageIndex helper cannot proceed."""

    def __init__(
        self,
        message: str,
        error_code: str = "PAGEINDEX.RUNTIME.UNAVAILABLE",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


class PageIndexClient:
    """PageIndex adapter with pure-Python retrieval and traversal helpers."""

    runtime_probe_timeout_seconds = 5

    def __init__(
        self,
        pageindex_dir: Optional[str] = None,
        timeout_seconds: int = 600,
    ) -> None:
        if not pageindex_dir:
            code = "PAGEINDEX.CONFIG.MISSING"
            raise PageIndexError(_ERROR_MESSAGES[code], code)
        self.pageindex_dir = str(Path(pageindex_dir).expanduser().resolve())
        venv_bin = "Scripts" if os.name == "nt" else "bin"
        python_name = "python.exe" if os.name == "nt" else "python"
        self.python_bin = os.path.join(
            self.pageindex_dir, ".venv", venv_bin, python_name
        )
        self.cli_script = os.path.join(self.pageindex_dir, "run_pageindex.py")
        self.timeout_seconds = timeout_seconds

    def check_environment(self) -> None:
        """Validate that the external PageIndex runtime can start safely."""
        if not os.path.isfile(self.python_bin):
            code = "PAGEINDEX.RUNTIME.PYTHON_UNAVAILABLE"
            raise PageIndexError(_ERROR_MESSAGES[code], code)
        if not os.path.isfile(self.cli_script):
            code = "PAGEINDEX.RUNTIME.CLI_UNAVAILABLE"
            raise PageIndexError(_ERROR_MESSAGES[code], code)
        try:
            process = subprocess.run(
                [self.python_bin, "--version"],
                cwd=self.pageindex_dir,
                capture_output=True,
                text=True,
                timeout=min(self.timeout_seconds, self.runtime_probe_timeout_seconds),
            )
        except subprocess.TimeoutExpired:
            code = "PAGEINDEX.RUNTIME.PROBE_TIMEOUT"
            raise PageIndexError(_ERROR_MESSAGES[code], code) from None
        except (OSError, UnicodeError):
            code = "PAGEINDEX.RUNTIME.PROBE_START_FAILED"
            raise PageIndexError(_ERROR_MESSAGES[code], code) from None
        if process.returncode != 0:
            code = "PAGEINDEX.RUNTIME.PROBE_FAILED"
            raise PageIndexError(_ERROR_MESSAGES[code], code)

    def index_pdf(
        self,
        pdf_path: str,
        model: Optional[str] = None,
        toc_check_pages: Optional[int] = None,
        max_pages_per_node: Optional[int] = None,
        max_tokens_per_node: Optional[int] = None,
        if_add_node_id: str = "yes",
        if_add_node_summary: str = "yes",
        if_add_doc_description: str = "yes",
        if_add_node_text: str = "yes",
        if_thinning: str = "no",
        thinning_threshold: int = 5000,
        summary_token_threshold: int = 200,
    ) -> Dict[str, Any]:
        """Index a PDF through the external PageIndex CLI."""
        if not os.path.isfile(pdf_path):
            return self._failed("PAGEINDEX.INPUT.PDF_NOT_FOUND")
        environment_failure = self._environment_failure()
        if environment_failure is not None:
            return environment_failure

        command = [self.python_bin, self.cli_script, "--pdf_path", pdf_path]
        options = {
            "model": model,
            "toc_check_pages": toc_check_pages,
            "max_pages_per_node": max_pages_per_node,
            "max_tokens_per_node": max_tokens_per_node,
            "if_add_node_id": if_add_node_id,
            "if_add_node_summary": if_add_node_summary,
            "if_add_doc_description": if_add_doc_description,
            "if_add_node_text": if_add_node_text,
            "if_thinning": if_thinning,
            "thinning_threshold": thinning_threshold,
            "summary_token_threshold": summary_token_threshold,
        }
        self._append_options(command, options)
        return self._run_index(command, pdf_path)

    def index_md(self, md_path: str, **kwargs: Any) -> Dict[str, Any]:
        """Index a Markdown document through the external PageIndex CLI."""
        if not md_path.lower().endswith((".md", ".markdown")):
            return self._failed("PAGEINDEX.INPUT.MARKDOWN_EXTENSION")
        if not os.path.isfile(md_path):
            return self._failed("PAGEINDEX.INPUT.MARKDOWN_NOT_FOUND")
        environment_failure = self._environment_failure()
        if environment_failure is not None:
            return environment_failure

        command = [self.python_bin, self.cli_script, "--md_path", md_path]
        supported = {
            "if_add_node_id",
            "if_add_node_summary",
            "if_add_doc_description",
            "if_add_node_text",
            "if_thinning",
            "thinning_threshold",
            "summary_token_threshold",
            "model",
        }
        self._append_options(
            command, {key: value for key, value in kwargs.items() if key in supported}
        )
        return self._run_index(command, md_path)

    def _environment_failure(self) -> Optional[Dict[str, Any]]:
        try:
            self.check_environment()
        except PageIndexError as exc:
            code = (
                exc.error_code
                if exc.error_code in _ERROR_MESSAGES
                else "PAGEINDEX.RUNTIME.UNAVAILABLE"
            )
            return self._failed(code)
        return None

    @staticmethod
    def _append_options(command: List[str], options: Dict[str, Any]) -> None:
        for key, value in options.items():
            if value is not None:
                command.extend([f"--{key.replace('_', '-')}", str(value)])

    @staticmethod
    def _failed(error_code: str, elapsed_seconds: float = 0.0) -> Dict[str, Any]:
        return {
            "status": "failed",
            "engine": "pageindex",
            "error_code": error_code,
            "error": _ERROR_MESSAGES.get(
                error_code,
                "PageIndex provider failed.",
            ),
            "elapsed_seconds": elapsed_seconds,
        }

    def _run_index(self, command: List[str], source_path: str) -> Dict[str, Any]:
        start = time.time()
        try:
            process = subprocess.run(
                command,
                cwd=self.pageindex_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return self._failed(
                "PAGEINDEX.EXECUTION.TIMEOUT",
                round(time.time() - start, 2),
            )
        except (OSError, UnicodeError):
            return self._failed(
                "PAGEINDEX.EXECUTION.START_FAILED",
                round(time.time() - start, 2),
            )

        elapsed = round(time.time() - start, 2)
        if process.returncode != 0:
            return self._failed("PAGEINDEX.EXECUTION.FAILED", elapsed)

        source_name = os.path.splitext(os.path.basename(source_path))[0]
        structure_json_path = os.path.join(
            self.pageindex_dir, "results", f"{source_name}_structure.json"
        )
        if not os.path.isfile(structure_json_path):
            return self._failed("PAGEINDEX.RESULT.MISSING", elapsed)
        try:
            with open(structure_json_path, "r", encoding="utf-8") as source:
                data = json.load(source)
        except UnicodeError:
            return self._failed("PAGEINDEX.RESULT.DECODE_FAILED", elapsed)
        except OSError:
            return self._failed("PAGEINDEX.RESULT.READ_FAILED", elapsed)
        except json.JSONDecodeError:
            return self._failed("PAGEINDEX.RESULT.INVALID_JSON", elapsed)

        return {
            "status": "success",
            "engine": "pageindex",
            "doc_name": data.get("doc_name", source_name),
            "doc_id": str(uuid.uuid4()),
            "structure": data.get("structure", []),
            "structure_json_path": structure_json_path,
            "elapsed_seconds": elapsed,
        }

    def get_page_content(self, pdf_path: str, pages: str) -> List[Dict[str, Any]]:
        """Read PDF text without requiring a PageIndex CLI environment."""
        if not os.path.isfile(pdf_path):
            code = "PAGEINDEX.CONTENT.PDF_NOT_FOUND"
            raise PageIndexError(_ERROR_MESSAGES[code], code)
        page_numbers = self._parse_pages(pages)
        try:
            return self._get_pdf_page_content_pypdf2(pdf_path, page_numbers)
        except Exception:
            try:
                return self._get_pdf_page_content_pymupdf(pdf_path, page_numbers)
            except Exception:
                code = "PAGEINDEX.CONTENT.READ_FAILED"
                raise PageIndexError(_ERROR_MESSAGES[code], code) from None

    def get_page_content_from_index(
        self, index_result: Dict[str, Any], pages: str
    ) -> List[Dict[str, Any]]:
        structure_json_path = index_result["structure_json_path"]
        pdf_path = structure_json_path.replace("_structure.json", ".pdf")
        if not os.path.isfile(pdf_path):
            pdf_path = os.path.join(
                os.path.dirname(structure_json_path), f"{index_result['doc_name']}.pdf"
            )
        return self.get_page_content(pdf_path, pages)

    @staticmethod
    def find_nodes_by_title(
        index_result: Dict[str, Any], keyword: str
    ) -> List[Dict[str, Any]]:
        """Traverse an existing structure without creating or probing a client."""
        pattern = re.compile(keyword, re.IGNORECASE)
        matches: List[Dict[str, Any]] = []

        def traverse(nodes: List[Dict[str, Any]]) -> None:
            for node in nodes:
                if pattern.search(node.get("title", "")):
                    matches.append(
                        {
                            "title": node.get("title"),
                            "node_id": node.get("node_id"),
                            "start_index": node.get("start_index"),
                            "end_index": node.get("end_index"),
                            "summary": node.get("summary"),
                        }
                    )
                children = node.get("nodes")
                if children:
                    traverse(children)

        traverse(index_result.get("structure", []))
        return matches

    @staticmethod
    def _parse_pages(pages: str) -> List[int]:
        result: List[int] = []
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                start_text, end_text = part.split("-", 1)
                start, end = int(start_text.strip()), int(end_text.strip())
                if start > end:
                    raise ValueError("Invalid page range: start must be <= end")
                result.extend(range(start, end + 1))
            else:
                result.append(int(part))
        return sorted(set(result))

    @staticmethod
    def _get_pdf_page_content_pypdf2(
        pdf_path: str, page_numbers: List[int]
    ) -> List[Dict[str, Any]]:
        import PyPDF2

        with open(pdf_path, "rb") as source:
            reader = PyPDF2.PdfReader(source)
            total = len(reader.pages)
            valid_pages = [page for page in page_numbers if 1 <= page <= total]
            return [
                {"page": page, "content": reader.pages[page - 1].extract_text() or ""}
                for page in valid_pages
            ]

    @staticmethod
    def _get_pdf_page_content_pymupdf(
        pdf_path: str, page_numbers: List[int]
    ) -> List[Dict[str, Any]]:
        import fitz

        document = fitz.open(pdf_path)
        try:
            valid_pages = [
                page for page in page_numbers if 1 <= page <= document.page_count
            ]
            return [
                {"page": page, "content": document.load_page(page - 1).get_text() or ""}
                for page in valid_pages
            ]
        finally:
            document.close()
