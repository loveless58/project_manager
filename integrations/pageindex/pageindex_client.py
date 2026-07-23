"""PageIndex 通用客户端(fork of VectifyAI/PageIndex, 本地 fork 复制)。

提供 3 个核心能力:
- index_pdf() / index_md() → 生成结构化 tree (调用 PageIndex CLI via subprocess)
- get_page_content() → 按页码范围获取 PDF 文本 (纯 Python, 不依赖 PageIndex .venv)

实现要点:
- subprocess 调 PageIndex .venv/bin/python + run_pageindex.py
- cwd 必须为 PageIndex 目录(否则 import pageindex 失败 + results/ 相对路径错位)
- 查询路径用项目主环境的 PyPDF2 / PyMuPDF(已装), 避免 PageIndex .venv 依赖
- 进程启动开销 ~200ms, 但单次 index 通常 30-60s, 启动开销可忽略

注意: 本地 PageIndex 仓库是 fork 复制 (git history 丢失),
包含 prompt injection 加固独有改动, 详见 project_manager/docs/pageindex-baseline.md (后续建)。
"""
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class PageIndexError(Exception):
    """PageIndex 客户端错误。"""


class PageIndexClient:
    """PageIndex 通用客户端。"""

    DEFAULT_PAGEINDEX_DIR = "/Users/zhang/Desktop/工作文件/PageIndex"

    def __init__(
        self,
        pageindex_dir: Optional[str] = None,
        timeout_seconds: int = 600,
    ):
        """初始化 PageIndex 客户端。

        Args:
            pageindex_dir: PageIndex 仓库绝对路径, 默认指向 /Users/zhang/Desktop/工作文件/PageIndex
            timeout_seconds: subprocess timeout, 默认 600s (50 页 PDF 约 55s)
        """
        self.pageindex_dir = pageindex_dir or self.DEFAULT_PAGEINDEX_DIR
        self.python_bin = os.path.join(self.pageindex_dir, ".venv", "bin", "python")
        self.cli_script = os.path.join(self.pageindex_dir, "run_pageindex.py")
        self.timeout_seconds = timeout_seconds
        self._verify_environment()

    def _verify_environment(self) -> None:
        """检查 PageIndex 仓库依赖。"""
        if not os.path.isfile(self.python_bin):
            raise PageIndexError(
                f"PageIndex Python 解释器未找到: {self.python_bin}。\n"
                f"请先在 {self.pageindex_dir} 下创建 venv 并安装依赖: "
                f"python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"
            )
        if not os.path.isfile(self.cli_script):
            raise PageIndexError(
                f"PageIndex CLI 脚本未找到: {self.cli_script}。\n"
                f"请检查 PageIndex 仓库是否完整。"
            )

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
        """对 PDF 文件生成结构化 tree。

        Args:
            pdf_path: PDF 绝对路径
            model: 覆盖 config.yaml 中的 model 字段 (默认 None, 用 config.yaml)
            toc_check_pages: TOC 检查页数 (默认 20)
            max_pages_per_node: 单节点最大页数
            max_tokens_per_node: 单节点最大 token
            if_add_node_id / if_add_node_summary / if_add_doc_description / if_add_node_text: 是否添加对应字段
            if_thinning: 是否做 thinning (yes/no)
            thinning_threshold / summary_token_threshold: 对应阈值

        Returns:
            {
                "status": "success" | "failed",
                "engine": "pageindex",
                "doc_name": str,
                "doc_id": str,  # 自动生成 UUID
                "structure": [...],  # tree 结构, 每个节点含 title/node_id/start_index/end_index/summary/nodes
                "structure_json_path": str,  # PageIndex/results/xxx_structure.json 绝对路径
                "elapsed_seconds": float,
                "error": str (only if failed)
            }
        """
        if not os.path.isfile(pdf_path):
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"PDF 文件未找到: {pdf_path}",
                "elapsed_seconds": 0.0,
            }

        # 构造 CLI 命令
        cmd = [self.python_bin, self.cli_script, "--pdf_path", pdf_path]
        opt_map = {
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
        for k, v in opt_map.items():
            if v is not None:
                cmd.extend([f"--{k.replace('_', '-')}", str(v)])

        start = time.time()
        try:
            # cwd=PageIndex_dir: 让 import pageindex 工作 + results/ 相对路径正确
            proc = subprocess.run(
                cmd,
                cwd=self.pageindex_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"PageIndex timeout ({self.timeout_seconds}s): {pdf_path}",
                "elapsed_seconds": round(time.time() - start, 2),
            }
        elapsed = round(time.time() - start, 2)

        if proc.returncode != 0:
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"PageIndex exited with code {proc.returncode}: {proc.stderr[:500]}",
                "elapsed_seconds": elapsed,
            }

        # PageIndex 把结果写到 ./results/{pdf_name}_structure.json (相对 PageIndex 目录)
        pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
        structure_json_path = os.path.join(
            self.pageindex_dir, "results", f"{pdf_name}_structure.json"
        )

        if not os.path.isfile(structure_json_path):
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"structure.json 未生成: {structure_json_path}",
                "elapsed_seconds": elapsed,
            }

        try:
            with open(structure_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"structure.json 解析失败: {e}",
                "elapsed_seconds": elapsed,
            }

        return {
            "status": "success",
            "engine": "pageindex",
            "doc_name": data.get("doc_name", pdf_name),
            "doc_id": str(uuid.uuid4()),
            "structure": data.get("structure", []),
            "structure_json_path": structure_json_path,
            "elapsed_seconds": elapsed,
        }

    def index_md(self, md_path: str, **kwargs) -> Dict[str, Any]:
        """对 Markdown 文件生成结构化 tree。

        参数同 index_pdf (去掉 toc_check_pages / max_pages_per_node / max_tokens_per_node)。
        """
        # 先检查后缀 (快速失败, 错误信息更明确)
        if not md_path.lower().endswith((".md", ".markdown")):
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"Markdown 文件必须是 .md / .markdown 后缀: {md_path}",
                "elapsed_seconds": 0.0,
            }
        if not os.path.isfile(md_path):
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"Markdown 文件未找到: {md_path}",
                "elapsed_seconds": 0.0,
            }

        cmd = [self.python_bin, self.cli_script, "--md_path", md_path]
        md_specific = {"if_add_node_id", "if_add_node_summary", "if_add_doc_description", "if_add_node_text",
                       "if_thinning", "thinning_threshold", "summary_token_threshold", "model"}
        for k, v in kwargs.items():
            if v is not None and k in md_specific:
                cmd.extend([f"--{k.replace('_', '-')}", str(v)])

        start = time.time()
        try:
            proc = subprocess.run(
                cmd, cwd=self.pageindex_dir, capture_output=True, text=True, timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"PageIndex timeout ({self.timeout_seconds}s): {md_path}",
                "elapsed_seconds": round(time.time() - start, 2),
            }
        elapsed = round(time.time() - start, 2)

        if proc.returncode != 0:
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"PageIndex exited with code {proc.returncode}: {proc.stderr[:500]}",
                "elapsed_seconds": elapsed,
            }

        md_name = os.path.splitext(os.path.basename(md_path))[0]
        structure_json_path = os.path.join(
            self.pageindex_dir, "results", f"{md_name}_structure.json"
        )
        if not os.path.isfile(structure_json_path):
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"structure.json 未生成: {structure_json_path}",
                "elapsed_seconds": elapsed,
            }
        try:
            with open(structure_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return {
                "status": "failed",
                "engine": "pageindex",
                "error": f"structure.json 解析失败: {e}",
                "elapsed_seconds": elapsed,
            }

        return {
            "status": "success",
            "engine": "pageindex",
            "doc_name": data.get("doc_name", md_name),
            "doc_id": str(uuid.uuid4()),
            "structure": data.get("structure", []),
            "structure_json_path": structure_json_path,
            "elapsed_seconds": elapsed,
        }

    def get_page_content(
        self,
        pdf_path: str,
        pages: str,
    ) -> List[Dict[str, Any]]:
        """按页码范围获取 PDF 文本内容(纯 Python, 不依赖 PageIndex .venv)。

        Args:
            pdf_path: PDF 绝对路径
            pages: 页码字符串, 支持 '5-7' / '3,8' / '12' / '1-3,5,8-10' 格式

        Returns:
            [{"page": int, "content": str}, ...]

        Raises:
            PageIndexError: PDF 读取失败或页码格式错误
        """
        if not os.path.isfile(pdf_path):
            raise PageIndexError(f"PDF 文件未找到: {pdf_path}")

        page_nums = self._parse_pages(pages)

        # 优先用 PyPDF2 (项目主环境已装 3.0.1), 失败时回退 PyMuPDF
        try:
            return self._get_pdf_page_content_pypdf2(pdf_path, page_nums)
        except Exception:
            return self._get_pdf_page_content_pymupdf(pdf_path, page_nums)

    def get_page_content_from_index(
        self,
        index_result: Dict[str, Any],
        pages: str,
    ) -> List[Dict[str, Any]]:
        """从 index_pdf 生成的 index_result 中读 PDF 内容。

        Args:
            index_result: index_pdf() 返回的 dict (含 doc_name 和 structure_json_path)
            pages: 页码字符串, 格式同 get_page_content

        Returns:
            [{"page": int, "content": str}, ...]

        Note:
            从 structure_json_path 推断 pdf_path (同目录同名 .pdf)。
            如果 pdf_path 已移动或改名, 请直接用 get_page_content(pdf_path, pages)。
        """
        structure_json_path = index_result["structure_json_path"]
        pdf_path = structure_json_path.replace("_structure.json", ".pdf")
        if not os.path.isfile(pdf_path):
            # 退化: 用 doc_name 推
            doc_name = index_result["doc_name"]
            pdf_path = os.path.join(os.path.dirname(structure_json_path), f"{doc_name}.pdf")
        return self.get_page_content(pdf_path, pages)

    def find_nodes_by_title(
        self,
        index_result: Dict[str, Any],
        keyword: str,
    ) -> List[Dict[str, Any]]:
        """在结构中按 title 关键词找节点(深度优先遍历)。

        Args:
            index_result: index_pdf() 返回的 dict
            keyword: title 关键词(支持正则)

        Returns:
            匹配的节点列表, 每个含 title/node_id/start_index/end_index/summary
        """
        pattern = re.compile(keyword, re.IGNORECASE)
        matches = []

        def _traverse(nodes: List[Dict[str, Any]]) -> None:
            for node in nodes:
                if pattern.search(node.get("title", "")):
                    matches.append({
                        "title": node.get("title"),
                        "node_id": node.get("node_id"),
                        "start_index": node.get("start_index"),
                        "end_index": node.get("end_index"),
                        "summary": node.get("summary"),
                    })
                if node.get("nodes"):
                    _traverse(node["nodes"])

        _traverse(index_result.get("structure", []))
        return matches

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_pages(pages: str) -> List[int]:
        """Parse '5-7' / '3,8' / '12' 格式的页码字符串, 返回排序去重的页码列表。

        复刻自 pageindex/retrieve.py:_parse_pages (避免 PageIndex .venv 依赖)。
        """
        result = []
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                start, end = int(start_str.strip()), int(end_str.strip())
                if start > end:
                    raise ValueError(f"Invalid range '{part}': start must be <= end")
                result.extend(range(start, end + 1))
            else:
                result.append(int(part))
        return sorted(set(result))

    @staticmethod
    def _get_pdf_page_content_pypdf2(pdf_path: str, page_nums: List[int]) -> List[Dict[str, Any]]:
        """PyPDF2 路径读 PDF 页内容。"""
        import PyPDF2
        with open(pdf_path, "rb") as f:
            pdf_reader = PyPDF2.PdfReader(f)
            total = len(pdf_reader.pages)
            valid_pages = [p for p in page_nums if 1 <= p <= total]
            return [
                {"page": p, "content": pdf_reader.pages[p - 1].extract_text() or ""}
                for p in valid_pages
            ]

    @staticmethod
    def _get_pdf_page_content_pymupdf(pdf_path: str, page_nums: List[int]) -> List[Dict[str, Any]]:
        """PyMuPDF (fitz) 路径读 PDF 页内容(PyPDF2 失败时回退)。"""
        import fitz
        doc = fitz.open(pdf_path)
        total = doc.page_count
        valid_pages = [p for p in page_nums if 1 <= p <= total]
        results = []
        try:
            for p in valid_pages:
                page = doc.load_page(p - 1)
                results.append({"page": p, "content": page.get_text() or ""})
        finally:
            doc.close()
        return results
