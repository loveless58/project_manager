"""
Data Cleaning Tools — 数据清洗工具层

职责：
- 扫描原始文件目录
- 提取PDF/图片结构化数据（调用OCR或文本解析）
- 根据文件名/内容分类文档到业务领域
- 批量处理并保存结构化输出

数据流：
  原始文件（PDF/图片）→ 提取 → 结构化数据（JSON/Markdown）→ 分类归档

工作目录：
  数据清洗工作台/
  ├── 00-原始文件（待处理）
  ├── 01-OCR输出（待清洗）
  └── 02-已清洗（结构化数据）
"""
import os
import json
import shutil
import hashlib
import re
import stat
import tempfile
import uuid
import html
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional
from datetime import datetime

from common.file_readiness import probe_readable_file, probe_writable_dir
from common.workspace_config import resolve_workspace_config
from agents import AdversarialAgent, AuditAgent
from business_rules.archive_decision import evaluate_archive_decision, review_only_archive_decision
from business_rules.bid_project_rules import BidProjectRuleEngine
from business_rules.field_quality import filter_business_facts
from business_rules.invoice_fields import extract_invoice_fields
from business_rules.semantic_document import DocumentClassification, apply_semantic_guardrail, normalize_document_classification, normalize_semantic_response
from contracts.archive_intent import ArchiveIntent
from contracts.archive_run_artifacts import (
    normalize_business_context,
    normalize_interpretation_output,
    strict_json_load,
    validate_archive_action,
    validate_archive_intent,
    validate_candidate_interpretation,
    validate_task5_collection_artifact,
)
from contracts.feedback_schema import (
    FeedbackValidationError,
    build_parser_test_candidates,
    build_rule_candidates,
    feedback_event,
    normalize_feedback_decision,
)
from contracts.feedback_form_schema import (
    build_feedback_form,
    feedback_decisions_from_form,
    render_feedback_form_markdown,
)
from contracts.archive_gate_schema import evaluate_archive_execution_gate
from contracts.ocr_aliases import resolve_ocr_field
from contracts.parsers import extract_deadlines, normalize_date, parse_amount
from contracts.review_queue_schema import normalize_review_queue
from contracts.test_candidate_schema import (
    build_candidate_test_manifest,
    render_parser_candidate_tests,
    render_rule_candidate_tests,
)
from ledger import ProjectLedger
from ocr import normalize_ocr_result
from ocr import provider_registry
from platform_core.models import BusinessContextEvidence, BusinessContextQuery
from platform_core.storage_bindings import (
    AmbiguousStorageBindingError,
    StorageBindingNotFoundError,
)
from services.archive_targets import ArchiveTargetResolution
from services.document_interpretation import DocumentInterpretationService


# 工作目录在 DataCleaningTools 实例化时按实际路由解析。

# 分类关键词映射（新三层架构：项目投标/项目执行/项目丢标）
CLASSIFICATION_KEYWORDS = {
    "项目投标": ["招标", "投标", "报价", "标书", "询价", "采购公告", "邀标", "居间", "合同"],
    "项目执行": ["合同", "验收", "交付", "实施", "变更", "中标", "通知", "开工", "付款", "发票"],
    "项目丢标": ["弃标", "流标", "归档", "结算", "结项", "未中标"],
}


class DataCleaningTools:
    """数据清洗工具集合"""

    def _ocr_with_tesseract(self, file_path: str) -> Dict[str, Any]:
        """使用 Tesseract 进行 OCR（如果已安装）。"""
        import shutil
        import subprocess
        import tempfile
        import os
        
        tesseract_path = shutil.which("tesseract")
        if not tesseract_path:
            return {"status": "failed", "engine": "tesseract", "error": "tesseract not installed"}
        
        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext in [".png", ".jpg", ".jpeg"]:
                # 图片直接 OCR
                result = subprocess.run(
                    [tesseract_path, file_path, "stdout", "-l", "chi_sim+eng"],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    return {
                        "status": "success",
                        "engine": "tesseract",
                        "text": result.stdout,
                        "pages": [{"page": 1, "text": result.stdout, "confidence": 0.85}],
                    }
                return {"status": "failed", "engine": "tesseract", "error": result.stderr}
            else:
                # PDF 先转图片再 OCR
                import fitz
                doc = fitz.open(file_path)
                all_text = []
                pages = []
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(dpi=300)
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp_path = tmp.name
                    pix.save(tmp_path)
                    result = subprocess.run(
                        [tesseract_path, tmp_path, "stdout", "-l", "chi_sim+eng"],
                        capture_output=True, text=True, timeout=60
                    )
                    os.unlink(tmp_path)
                    if result.returncode == 0:
                        all_text.append(result.stdout)
                        pages.append({"page": i + 1, "text": result.stdout, "confidence": 0.85})
                doc.close()
                return {
                    "status": "success",
                    "engine": "tesseract",
                    "text": "\n".join(all_text),
                    "pages": pages,
                }
        except Exception as e:
            return {"status": "failed", "engine": "tesseract", "error": str(e)}

    def _ocr_with_easyocr(self, file_path: str) -> Dict[str, Any]:
        """使用 easyocr 进行 OCR（纯 Python，已下载模型后可用）。"""
        from ocr.providers.easyocr_provider import EasyOcrProvider

        return EasyOcrProvider().extract(file_path)

    def _ocr_with_rapidocr(self, file_path: str) -> Dict[str, Any]:
        """使用 RapidOCR (PaddleOCR v4 ONNX 量化版) 做 OCR。

        Fallback 引擎, 在 macOS Vision 不可用或识别率低时启用。
        模型已装在 site-packages: ch_PP-OCRv4_{det,rec}_infer.onnx + cls。
        """
        try:
            from ocr.providers.rapidocr_provider import RapidOcrProvider
            return RapidOcrProvider().extract(file_path)
        except Exception as e:
            return {"status": "failed", "engine": "rapidocr", "error": str(e)}

    def _default_ocr_adapter(self, file_path: str) -> Dict[str, Any]:
        """默认 OCR adapter (v0.4.0): Vision → RapidOCR → EasyOCR → Tesseract。

        用户拍板 (2026-07-23): macOS Vision 优先 (系统自带, zh-Hans, ~0.4s/页),
        RapidOCR 备选 (PaddleOCR v4 ONNX 量化版, ~1.5s/页)。
        EasyOCR / Tesseract 仅在前两者都失败时启用。
        """
        # 1. macOS Vision (首选, 系统自带)
        vision_result = self._ocr_with_vision_macos(file_path)
        if vision_result.get("status") == "success":
            text = vision_result.get("text", "")
            if len(text.strip()) >= 10:
                chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
                if chinese_chars > 10 or len(text) < 100:
                    return vision_result

        # 2. RapidOCR (PaddleOCR v4 ONNX 量化版, 跨平台备选)
        rapidocr_result = self._ocr_with_rapidocr(file_path)
        if rapidocr_result.get("status") == "success":
            return rapidocr_result

        # 3. EasyOCR (纯 Python, 中文支持好)
        easyocr_result = self._ocr_with_easyocr(file_path)
        if easyocr_result.get("status") == "success":
            return easyocr_result

        # 4. Tesseract (如果已安装)
        tesseract_result = self._ocr_with_tesseract(file_path)
        if tesseract_result.get("status") == "success":
            return tesseract_result

        # 5. 都失败
        return {
            "status": "failed",
            "engine": "unavailable",
            "error": "No OCR engine available. "
                     "Options: (1) pip install easyocr (models auto-download) "
                     "(2) brew install tesseract tesseract-lang",
        }

    def __init__(
        self,
        workspace_dir: Optional[str] = None,
        ocr_adapter: Optional[Callable[[str], Dict[str, Any]]] = None,
        semantic_adapter: Optional[Callable[[Dict[str, Any]], Any]] = None,
        *,
        storage_binding_registry: Any = None,
        document_store_router: Any = None,
        retrieval_service: Any = None,
        interpretation_service: Any = None,
        archive_target_resolver: Any = None,
    ):
        if workspace_dir is None:
            config = resolve_workspace_config()
            self.workspace_dir = str(config.data_cleaning_workspace)
            self.project_files_dir = str(config.project_files_dir)
            self.business_root = str(config.business_root)
            self.raw_dir = str(config.business_root)
        else:
            self.workspace_dir = workspace_dir
            self.project_files_dir = os.path.join(self.workspace_dir, "项目文件")
            self.business_root = self.workspace_dir
            self.raw_dir = os.path.join(self.workspace_dir, "00-原始文件（待处理）")
        self.ocr_dir = os.path.join(self.workspace_dir, "01-OCR输出（待清洗）")
        self.cleaned_dir = os.path.join(self.workspace_dir, "02-已清洗（结构化数据）")
        self.ocr_adapter = ocr_adapter
        self.semantic_adapter = semantic_adapter
        self.storage_binding_registry = storage_binding_registry
        self.document_store_router = document_store_router
        self.retrieval_service = retrieval_service
        self.interpretation_service = interpretation_service
        self.archive_target_resolver = archive_target_resolver
        if self.storage_binding_registry is not None:
            workspace_path = Path(self.workspace_dir).expanduser().resolve()
            for binding in self.storage_binding_registry.bindings:
                try:
                    workspace_path.relative_to(binding.physical_root)
                except ValueError:
                    continue
                raise ValueError("runtime workspace must be outside storage bindings")

    def scan_raw_files(self, source_dir: Optional[str] = None) -> Dict:
        """扫描原始文件目录，返回文件列表"""
        src = source_dir or self.raw_dir
        if not os.path.exists(src):
            return {"files": [], "count": 0, "source": src}
        files = []
        for f in os.listdir(src):
            fpath = os.path.join(src, f)
            if os.path.isfile(fpath) and not f.startswith("."):
                ext = os.path.splitext(f)[1].lower()
                files.append({
                    "name": f,
                    "path": fpath,
                    "type": ext,
                    "size": os.path.getsize(fpath),
                })
        return {"files": files, "count": len(files), "source": src}

    def extract_pdf(self, file_path: str) -> Dict:
        """提取 PDF 结构化数据
        
        当前实现：调用 pdf-ocr-extract.py 或直接解析文本型 PDF
        未来：集成 LLM 做更精确的结构化提取
        """
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}
        
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in [".pdf", ".png", ".jpg", ".jpeg", ".ofd"]:
            return {"error": f"Unsupported file type: {ext}"}

        result = provider_registry.extract_pdf_or_image(
            file_path,
            ocr_adapter=self.ocr_adapter or self._default_ocr_adapter,
        )
        text = result.get("extracted_text", "")
        if result.get("status") == "success":
            classification = self._classify_document(file_path, text, result.get("document_type") or "")
            document_type = classification["document_type"]
            fields = self._extract_fields_for_document(text, document_type)
            if document_type != "发票":
                self._apply_ocr_field_aliases(fields, result.get("fields", {}), document_type)
            result["fields"] = fields
            result["document_type"] = document_type
            result["classification"] = classification
        return result

    def run_ocr(self, file_path: str) -> Dict[str, Any]:
        """Run OCR for image or scanned-PDF input and return ocr.result.v1."""
        if not os.path.exists(file_path):
            return normalize_ocr_result({
                "status": "failed",
                "engine": "unavailable",
                "error": f"File not found: {file_path}",
            })
        return self._run_ocr(file_path)

    def extract_document(self, file_path: str) -> Dict:
        """Extract text, table text, classification, and candidate facts from a supported document."""
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}

        ext = os.path.splitext(file_path)[1].lower()
        if ext in [".pdf", ".png", ".jpg", ".jpeg", ".ofd"]:
            extracted = self.extract_pdf(file_path)
            if "error" not in extracted and not extracted.get("document_type"):
                extracted["document_type"] = self._classify_text_document(
                    file_path,
                    extracted.get("extracted_text") or extracted.get("text") or "",
                )
            return extracted

        if ext == ".md":
            # Markdown 文件直接读取文本
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                classification = self._classify_document(file_path, text)
                document_type = classification["document_type"]
                fields = self._extract_fields_for_document(text, document_type)
                return {
                    "schema_version": "document.extract.v1",
                    "file": file_path,
                    "filename": os.path.basename(file_path),
                    "file_type": ext,
                    "document_type": document_type,
                    "classification": classification,
                    "text_length": len(text),
                    "extracted_text": text[:4000] + ("..." if len(text) > 4000 else ""),
                    "fields": fields,
                }
            except Exception as e:
                return {"error": str(e)}

        if ext == ".xlsx":
            return self._extract_xlsx_document(file_path)

        if ext == ".xml":
            return self._extract_xml_document(file_path)

        if ext != ".docx":
            if self._business_phase_from_path(file_path):
                return self._metadata_passthrough_document(file_path, ext)
            return {"error": f"Unsupported file type: {ext}"}

        try:
            from docx import Document
        except ImportError:
            return {"error": "python-docx not installed. Run: pip install python-docx"}

        try:
            doc = Document(file_path)
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
            table_rows = []
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
                    if cells:
                        table_rows.append(cells)

            table_text_lines = [" | ".join(row) for row in table_rows]
            text = "\n".join(paragraphs + table_text_lines)
            classification = self._classify_document(file_path, text)
            document_type = classification["document_type"]
            fields = self._extract_fields_for_document(text, document_type)
            if document_type != "发票":
                fields.update(self._extract_docx_business_fields(paragraphs, table_rows, file_path))

            return {
                "schema_version": "document.extract.v1",
                "file": file_path,
                "filename": os.path.basename(file_path),
                "file_type": ext,
                "document_type": document_type,
                "classification": classification,
                "paragraph_count": len(paragraphs),
                "table_count": len(doc.tables),
                "table_row_count": len(table_rows),
                "text_length": len(text),
                "extracted_text": text[:4000] + ("..." if len(text) > 4000 else ""),
                "fields": fields,
            }
        except Exception as e:
            return {"error": str(e)}

    def _extract_ocr_document(self, file_path: str, file_type: str) -> Dict[str, Any]:
        """Extract image or scanned-PDF text through an OCR adapter or sidecar text."""
        ocr = self._run_ocr(file_path)
        if ocr.get("status") != "success":
            blocked_reason = ocr.get("blocked_reason", "ocr_adapter_unavailable")
            if file_type in {".png", ".jpg", ".jpeg"} and blocked_reason == "ocr_adapter_unavailable":
                blocked_reason = "ocr_engine_failed"
            return {
                "schema_version": "document.extract.v1",
                "status": "blocked",
                "blocked_reason": blocked_reason,
                "error": ocr.get("error", "ocr_adapter_unavailable: image or scanned PDF requires OCR"),
                "file": file_path,
                "filename": os.path.basename(file_path),
                "file_type": file_type,
                "extract_method": "ocr",
                "text_length": 0,
                "extracted_text": "",
                "is_scanned": True,
                "fields": {},
                "ocr": ocr,
                "needs_human_review": True,
            }

        text = ocr.get("text", "") or ""
        classification = self._classify_document(file_path, text)
        document_type = classification["document_type"]
        fields = self._extract_fields_for_document(text, document_type)
        needs_human_review = bool(ocr.get("quality", {}).get("needs_human_review"))
        return {
            "schema_version": "document.extract.v1",
            "status": "success",
            "file": file_path,
            "filename": os.path.basename(file_path),
            "file_type": file_type,
            "document_type": document_type,
            "classification": classification,
            "extract_method": "ocr",
            "text_length": len(text),
            "extracted_text": text[:4000] + ("..." if len(text) > 4000 else ""),
            "is_scanned": True,
            "fields": fields,
            "ocr": ocr,
            "needs_human_review": needs_human_review,
        }

    def _metadata_passthrough_document(self, file_path: str, file_type: str) -> Dict[str, Any]:
        return {
            "schema_version": "document.extract.v1",
            "status": "success",
            "file": file_path,
            "filename": os.path.basename(file_path),
            "file_type": file_type,
            "document_type": self.classify_document(file_path).get("category", "未分类"),
            "extract_method": "metadata_passthrough",
            "text_length": 0,
            "extracted_text": "",
            "fields": {},
            "needs_human_review": False,
        }

    def _extract_xlsx_document(self, file_path: str) -> Dict[str, Any]:
        try:
            from openpyxl import load_workbook
        except ImportError:
            return {"error": "openpyxl not installed. Run: pip install openpyxl"}

        try:
            workbook = load_workbook(file_path, read_only=True, data_only=True)
            lines: List[str] = []
            row_count = 0
            for sheet in workbook.worksheets:
                lines.append(f"[sheet] {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    values = [self._cell_to_text(value) for value in row]
                    values = [value for value in values if value]
                    if not values:
                        continue
                    row_count += 1
                    lines.append(" | ".join(values))
                    if row_count >= 200:
                        break
                if row_count >= 200:
                    break
            workbook.close()

            text = "\n".join(lines)
            classification = self._classify_document(file_path, text)
            document_type = classification["document_type"]
            fields = self._extract_fields_for_document(text, document_type)
            return {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": file_path,
                "filename": os.path.basename(file_path),
                "file_type": ".xlsx",
                "document_type": document_type,
                "classification": classification,
                "extract_method": "xlsx_table",
                "sheet_count": len(workbook.worksheets),
                "table_row_count": row_count,
                "text_length": len(text),
                "extracted_text": text[:4000] + ("..." if len(text) > 4000 else ""),
                "fields": fields,
                "needs_human_review": False,
            }
        except Exception as e:
            return {"error": str(e)}

    def _extract_xml_document(self, file_path: str) -> Dict[str, Any]:
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            pairs: List[str] = []
            for elem in root.iter():
                text = (elem.text or "").strip()
                if not text:
                    continue
                tag = elem.tag.split("}", 1)[-1]
                pairs.append(f"{tag}: {text}")
                if len(pairs) >= 200:
                    break
            extracted_text = "\n".join(pairs)
            classification = self._classify_document(file_path, extracted_text)
            document_type = classification["document_type"]
            fields = self._extract_fields_for_document(extracted_text, document_type)
            return {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": file_path,
                "filename": os.path.basename(file_path),
                "file_type": ".xml",
                "document_type": document_type,
                "classification": classification,
                "extract_method": "xml_text",
                "text_length": len(extracted_text),
                "extracted_text": extracted_text[:4000] + ("..." if len(extracted_text) > 4000 else ""),
                "fields": fields,
                "needs_human_review": False,
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _cell_to_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value).strip()

    def _ocr_with_vision_macos(self, file_path: str) -> Dict[str, Any]:
        """使用 macOS Vision 框架对 PDF/图片做 OCR。

        实现路径 (v0.4.0): 通过 swift 子进程调用 `integrations/macos_vision_bridge/swift_ocr_bridge`,
        避开 PyObjC VNRecognizeTextRequest 中文识别乱码问题。

        性能基准：~0.4s/页 (5 页 PDF ≈ 2s, 含 swift 启动开销)。
        中文识别率：与 Swift 直接调用输出一致, 漏字符率约 1-3% (vs Vision 直接)。
        """
        import time
        import subprocess
        import tempfile

        start = time.time()

        # 定位 swift_ocr_bridge 可执行文件
        bridge_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "integrations", "macos_vision_bridge", "swift_ocr_bridge",
        )
        if not os.path.isfile(bridge_path) or not os.access(bridge_path, os.X_OK):
            return {
                "status": "failed",
                "engine": "macos_vision",
                "error": f"swift_ocr_bridge not found or not executable: {bridge_path}",
            }

        # 输出 JSON 到临时文件 (避免 stdout buffer 问题)
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            tmp_path = tmp.name

        try:
            proc = subprocess.run(
                [bridge_path, file_path, tmp_path],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if proc.returncode != 0:
                return {
                    "status": "failed",
                    "engine": "macos_vision",
                    "error": f"swift_ocr_bridge exited with code {proc.returncode}: {proc.stderr[:300]}",
                }

            with open(tmp_path, "r", encoding="utf-8") as f:
                result = json.load(f)

            # 转换 pages 字段为内部 schema (confidence 已是 float, source_ref 可选)
            pages = []
            for p in result.get("pages", []):
                pages.append({
                    "page": p["page"],
                    "text": p["text"],
                    "confidence": p.get("confidence", 0.85),
                })

            elapsed = time.time() - start
            return {
                "status": "success",
                "engine": "macos_vision",
                "text": result.get("text", ""),
                "pages": pages,
                "elapsed_seconds": round(elapsed, 2),
            }
        except subprocess.TimeoutExpired:
            return {"status": "failed", "engine": "macos_vision", "error": "swift_ocr_bridge timeout (300s)"}
        except Exception as e:
            return {"status": "failed", "engine": "macos_vision", "error": str(e)}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    def _run_ocr(self, file_path: str) -> Dict[str, Any]:
        """Run the configured OCR adapter, or read a sidecar OCR text file when present."""
        # 1. 优先检查 sidecar
        sidecar = self._find_ocr_sidecar(file_path)
        if sidecar:
            text = self._read_text_file(sidecar)
            if text:
                return normalize_ocr_result({
                    "status": "success",
                    "engine": "sidecar_text",
                    "text": text,
                    "pages": [{"page": 1, "text": text, "confidence": 1.0, "source_ref": sidecar}],
                })

        # 2. 使用配置的 ocr_adapter
        if self.ocr_adapter is not None:
            try:
                result = self.ocr_adapter(file_path)
            except Exception as e:
                return normalize_ocr_result({
                    "status": "failed",
                    "engine": "custom_adapter",
                    "error": str(e),
                })
            if isinstance(result, str):
                result = {"text": result}
            
            # 如果 adapter 返回失败状态，直接传递错误信息
            if isinstance(result, dict) and result.get("status") == "failed":
                return normalize_ocr_result({
                    "status": "failed",
                    "engine": result.get("engine", "custom_adapter"),
                    "error": result.get("error", "OCR failed"),
                })
            
            text = result.get("text", "") if isinstance(result, dict) else ""
            if not text:
                return normalize_ocr_result({
                    "status": "failed",
                    "engine": result.get("engine", "custom_adapter") if isinstance(result, dict) else "custom_adapter",
                    "error": "OCR adapter returned empty text",
                })
            return normalize_ocr_result({
                "status": "success",
                "engine": result.get("engine", "custom_adapter"),
                "text": text,
                "pages": result.get("pages", []),
            })

        # 3. 没有配置 adapter，尝试默认的（Tesseract > easyocr > Vision）
        default_result = self._default_ocr_adapter(file_path)
        if default_result.get("status") == "success":
            return normalize_ocr_result({
                "status": "success",
                "engine": default_result.get("engine", "default"),
                "text": default_result.get("text", ""),
                "pages": default_result.get("pages", []),
            })

        # 4. 都失败，返回 blocked 并附带具体错误信息
        return normalize_ocr_result({
            "status": "blocked",
            "engine": default_result.get("engine", "unavailable"),
            "blocked_reason": "ocr_adapter_unavailable",
            "error": default_result.get("error", "No OCR engine available. Options: (1) brew install tesseract tesseract-lang (2) pip install easyocr"),
        })

    def _find_ocr_sidecar(self, file_path: str) -> Optional[str]:
        base, _ = os.path.splitext(file_path)
        candidates = [
            f"{file_path}.ocr.txt",
            f"{base}.ocr.txt",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def _read_text_file(self, file_path: str) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                with open(file_path, "r", encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    

    def _extract_fields_for_document(self, text: str, document_type: str) -> Dict[str, Any]:
        """Select a document-specific extractor before applying generic rules."""
        if document_type == "发票":
            return extract_invoice_fields(text)
        return self._extract_fields(text)

    def _extract_fields(self, text: str) -> Dict:
        """从文本中提取常见字段"""
        fields = {}
        
        # 招标编号
        m = re.search(r'(?:招标编号|项目编号|采购编号|CRM\s*编号)\**\s*[:：]?\s*([A-Z0-9\-]{5,})', text)
        if m:
            fields["project_code"] = m.group(1)
        
        # 项目名称
        m = re.search(r'(?:项目名称|采购名称|标的名称)\**\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if not m:
            m = re.search(r'^#\s*项目记录\s*[:：]\s*(.+?)(?:\n|$)', text, re.MULTILINE)
        if m:
            value = self._clean_markdown_field_value(m.group(1))
            if value:
                fields["project_name"] = value
        
        # 预算金额
        m = re.search(r'(?:预算金额|采购预算|最高限价)\s*[:：]?\s*([\d,\.]+)\s*万元?', text)
        if m:
            fields["budget"] = m.group(1)
        
        # 截止时间
        m = re.search(r'(?:投标截止|报名截止|截止时间)\s*[:：]?\s*(\d{4}[年\-/]\d{1,2}[月\-/]\d{1,2})', text)
        if m:
            fields["deadline"] = m.group(1)
        
        # 客户/招标人
        m = re.search(r'(?:招标人/客户|招标人|采购人|委托人|甲方|业主)\**\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            value = self._clean_markdown_field_value(m.group(1))
            if value not in {"待确认", "待录入", "待补充", "未指定"} and 2 <= len(value) <= 80 and not any(k in value for k in ["技术成果", "培训", "合同标的", "第三方"]):
                fields["customer"] = value
                fields.setdefault("customer_name", value)

        m = re.search(r'(?:中标结果|中标状态|投标结果|投标状态)\**\s*[:：]?\s*(已中标|未中标|已丢标|丢标|已弃标|弃标|待开标|已报名|待报名)', text)
        if m:
            value = self._clean_markdown_field_value(m.group(1))
            if value in {"未中标", "已丢标", "丢标"}:
                fields["bid_status"] = "已丢标"
            elif value in {"已弃标", "弃标"}:
                fields["bid_status"] = "已弃标"
            else:
                fields["bid_status"] = value

        m = re.search(r'(?:合同状态|签约状态)\**\s*[:：]?\s*(已签约|已签订|未签约|待签约)', text)
        if m:
            fields["contract_status"] = self._clean_markdown_field_value(m.group(1))

        m = re.search(r'(?:报名状态|报名情况)\**\s*[:：]?\s*(待报名|已报名|已弃标|弃标)', text)
        if m:
            fields["registration_status"] = self._clean_markdown_field_value(m.group(1))
            if fields["registration_status"] in {"已弃标", "弃标"}:
                fields["bid_status"] = "已弃标"

        m = re.search(r'(?:负责销售|销售负责人|客户经理|负责人)\**\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            value = self._clean_markdown_field_value(m.group(1))
            if value not in {"待确认", "待录入", "待补充", "未指定"} and re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value):
                fields["sales_owner"] = value

        if fields.get("bid_status") == "已弃标" or "已弃标" in text:
            fields["lifecycle_stage"] = "closed"
            fields["closed_reason_type"] = "abandoned_by_us"
        elif fields.get("bid_status") == "已丢标" or "未中标" in text or "已丢标" in text:
            fields["lifecycle_stage"] = "closed"
            fields["closed_reason_type"] = "lost_to_competitor"

        payment_patterns = {
            "payer": r"付款人\s*[:：]\s*(.+?)(?:\n|$)",
            "payee": r"收款人\s*[:：]\s*(.+?)(?:\n|$)",
            "payment_date": r"交易日期\s*[:：]\s*(\d{4}年\d{1,2}月\d{1,2}日?)",
            "payment_amount": r"交易金额\s*(?:[（(]小写[）)])?\s*[:：]\s*(.+?)(?:\n|$)",
            "payment_summary": r"交易摘要\s*[:：]\s*(.+?)(?:\n|$)",
        }
        for field, pattern in payment_patterns.items():
            m = re.search(pattern, text)
            if not m:
                continue
            value = self._clean_markdown_field_value(m.group(1))
            if field == "payment_amount":
                value = self._normalize_payment_amount(value)
            if value:
                fields[field] = value

        self._apply_contract_text_fields(text, fields)
        return fields

    def _apply_contract_text_fields(self, text: str, fields: Dict[str, Any]) -> None:
        deadlines = extract_deadlines(text)
        fields.update(deadlines)
        if deadlines.get("bid_deadline"):
            fields["deadline"] = deadlines["bid_deadline"]

        amount_match = re.search(
            r"(?:项目金额|预算金额|采购预算|最高限价|报价|投标报价|合同金额|金额)\s*[:：]\s*(.+?)(?:\n|$)",
            text,
        )
        if amount_match:
            amount, label = parse_amount(amount_match.group(1))
            if amount is not None:
                fields.setdefault("amount", amount)
            if label:
                fields.setdefault("amount_label", label)

    def _apply_ocr_field_aliases(
        self,
        fields: Dict[str, Any],
        ocr_fields: Optional[Dict[str, Any]],
        document_type: str,
    ) -> None:
        if not isinstance(ocr_fields, dict):
            return

        amount_fields = {"amount", "bid_fee", "budget", "quoted_amount", "contract_amount", "project_amount"}
        date_fields = {
            "date",
            "bid_deadline",
            "registration_deadline",
            "bid_open_time",
            "payment_date",
            "contract_date",
            "bid_announcement_date",
            "document_date",
        }
        for source_field, raw_value in ocr_fields.items():
            if raw_value in (None, ""):
                continue
            target_field = resolve_ocr_field(source_field, document_type)
            value = raw_value
            if target_field in amount_fields:
                amount, label = parse_amount(raw_value)
                if amount is not None:
                    value = amount
                if label:
                    fields.setdefault("amount_label", label)
            elif source_field in date_fields or target_field in date_fields:
                value = normalize_date(raw_value) or raw_value

            fields[target_field] = value
            if target_field == "bid_deadline":
                fields["deadline"] = value

    @staticmethod
    def _clean_markdown_field_value(value: str) -> str:
        value = value.strip()
        value = re.sub(r"^\s*[-*]\s*", "", value)
        value = re.sub(r"^\*+", "", value)
        value = re.sub(r"\*+\s*[:：]\s*", "", value)
        value = value.strip("* \t")
        value = re.sub(r"^[\s）)\]】]+", "", value)
        value = re.sub(r"^(?:委托人|受托人|甲方|乙方)\s*[:：]\s*", "", value)
        value = re.sub(r"^[（(](?:甲方|乙方)[）)]\s*", "", value)
        value = re.sub(r"\s*[（(](?:甲方|乙方)[）)]\s*$", "", value)
        return value.strip()

    @staticmethod
    def _normalize_payment_amount(value: str) -> str:
        raw = value.replace("O", "0").replace("o", "0").replace("零", "0")
        cleaned = re.sub(r"[^0-9.]", "", raw)
        m = re.search(r"\d+(?:\.\d*)?", cleaned)
        if not m:
            return ""
        amount = m.group(0).rstrip(".")
        if "." not in amount:
            return f"{amount}.00"
        integer, decimal = amount.split(".", 1)
        return f"{integer}.{decimal[:2].ljust(2, '0')}"

    def _extract_docx_business_fields(self, paragraphs: List[str], table_rows: List[List[str]], file_path: str) -> Dict:
        """Extract lightweight bid-project fields from Word paragraph/table text."""
        fields: Dict[str, Any] = {}
        lines = [line.strip() for line in paragraphs if line.strip()]
        table_lines = [" ".join(row) for row in table_rows]
        all_lines = lines + table_lines
        joined = "\n".join(all_lines)

        for idx, line in enumerate(all_lines):
            m = re.match(r"项目名称\s*[:：]\s*(.*)$", line)
            if m:
                value = m.group(1).strip()
                if not value and idx + 1 < len(all_lines):
                    value = all_lines[idx + 1].strip()
                if value:
                    fields.setdefault("project_name", value)
                    break

        if "project_name" not in fields:
            for line in all_lines[:40]:
                if 4 <= len(line) <= 40 and any(k in line for k in ["采购项目", "建设项目", "服务项目", "系统项目"]):
                    fields["project_name"] = line
                    break

        for line in all_lines[:80]:
            m = re.match(r"单位名称\s*[:：]\s*(.+)$", line)
            if m:
                fields["supplier_name"] = m.group(1).strip()
                break

        for line in all_lines[:40]:
            if line.endswith("有限公司") and "单位名称" not in line:
                fields.setdefault("customer_name", self._clean_markdown_field_value(line.strip()))
                break

        if "技术开发合同" in joined or "合同登记编号" in joined or ("委托人" in joined and "受托人" in joined):
            fields["document_type"] = "合同"
        elif "采购公告" in joined or "响应须知" in joined:
            fields["document_type"] = "采购公告"
        elif "投标文件" in os.path.basename(file_path) or "报价单" in joined:
            fields["document_type"] = "投标文件"

        m = re.search(r"日期\s*[:：]\s*(\d{4}年\d{1,2}月\d{1,2}日)", joined)
        if m:
            fields["document_date"] = m.group(1)

        amount_patterns = [
            r"(?:总价|报价|投标报价|合计)[^\d]{0,20}([\d,]+(?:\.\d+)?)\s*(?:元|万元)",
            r"人民币[^\d]{0,20}([\d,]+(?:\.\d+)?)\s*(?:元|万元)",
        ]
        for pattern in amount_patterns:
            m = re.search(pattern, joined)
            if m:
                fields["quoted_amount"] = m.group(1).replace(",", "")
                break

        return fields


    def _classify_document(self, file_path: str, text: str, document_type_hint: str = "") -> Dict[str, Any]:
        """Build one classification payload for extraction and archive planning."""
        filename = os.path.basename(file_path)
        haystack = f"{filename}\n{text[:5000]}".lower()
        if not document_type_hint and ("project_manager" in haystack or filename.lower().startswith("prd-project-manager")):
            return DocumentClassification("项目治理文档", "internal_project", None, None, 0.98, [f"filename:{filename}", "text:project_manager"], True).payload()

        document_type = document_type_hint or self._classify_text_document(file_path, text)
        phase = self._business_phase_from_path(file_path)
        domain = "finance" if document_type == "发票" else ("bid_project" if phase or document_type != "未分类" or "项目名称" in text else "unknown")
        requires_review = domain == "unknown" or (document_type == "发票" and not phase)
        return DocumentClassification(document_type, domain, phase or None, phase or None, 0.9 if document_type != "未分类" else 0.3, [f"filename:{filename}"], requires_review).payload()
    def _classify_text_document(self, file_path: str, text: str) -> str:
        filename = os.path.basename(file_path)
        haystack = f"{filename}\n{text[:5000]}"
        if filename.startswith("项目记录"):
            return "项目记录"
        if "电子发票" in haystack or "发票号码" in haystack:
            return "发票"
        if (
            "技术开发合同" in haystack
            or "合同登记编号" in haystack
            or ("委托人" in haystack and "受托人" in haystack)
            or ("买受人" in haystack and "出卖人" in haystack)
            or ("甲方" in haystack and "乙方" in haystack and "合同" in haystack)
            or ("合同" in filename and self._business_phase_from_path(file_path))
        ):
            return "合同"
        if any(token in filename for token in ("开户凭证", "账户", "账号", "汇款账号")) or (
            "开户银行" in haystack and "账号" in haystack
        ):
            return "账户凭证"
        if "付款凭证" in filename or "交易金额" in haystack or "交易摘要" in haystack:
            return "付款凭证"
        if "支付申请" in filename or "付款申请" in filename or "工程款支付申请" in haystack:
            return "付款申请"
        if "授权书" in filename or "授权代表" in haystack:
            return "授权文件"
        if "法审" in filename or "法律审核" in haystack or "审核意见" in haystack:
            return "法审材料"
        if "仲裁" in filename or "仲裁裁决" in haystack:
            return "仲裁文书"
        if "采购公告" in haystack:
            return "采购公告"
        if "投标文件" in haystack or "报价单" in haystack:
            return "投标文件"
        classification = self.classify_document(file_path)
        return classification.get("category", "未分类")

    def _source_type_for_document(self, document_type: str, filename: str) -> str:
        joined = f"{document_type} {filename}"
        if "投标文件" in joined:
            return "bid_document"
        if "采购公告" in joined or "招标公告" in joined:
            return "official_document"
        if "合同" in joined:
            return "contract_document"
        return "local_file"

    def process_documents_to_ledger(
        self,
        file_paths: List[str],
        project_name: str = "",
    ) -> Dict[str, Any]:
        """Process multiple source documents and write extracted facts into the project ledger.

        This is the first complete loop for the data-cleaning file-organization
        skill: read source documents, save structured extraction artifacts, and
        update the shared project overview ledger. Source files are not moved or
        modified.
        """
        if isinstance(file_paths, str):
            file_paths = [file_paths]

        extracted_items = []
        failures = []
        for path in file_paths:
            if self._use_archive_metadata_passthrough(path):
                extracted = self._metadata_passthrough_document(path, os.path.splitext(path)[1].lower())
            else:
                extracted = self.extract_document(path)
            if "error" in extracted:
                failures.append({"file": path, "error": extracted["error"]})
                continue
            extracted_items.append(extracted)

        inferred_project_name = project_name or self._first_field(extracted_items, "project_name") or "未命名项目"
        structured_dir = os.path.join(self.workspace_dir, "structured_documents", self._safe_name(inferred_project_name))
        os.makedirs(structured_dir, exist_ok=True)

        ledger_dir = self.project_files_dir
        ledger = ProjectLedger(base_dir=ledger_dir)
        ledger_result = None
        structured_outputs = []

        for item in extracted_items:
            facts = {
                key: value
                for key, value in (item.get("fields") or {}).items()
                if key not in {"document_type", "source_filename"}
            }
            facts.setdefault("project_name", inferred_project_name)

            source_type = self._source_type_for_document(item.get("document_type", ""), item.get("filename", ""))
            accepted_facts, field_quality = filter_business_facts(
                facts,
                source_type=source_type,
                source_path=item.get("file", ""),
            )
            item["field_quality"] = field_quality
            evidence = [
                {
                    "field": field,
                    "source_type": source_type,
                    "source_ref": item["file"],
                    "extract_method": item.get("extract_method") or ("docx_text_table" if item.get("file_type") == ".docx" else "document_text"),
                    "confidence": 0.86 if field in {"project_name", "document_type"} else 0.78,
                    "summary": f"{item.get('filename', '')} extracted as {item.get('document_type', '')}",
                }
                for field in accepted_facts.keys()
            ]

            if accepted_facts:
                ledger_result = ledger.apply_patch({
                    "project_name": accepted_facts.get("project_name", inferred_project_name),
                    "source_type": source_type,
                    "facts": accepted_facts,
                    "evidence": evidence,
                    "skill": "data_cleaning_file_organization",
                    "actor": "data_cleaning_file_organization",
                })
            else:
                ledger_result = self._archive_only_ledger_result(
                    inferred_project_name,
                    {"project_name": inferred_project_name},
                    evidence,
                )

            output_path = os.path.join(structured_dir, f"{self._safe_name(item['filename'])}_extracted.json")
            self._save_structured_json(output_path, {
                "schema_version": "document.structured_extraction.v1",
                "project_name": inferred_project_name,
                "source_type": source_type,
                "extraction": item,
                "accepted_business_facts": accepted_facts,
                "field_quality": field_quality,
                "ledger_artifacts": {
                    "project_overview_md": ledger_result.get("markdown_path", ""),
                    "project_ledger_json": ledger_result.get("state_path", ""),
                },
                "processed_at": datetime.now().isoformat(),
            })
            structured_outputs.append(output_path)

        return {
            "schema_version": "data_cleaning.documents_to_ledger.v1",
            "status": "success" if extracted_items and not failures else ("partial" if extracted_items else "failed"),
            "processed": len(extracted_items),
            "failed": len(failures),
            "project_name": inferred_project_name,
            "documents": [
                {
                    "file": item["file"],
                    "document_type": item.get("document_type"),
                    "fields": item.get("fields", {}),
                    "text_length": item.get("text_length", 0),
                    "paragraph_count": item.get("paragraph_count", 0),
                    "table_count": item.get("table_count", 0),
                }
                for item in extracted_items
            ],
            "failures": failures,
            "structured_outputs": structured_outputs,
            "artifacts": {
                "project_overview_md": ledger_result.get("markdown_path", "") if ledger_result else "",
                "project_ledger_json": ledger_result.get("state_path", "") if ledger_result else "",
                "project_dir": ledger_result.get("project_dir", "") if ledger_result else "",
            },
        }

    def build_evidence_pack(
        self,
        file_path: str,
        extracted: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a read-only evidence pack for semantic structuring."""
        extracted = extracted or self.extract_document(file_path)
        text = extracted.get("extracted_text") or extracted.get("text") or ""
        text_segments = []
        if text:
            text_segments.append({
                "ref": "text:0",
                "text": text[:4000],
            })

        fields = dict(extracted.get("fields") or {})
        path_project = self._project_name_from_phase_path(self._path_parts(file_path))
        if path_project:
            fields.setdefault("project_name", path_project)
        path_phase = self._business_phase_from_path(file_path)
        if path_phase == "项目执行":
            fields.setdefault("lifecycle_stage", "execution")
        elif path_phase == "项目丢标":
            fields.setdefault("lifecycle_stage", "closed")

        return {
            "schema_version": "document.evidence_pack.v1",
            "source_file": file_path,
            "filename": os.path.basename(file_path),
            "file_type": extracted.get("file_type") or os.path.splitext(file_path)[1].lower(),
            "document_type_hint": extracted.get("document_type", ""),
            "extract_method": extracted.get("extract_method") or (
                "docx_text_table" if os.path.splitext(file_path)[1].lower() == ".docx" else "document_text"
            ),
            "text_length": extracted.get("text_length", len(text)),
            "text_segments": text_segments,
            "path_context": {
                "business_phase": path_phase,
                "project_name": path_project,
            },
            "candidate_fields": fields,
            "extraction_quality": {
                "needs_human_review": bool(extracted.get("needs_human_review")),
                "ocr_quality": ((extracted.get("ocr") or {}).get("quality") or {}),
            },
        }

    def semantic_structure_document(self, evidence_pack: Dict[str, Any]) -> Dict[str, Any]:
        """Read-only semantic structuring over an evidence pack; never writes ledgers or archives."""
        if not isinstance(evidence_pack, dict):
            return {
                "schema_version": "semantic_document.v1",
                "status": "blocked",
                "blocked_reason": "invalid_evidence_pack",
                "accepted_business_facts": {},
                "semantic_guardrail": {
                    "schema_version": "semantic_guardrail.v1",
                    "status": "blocked",
                    "rejected_fields": [],
                },
            }

        source_path = evidence_pack.get("source_file", "")
        fallback_document_type = evidence_pack.get("document_type_hint", "")
        if self.semantic_adapter is None:
            return {
                "schema_version": "semantic_document.v1",
                "status": "blocked",
                "blocked_reason": "semantic_adapter_unavailable",
                "document_type": fallback_document_type,
                "accepted_business_facts": {},
                "semantic_guardrail": {
                    "schema_version": "semantic_guardrail.v1",
                    "status": "blocked",
                    "rejected_fields": [],
                },
                "review_reasons": ["configure_semantic_adapter_or_llm_provider"],
            }

        try:
            raw = self.semantic_adapter(evidence_pack)
            if isinstance(raw, str):
                raw = json.loads(raw)
        except Exception as exc:
            return {
                "schema_version": "semantic_document.v1",
                "status": "blocked",
                "blocked_reason": "semantic_adapter_failed",
                "error": str(exc),
                "document_type": fallback_document_type,
                "accepted_business_facts": {},
                "semantic_guardrail": {
                    "schema_version": "semantic_guardrail.v1",
                    "status": "blocked",
                    "rejected_fields": [],
                },
            }

        semantic_document = normalize_semantic_response(raw, fallback_document_type=fallback_document_type)
        source_type = self._source_type_for_document(
            semantic_document.get("document_type", ""),
            evidence_pack.get("filename", ""),
        )
        accepted_facts, guardrail = apply_semantic_guardrail(
            semantic_document,
            source_type=source_type,
            source_path=source_path,
        )
        semantic_document["accepted_business_facts"] = accepted_facts
        semantic_document["semantic_guardrail"] = guardrail
        return semantic_document

    def extract_structured_business_output(
        self,
        file_paths: Optional[List[str]] = None,
        source_dir: str = "",
        project_name: str = "",
        output_dir: str = "",
        skip_backups: bool = True,
    ) -> Dict[str, Any]:
        """Read source files and write structured business evidence without ledger/archive side effects."""
        paths = self._resolve_structured_source_files(file_paths, source_dir, skip_backups)
        run_id = datetime.now().strftime("structured_%Y%m%d_%H%M%S_%f")
        target_dir = output_dir or os.path.join(self.workspace_dir, "runs", run_id, "structured_business_output")
        os.makedirs(target_dir, exist_ok=True)

        documents: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        business_cases: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []

        supported = {".md", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".ofd", ".xlsx", ".xml"}
        for item in paths:
            if item.get("status") == "skipped":
                skipped.append(item)
                continue
            path = item["path"]
            ext = os.path.splitext(path)[1].lower()
            if ext not in supported:
                skipped.append({"file": path, "reason": f"unsupported_suffix:{ext}"})
                continue

            readiness = probe_readable_file(path)
            if readiness.get("status") != "ready":
                failure = {
                    "file": path,
                    "stage": "source_readiness",
                    "status": "blocked",
                    "error": "source_not_local_or_unreadable",
                    "blocked_reason": readiness.get("blocked_reason", "source_not_local_or_unreadable"),
                    "readiness": readiness,
                }
                failures.append(failure)
                trace.append({"stage": "source_readiness", "file": path, "status": "blocked"})
                continue

            extracted = self.extract_document(path)
            if "error" in extracted:
                failure = {
                    "file": path,
                    "stage": "extract_document",
                    "status": extracted.get("status", "failed"),
                    "error": extracted["error"],
                }
                if extracted.get("blocked_reason"):
                    failure["blocked_reason"] = extracted["blocked_reason"]
                failures.append(failure)
                trace.append({"stage": "extract_document", "file": path, "status": failure["status"]})
                continue

            fields = self._apply_source_path_context(extracted.get("fields") or {}, path)
            extracted["fields"] = fields
            evidence_pack = self.build_evidence_pack(path, extracted=extracted)
            evidence_pack["candidate_fields"] = fields
            semantic_structure = self.semantic_structure_document(evidence_pack)
            inferred_project_name = fields.get("project_name") or self._project_name_from_phase_path(self._path_parts(path)) or project_name or "未命名项目"
            facts = {key: value for key, value in fields.items() if key not in {"document_type", "source_filename"}}
            facts.setdefault("project_name", inferred_project_name)
            extraction_trust = self._business_fact_gate(extracted)
            if not extraction_trust.get("trusted", True):
                facts = {
                    key: value
                    for key, value in facts.items()
                    if key in {"project_name", "lifecycle_stage"}
                }
            source_type = self._source_type_for_document(extracted.get("document_type", ""), extracted.get("filename", os.path.basename(path)))
            accepted_facts, field_quality = filter_business_facts(
                facts,
                source_type=source_type,
                source_path=path,
            )
            field_quality["extraction_trust"] = extraction_trust
            judgement_facts = dict(accepted_facts) if accepted_facts else dict(facts)
            judgement_facts.setdefault("project_name", inferred_project_name)
            business_judgement = BidProjectRuleEngine().evaluate(judgement_facts)
            cases = self._business_cases_for_structured_output(
                project_name=inferred_project_name,
                source_file=path,
                source_type=source_type,
                facts=judgement_facts,
                judgement=business_judgement,
            )
            business_cases.extend(cases)

            documents.append({
                "source_file": path,
                "filename": os.path.basename(path),
                "status": extracted.get("status", "success"),
                "file_type": extracted.get("file_type") or ext,
                "document_type": extracted.get("document_type", ""),
                "source_type": source_type,
                "extract_method": extracted.get("extract_method") or ("docx_text_table" if ext == ".docx" else "document_text"),
                "text_length": extracted.get("text_length", len(extracted.get("extracted_text", "") or "")),
                "paragraph_count": extracted.get("paragraph_count", 0),
                "table_count": extracted.get("table_count", 0),
                "fields": fields,
                "accepted_business_facts": accepted_facts,
                "field_quality": field_quality,
                "semantic_structure": semantic_structure,
                "business_judgement": business_judgement,
                "business_cases": cases,
                "extraction": extracted,
            })
            trace.append({"stage": "structured_output", "file": path, "status": "success", "project_name": inferred_project_name})

        primary_project_name = project_name or self._first_project_name_from_documents(documents) or "未命名项目"
        output_path = os.path.join(target_dir, f"{self._safe_name(primary_project_name)}_structured_business_output.json")
        payload = {
            "schema_version": "business_structured_output.run.v1",
            "run_id": run_id,
            "project_name": primary_project_name,
            "source_dir": source_dir,
            "created_at": datetime.now().isoformat(),
            "boundary": {
                "mode": "read_only_structured_output",
                "moved_files": False,
                "updated_external_ledger": False,
                "archive_plan_created": False,
                "archive_plan_executed": False,
            },
            "summary": {
                "documents_total": len(documents),
                "documents_success": len([d for d in documents if d.get("status") == "success"]),
                "documents_blocked": len([d for d in documents if d.get("status") == "blocked"]),
                "documents_failed": len(failures),
                "business_cases_total": len(business_cases),
                "skipped_total": len(skipped),
            },
            "documents": documents,
            "business_cases": business_cases,
            "failures": failures,
            "skipped": skipped,
            "trace": trace,
        }
        self._save_structured_json(output_path, payload)
        return {
            "schema_version": "business_structured_output.result.v1",
            "run_id": run_id,
            "status": "success" if documents and not failures else ("partial" if documents else "failed"),
            "project_name": primary_project_name,
            "processed": len(documents),
            "failed": len(failures),
            "business_cases": business_cases,
            "failures": failures,
            "skipped": skipped,
            "artifacts": {
                "structured_business_output": output_path,
                "output_dir": target_dir,
            },
            "boundary": payload["boundary"],
        }

    @staticmethod
    def _logical_ref_payload(ref: Any) -> Dict[str, str]:
        return {
            "storage_provider": ref.storage_provider,
            "object_key": ref.object_key,
            "logical_uri": ref.logical_uri,
            "binding_id": ref.binding_id,
        }

    @staticmethod
    def _interpretation_document_type(value: str) -> str:
        return {
            "发票": "invoice",
            "合同": "contract",
            "项目记录": "project",
            "投标文件": "bid",
            "采购公告": "tender",
        }.get(value, value if value in {"invoice", "project", "contract", "bid", "tender", "other"} else "other")

    @staticmethod
    def _interpretation_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            "invoice_number", "invoice_date", "amount", "tax_amount", "total_amount",
            "buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id",
            "project_code", "project_name", "contract_code", "contract_name",
            "buyer", "seller", "date", "line_items",
        }
        return {key: value for key, value in fields.items() if key in allowed}

    @staticmethod
    def _retrieval_payload(context: BusinessContextEvidence) -> Dict[str, Any]:
        _, payload = normalize_business_context(context)
        return payload

    @staticmethod
    def _business_relation(interpretation: Dict[str, Any]) -> Dict[str, str]:
        relation = next(iter(interpretation.get("relations") or []), {})
        candidate_id = relation.get("target_candidate_id", "") if isinstance(relation, dict) else ""
        relation_type = relation.get("relation_type", "") if isinstance(relation, dict) else ""
        result = {"relation_type": relation_type}
        if relation_type == "invoice_contract":
            result["candidate_contract_id"] = candidate_id
        elif relation_type in {"invoice_project", "contract_project"}:
            # Keep the compatibility contract requested by the run package while
            # retaining the canonical relation in ``relations``.
            result["candidate_contract_id"] = candidate_id
            result["candidate_project_id"] = candidate_id
        return result

    @staticmethod
    def _fixed_context_interpretation(service: Any, evidence_pack: Dict[str, Any], context: BusinessContextEvidence) -> Dict[str, Any]:
        class FixedRetrieval:
            def find_business_candidates(self, query):
                return context

        if service is None:
            return {
                "status": "blocked",
                "blocked_reason": "DOCUMENT_INTERPRETATION.CAPABILITY_DISABLED",
                "parse_artifact_ref": evidence_pack["parse_artifact_ref"],
                "confirmed": False,
            }
        if hasattr(service, "interpreter"):
            return DocumentInterpretationService(FixedRetrieval(), service.interpreter).interpret(evidence_pack)
        result = service.interpret(evidence_pack)
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault("parse_artifact_ref", evidence_pack["parse_artifact_ref"])
            result.setdefault("confirmed", False)
        return normalize_interpretation_output(
            result,
            parse_artifact_ref=evidence_pack["parse_artifact_ref"],
        )

    @staticmethod
    def _interpretation_blockers(
        classification: Dict[str, Any],
        context: BusinessContextEvidence,
        interpretation: Dict[str, Any],
        resolution: ArchiveTargetResolution,
    ) -> List[str]:
        blockers: List[str] = []
        if context.status != "matched":
            blockers.append("BUSINESS_CONTEXT.BLOCKED" if context.status == "blocked" else "BUSINESS_CONTEXT.NEEDS_REVIEW")
        blockers.extend(
            str(item.get("code"))
            for item in context.diagnostics
            if isinstance(item, dict) and item.get("code") != "BUSINESS_CONTEXT.CANDIDATES_FOUND"
        )
        blockers.extend(
            str(item.get("code", "BUSINESS_CONTEXT.CONFLICT"))
            for item in context.conflicts
            if isinstance(item, dict)
        )
        status = interpretation.get("status")
        if status == "blocked":
            blockers.append(str(interpretation.get("blocked_reason") or "DOCUMENT_INTERPRETATION.BLOCKED"))
        elif status == "needs_review" or interpretation.get("relations"):
            blockers.append("DOCUMENT_INTERPRETATION.NEEDS_REVIEW")
        if classification.get("requires_review"):
            blockers.append("classification_requires_review")
            blockers.extend(classification.get("validation_errors") or [])
        blockers.extend(resolution.diagnostics)
        return list(dict.fromkeys(blockers))

    @staticmethod
    def _source_identity(path: str) -> tuple[int, int, int, int]:
        details = os.lstat(path)
        attributes = getattr(details, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
            raise ValueError("DOCUMENT_SOURCE.SYMLINK_REJECTED")
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("DOCUMENT_SOURCE.NOT_REGULAR")
        return (
            details.st_dev, details.st_ino, details.st_size,
            getattr(details, "st_mtime_ns", int(details.st_mtime * 1_000_000_000)),
        )

    def _read_source_snapshot(
        self, path: str, source_ref: Any, run_dir: str,
    ) -> tuple[str, str, tuple[int, int, int, int]]:
        if self.document_store_router is None:
            raise ValueError("DOCUMENT_STORE.CAPABILITY_DISABLED")
        before = self._source_identity(path)
        try:
            with self.document_store_router.open_read(source_ref) as stream:
                try:
                    opened = os.fstat(stream.fileno())
                except (AttributeError, OSError, ValueError):
                    opened = None
                if opened is not None and (
                    opened.st_dev != before[0]
                    or opened.st_ino != before[1]
                    or opened.st_size != before[2]
                ):
                    raise ValueError("DOCUMENT_SOURCE.REPLACED")
                content = stream.read(16 * 1024 * 1024 + 1)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("DOCUMENT_SOURCE.READ_FAILED") from exc
        if not isinstance(content, bytes) or len(content) > 16 * 1024 * 1024:
            raise ValueError("DOCUMENT_SOURCE.SIZE_LIMIT")
        if self._source_identity(path) != before:
            raise ValueError("DOCUMENT_SOURCE.REPLACED")
        content_hash = hashlib.sha256(content).hexdigest()
        snapshot_dir = os.path.join(run_dir, ".source_snapshots")
        os.makedirs(snapshot_dir, exist_ok=True)
        source_name = os.path.basename(path)
        snapshot_path = os.path.join(snapshot_dir, f"{content_hash[:24]}__{source_name}")
        descriptor, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=snapshot_dir)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, snapshot_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return snapshot_path, content_hash, before

    def _save_task5_collection(
        self, output_path: str, payload: Dict[str, Any], *, kind: str, run_id: str,
    ) -> None:
        validate_task5_collection_artifact(payload, kind=kind, run_id=run_id)
        self._save_structured_json(output_path, payload)

    def _prepare_interpreted_file_organization_run(self, file_paths: List[str]) -> Dict[str, Any]:
        run_id, run_dir = self._claim_archive_intent_run()
        extracted_dir = os.path.join(run_dir, "extracted")
        os.makedirs(extracted_dir, exist_ok=False)
        candidate_interpretations: List[Dict[str, Any]] = []
        archive_intents: List[Dict[str, Any]] = []
        archive_actions: List[Dict[str, Any]] = []
        structured_outputs: List[str] = []
        failures: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []
        manifest_files: List[Dict[str, Any]] = []

        for path in file_paths:
            try:
                self._source_identity(path)
            except (OSError, ValueError) as exc:
                code = str(exc) if isinstance(exc, ValueError) else "DOCUMENT_SOURCE.READ_FAILED"
                failures.append({"source": os.path.basename(path), "stage": "source_snapshot", "status": "blocked", "blocked_reason": code})
                archive_actions.append(self._review_only_archive_action(run_id, None, [code]))
                trace.append({"stage": "source_snapshot", "source": os.path.basename(path), "status": "blocked", "blocked_reason": code})
                continue
            try:
                source_ref = self.storage_binding_registry.document_ref_from_path(path)
            except AmbiguousStorageBindingError:
                code = "STORAGE_BINDING.AMBIGUOUS"
                failures.append({"source": os.path.basename(path), "stage": "storage_binding", "status": "blocked", "blocked_reason": code})
                archive_actions.append(self._review_only_archive_action(run_id, None, [code]))
                trace.append({"stage": "storage_binding", "source": os.path.basename(path), "status": "blocked", "blocked_reason": code})
                continue
            except StorageBindingNotFoundError:
                code = "STORAGE_BINDING.NOT_FOUND"
                failures.append({"source": os.path.basename(path), "stage": "storage_binding", "status": "blocked", "blocked_reason": code})
                archive_actions.append(self._review_only_archive_action(run_id, None, [code]))
                trace.append({"stage": "storage_binding", "source": os.path.basename(path), "status": "blocked", "blocked_reason": code})
                continue

            ref_payload = self._logical_ref_payload(source_ref)
            try:
                snapshot_path, content_hash, source_identity = self._read_source_snapshot(
                    path, source_ref, run_dir,
                )
            except ValueError as exc:
                code = str(exc)
                failures.append({"source_ref": ref_payload, "stage": "source_snapshot", "status": "blocked", "blocked_reason": code})
                archive_actions.append(self._review_only_archive_action(run_id, source_ref, [code]))
                trace.append({"stage": "source_snapshot", "source_ref": ref_payload, "status": "blocked", "blocked_reason": code})
                continue
            extracted = self.extract_document(snapshot_path)
            try:
                if self._source_identity(path) != source_identity:
                    raise ValueError("DOCUMENT_SOURCE.REPLACED")
            except (OSError, ValueError) as exc:
                code = str(exc) if isinstance(exc, ValueError) else "DOCUMENT_SOURCE.REPLACED"
                failures.append({"source_ref": ref_payload, "stage": "source_snapshot", "status": "blocked", "blocked_reason": code})
                archive_actions.append(self._review_only_archive_action(run_id, source_ref, [code]))
                trace.append({"stage": "source_snapshot", "source_ref": ref_payload, "status": "blocked", "blocked_reason": code})
                continue
            if "error" in extracted or extracted.get("status") == "blocked":
                code = str(extracted.get("blocked_reason") or "DOCUMENT_PARSE.FAILED")
                failures.append({"source_ref": ref_payload, "stage": "native_parse", "status": "blocked", "blocked_reason": code})
                archive_actions.append(self._review_only_archive_action(run_id, source_ref, [code]))
                trace.append({"stage": "native_parse", "source_ref": ref_payload, "status": "blocked", "blocked_reason": code})
                continue

            manifest_files.append({
                "run_id": run_id, "source_ref": ref_payload,
                "name": os.path.basename(path), "content_hash": content_hash,
            })
            parse_ref = f"artifact:parsed:{content_hash[:24]}"
            classification = normalize_document_classification(
                extracted.get("classification"),
                fallback_document_type=extracted.get("document_type") or "未分类",
            )
            fields = self._interpretation_fields(dict(extracted.get("fields") or {}))
            text = str(extracted.get("extracted_text") or extracted.get("text") or "")
            evidence_pack = {
                "parse_artifact_ref": parse_ref,
                "document_type_hint": self._interpretation_document_type(str(extracted.get("document_type") or "other")),
                "candidate_fields": fields,
                "text_segments": ([{"id": "text-0", "text": text[:4000]}] if text else []),
            }
            extracted_path = os.path.join(extracted_dir, f"{content_hash[:24]}_extracted.json")
            self._save_structured_json(extracted_path, {
                "schema_version": "file_organization.extracted_document.v1",
                "parse_artifact_ref": parse_ref,
                "source_ref": ref_payload,
                "content_hash": content_hash,
                "document_type": extracted.get("document_type", ""),
                "classification": classification,
                "candidate_fields": fields,
                "text_length": len(text),
            })
            structured_outputs.append(extracted_path)

            if self.retrieval_service is None:
                context = BusinessContextEvidence(
                    "blocked", (), (), (), ({"code": "BUSINESS_CONTEXT.NO_CANDIDATES"},),
                )
            else:
                try:
                    context = self.retrieval_service.find_business_candidates(
                        BusinessContextQuery(evidence_pack["document_type_hint"], fields, evidence_pack["text_segments"])
                    )
                    if not isinstance(context, BusinessContextEvidence):
                        raise TypeError("invalid business context")
                except Exception:
                    context = BusinessContextEvidence(
                        "blocked", (), (), (), ({"code": "BUSINESS_CONTEXT.NO_CANDIDATES"},),
                    )
            context, context_payload = normalize_business_context(context)
            interpretation = self._fixed_context_interpretation(self.interpretation_service, evidence_pack, context)
            interpretation = normalize_interpretation_output(
                interpretation, parse_artifact_ref=parse_ref,
            )
            interpretation_entry = dict(interpretation)
            interpretation_entry["run_id"] = run_id
            interpretation_entry["source_ref"] = ref_payload
            interpretation_entry["content_hash"] = content_hash
            interpretation_entry["business_context"] = context_payload
            interpretation_entry["business_relation"] = self._business_relation(interpretation)
            validate_candidate_interpretation(interpretation_entry, run_id)
            candidate_interpretations.append(interpretation_entry)

            relations = interpretation.get("relations") if isinstance(interpretation.get("relations"), list) else []
            explicit_ids = tuple(
                relation.get("target_candidate_id")
                for relation in relations
                if isinstance(relation, dict) and isinstance(relation.get("target_candidate_id"), str)
            )
            if self.archive_target_resolver is None:
                resolution = ArchiveTargetResolution(
                    "unresolved", (), "", ("ARCHIVE_TARGET.CAPABILITY_DISABLED", "ARCHIVE_TARGET.UNRESOLVED"),
                )
            else:
                resolution = self.archive_target_resolver.resolve(source_ref, explicit_ids)
            project_id = explicit_ids[0] if len(explicit_ids) == 1 else ""
            intent = ArchiveIntent(
                source_ref=source_ref,
                destination_status=resolution.destination_status,
                candidate_target_binding_ids=resolution.candidate_target_binding_ids,
                project_id=project_id,
                archive_phase=classification.get("archive_phase") or "",
                content_hash=content_hash,
            )
            blockers = self._interpretation_blockers(classification, context, interpretation, resolution)
            intent_payload = intent.payload()
            intent_payload.update({
                "run_id": run_id,
                "intent_id": f"archive-intent:{content_hash[:24]}",
                "normalized_status": "blocked" if context.status == "blocked" or interpretation.get("status") == "blocked" else "needs_review",
                "blockers": blockers,
                "classification": classification,
            })
            validate_archive_intent(intent_payload, run_id)
            archive_intents.append(intent_payload)
            action = self._review_only_archive_action(run_id, source_ref, blockers, intent_payload)
            validate_archive_action(action, run_id)
            archive_actions.append(action)
            trace.extend([
                {"stage": "native_parse", "source_ref": ref_payload, "status": "success", "parse_artifact_ref": parse_ref},
                {"stage": "retrieval", "source_ref": ref_payload, "status": context.status},
                {"stage": "interpretation", "source_ref": ref_payload, "status": interpretation.get("status", "blocked")},
                {"stage": "archive_intent", "source_ref": ref_payload, "status": intent_payload["normalized_status"]},
                {"stage": "stop", "source_ref": ref_payload, "status": "needs_review"},
            ])

        review_items = [
            {
                "type": "archive_intent_review",
                "severity": "high" if action.get("normalized_status") == "blocked" else "medium",
                "source_ref": action.get("source_ref"),
                "blockers": action.get("blockers", []),
                "recommended_action": "review business interpretation and explicitly resolve the archive target",
            }
            for action in archive_actions
        ]
        review_queue = normalize_review_queue(run_id=run_id, raw_items=review_items)
        artifacts = {
            "input_manifest": os.path.join(run_dir, "input_manifest.json"),
            "candidate_interpretations": os.path.join(run_dir, "candidate_interpretations.json"),
            "archive_intents": os.path.join(run_dir, "archive_intents.json"),
            "review_queue": os.path.join(run_dir, "review_queue.json"),
            "planned_archive_actions": os.path.join(run_dir, "planned_archive_actions.json"),
            "trace": os.path.join(run_dir, "trace.json"),
            "run_dir": run_dir,
        }
        self._save_task5_collection(artifacts["input_manifest"], {
            "schema_version": "file_organization.input_manifest.v1", "run_id": run_id, "files": manifest_files,
        }, kind="input_manifest", run_id=run_id)
        self._save_task5_collection(artifacts["candidate_interpretations"], {
            "schema_version": "candidate_interpretations.v1", "run_id": run_id, "items": candidate_interpretations,
        }, kind="candidate_interpretations", run_id=run_id)
        self._save_task5_collection(artifacts["archive_intents"], {
            "schema_version": "archive_intents.v1", "run_id": run_id, "items": archive_intents,
        }, kind="archive_intents", run_id=run_id)
        self._save_structured_json(artifacts["review_queue"], review_queue)
        self._save_task5_collection(artifacts["planned_archive_actions"], {
            "schema_version": "archive_plan.v1", "run_id": run_id,
            "archive_intent_required": True, "actions": archive_actions,
        }, kind="planned_archive_actions", run_id=run_id)
        self._save_structured_json(artifacts["trace"], {
            "schema_version": "file_organization.trace.v1", "run_id": run_id, "events": trace,
        })
        return {
            "schema_version": "file_organization.run.v1",
            "run_id": run_id,
            "status": "success" if archive_intents and not failures else ("partial" if archive_intents else "failed"),
            "processed": len(archive_intents),
            "failed": len(failures),
            "structured_outputs": structured_outputs,
            "failures": failures,
            "candidate_interpretations": candidate_interpretations,
            "archive_intents": archive_intents,
            "review_queue": review_queue,
            "archive_actions": archive_actions,
            "artifacts": artifacts,
        }

    @staticmethod
    def _review_only_archive_action(run_id: str, source_ref: Any, blockers: List[str], intent: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ref_payload = DataCleaningTools._logical_ref_payload(source_ref) if source_ref is not None else {}
        return {
            "schema_version": "archive_action.v1",
            "run_id": run_id,
            "status": "needs_review",
            "normalized_status": "blocked" if any(str(item).startswith(("STORAGE_BINDING.", "BUSINESS_CONTEXT.", "DOCUMENT_INTERPRETATION.")) for item in blockers) else "needs_review",
            "confirmed": False,
            "source_file": ref_payload.get("logical_uri", ""),
            "source_ref": ref_payload,
            "project_name": (intent or {}).get("project_id", "") or "待确认",
            "document_type": ((intent or {}).get("classification") or {}).get("document_type", "未分类"),
            "business_judgement": {},
            "archive_decision": review_only_archive_decision(intent or {}, blockers),
            "proposed_name": "",
            "target_dir": None,
            "target_path": None,
            "blockers": list(dict.fromkeys(blockers)),
            "archive_intent_ref": (intent or {}).get("intent_id", ""),
            "archive_intent": intent,
        }

    def prepare_file_organization_run(self, file_paths: List[str], project_name: str = "") -> Dict[str, Any]:
        """Prepare a full file-organization run package without moving source files."""
        if isinstance(file_paths, str):
            file_paths = [file_paths]
        if self.storage_binding_registry is not None:
            return self._prepare_interpreted_file_organization_run(file_paths)

        run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        extracted_dir = os.path.join(run_dir, "extracted")
        patches_dir = os.path.join(run_dir, "project_patches")
        os.makedirs(extracted_dir, exist_ok=True)
        os.makedirs(patches_dir, exist_ok=True)

        readiness_by_path = {path: probe_readable_file(path) for path in file_paths}
        input_manifest = {
            "schema_version": "file_organization.input_manifest.v1",
            "run_id": run_id,
            "created_at": datetime.now().isoformat(),
            "files": [
                {
                    "path": path,
                    "name": os.path.basename(path),
                    "exists": readiness_by_path[path].get("exists", False),
                    "size": os.path.getsize(path) if readiness_by_path[path].get("status") == "ready" else None,
                    "readiness": readiness_by_path[path],
                }
                for path in file_paths
            ],
        }

        extracted_items: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        structured_outputs: List[str] = []
        ledger_results: List[Dict[str, Any]] = []
        archive_actions: List[Dict[str, Any]] = []
        quality_reviews: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []

        for path in file_paths:
            readiness = readiness_by_path[path]
            if readiness.get("status") != "ready":
                failure = {
                    "file": path,
                    "stage": "source_readiness",
                    "error": "source_not_local_or_unreadable",
                    "blocked_reason": readiness.get("blocked_reason", "source_not_local_or_unreadable"),
                    "readiness": readiness,
                }
                failures.append(failure)
                trace.append({
                    "stage": "source_readiness",
                    "file": path,
                    "status": "blocked",
                    "blocked_reason": failure["blocked_reason"],
                    "error": readiness.get("error", ""),
                })
                continue

            if self._use_archive_metadata_passthrough(path):
                extracted = self._metadata_passthrough_document(path, os.path.splitext(path)[1].lower())
            else:
                extracted = self.extract_document(path)
            if "error" in extracted:
                failure = {
                    "file": path,
                    "stage": "extract_document",
                    "status": extracted.get("status", "failed"),
                    "error": extracted["error"],
                }
                if extracted.get("blocked_reason"):
                    failure["blocked_reason"] = extracted["blocked_reason"]
                if extracted.get("ocr"):
                    failure["ocr"] = extracted["ocr"]
                failures.append(failure)
                trace_event = {
                    "stage": "extract_document",
                    "file": path,
                    "status": failure["status"],
                    "error": extracted["error"],
                }
                if failure.get("blocked_reason"):
                    trace_event["blocked_reason"] = failure["blocked_reason"]
                trace.append(trace_event)
                continue

            extracted_items.append(extracted)
            fields = self._apply_source_path_context(extracted.get("fields") or {}, path)
            extracted["fields"] = fields
            inferred_project_name = project_name or fields.get("project_name") or "未命名项目"
            facts = {key: value for key, value in fields.items() if key not in {"document_type", "source_filename"}}
            facts.setdefault("project_name", inferred_project_name)
            source_type = self._source_type_for_document(extracted.get("document_type", ""), extracted.get("filename", ""))
            accepted_facts, field_quality = filter_business_facts(
                facts,
                source_type=source_type,
                source_path=path,
            )
            extracted["field_quality"] = field_quality
            ledger = ProjectLedger(base_dir=self._ledger_base_for_source(path, fields))
            evidence = [
                {
                    "field": field,
                    "source_type": source_type,
                    "source_ref": path,
                    "extract_method": extracted.get("extract_method") or ("docx_text_table" if extracted.get("file_type") == ".docx" else "document_text"),
                    "confidence": self._evidence_confidence(field, path),
                    "summary": f"{extracted.get('filename', '')} extracted as {extracted.get('document_type', '')}",
                }
                for field in accepted_facts.keys()
            ]

            fact_gate = self._business_fact_gate(extracted)
            if fact_gate["trusted"] and accepted_facts:
                ledger_result = ledger.apply_patch({
                    "project_name": accepted_facts.get("project_name", inferred_project_name),
                    "source_type": source_type,
                    "facts": accepted_facts,
                    "evidence": evidence,
                    "skill": "data_cleaning_file_organization",
                    "actor": "data_cleaning_file_organization",
                })
                ledger_results.append(ledger_result)
            else:
                if fact_gate["trusted"] and not accepted_facts:
                    fact_gate = {
                        "trusted": False,
                        "blocked_reason": "no_accepted_business_facts",
                    }
                extracted["business_fact_gate"] = fact_gate
                quality_reviews.append({
                    "type": "extraction_quality_review",
                    "severity": "medium",
                    "project_name": inferred_project_name,
                    "file": path,
                    "reason": fact_gate["blocked_reason"],
                    "field_quality": field_quality,
                    "extract_method": extracted.get("extract_method", ""),
                    "recommended_action": "人工复核提取文本后再写入业务账本",
                })
                archive_only_facts = dict(accepted_facts) if accepted_facts else {"project_name": inferred_project_name}
                ledger_result = self._archive_only_ledger_result(inferred_project_name, archive_only_facts, evidence)

            extracted_path = os.path.join(extracted_dir, f"{self._structured_output_stem(path)}_extracted.json")
            self._save_structured_json(extracted_path, {
                "schema_version": "file_organization.extracted_document.v1",
                "run_id": run_id,
                "project_name": inferred_project_name,
                "source_file": path,
                "source_type": source_type,
                "extraction": extracted,
                "accepted_business_facts": accepted_facts,
                "field_quality": field_quality,
                "business_judgement": ledger_result.get("business_judgement", {}),
                "business_cases": self._business_cases_for_structured_output(
                    project_name=inferred_project_name,
                    source_file=path,
                    source_type=source_type,
                    facts=fields,
                    judgement=ledger_result.get("business_judgement", {}),
                ),
                "ledger_artifacts": {
                    "project_overview_md": ledger_result.get("markdown_path", ""),
                    "project_ledger_json": ledger_result.get("state_path", ""),
                },
                "processed_at": datetime.now().isoformat(),
            })
            structured_outputs.append(extracted_path)

            if fact_gate["trusted"]:
                patch_path = os.path.join(patches_dir, f"{self._safe_name(inferred_project_name)}_{self._safe_name(os.path.basename(path))}.json")
                self._save_structured_json(patch_path, {
                    "schema_version": "project_patch.v1",
                    "project_name": inferred_project_name,
                    "source_type": source_type,
                    "facts": accepted_facts,
                    "evidence": evidence,
                    "field_quality": field_quality,
                })

            action = self._build_archive_action(run_id, path, inferred_project_name, extracted, ledger_result)
            archive_actions.append(action)
            trace.append({"stage": "process_file", "file": path, "status": "success", "project_name": inferred_project_name})

        review_queue = self._build_review_queue(run_id, failures, archive_actions, ledger_results, quality_reviews)

        # ── 对抗性验证循环（Adversarial Verification Loop）──
        try:
            from tools.adversarial_verification import AdversarialVerification
            av = AdversarialVerification(workspace_dir=self.workspace_dir)
            verification_result = av.run(
                run_id=run_id,
                extracted_items=extracted_items,
                ledger_results=ledger_results,
                archive_actions=archive_actions,
            )
            # 将验证结果加入 review_queue
            if verification_result.get("overall_verdict") != "pass":
                verdict = verification_result.get("overall_verdict", "")
                block_reason = verification_result.get("block_reason", "")
                low_conf = verification_result.get("low_confidence_items", [])
                review_queue["items"].append({
                    "type": "adversarial_verification",
                    "severity": "high" if verdict == "needs_correction" else ("medium" if verdict == "pass_with_warnings" else "medium"),
                    "verdict": verdict,
                    "block_reason": block_reason,
                    "low_confidence_items": low_conf,
                    "recommended_action": "查看 runs/{}/adversarial_verification.json 了解详情".format(run_id),
                    "verification_path": os.path.join(self.workspace_dir, "runs", run_id, "adversarial_verification.json"),
                })
                review_queue["status"] = "needs_review"
        except Exception as e:
            # 对抗验证失败不应阻断主流程
            review_queue["items"].append({
                "type": "adversarial_verification_error",
                "severity": "low",
                "message": f"对抗性验证执行异常（已跳过）: {str(e)}",
                "recommended_action": "可忽略，主流程已继续",
            })

        artifacts = {
            "input_manifest": os.path.join(run_dir, "input_manifest.json"),
            "review_queue": os.path.join(run_dir, "review_queue.json"),
            "planned_archive_actions": os.path.join(run_dir, "planned_archive_actions.json"),
            "run_report": os.path.join(run_dir, "run_report.md"),
            "trace": os.path.join(run_dir, "trace.json"),
            "run_dir": run_dir,
        }
        self._save_structured_json(artifacts["input_manifest"], input_manifest)
        self._save_structured_json(artifacts["review_queue"], review_queue)
        self._save_structured_json(artifacts["planned_archive_actions"], {
            "schema_version": "archive_plan.v1",
            "run_id": run_id,
            "actions": archive_actions,
        })
        self._save_structured_json(artifacts["trace"], {
            "schema_version": "file_organization.trace.v1",
            "run_id": run_id,
            "events": trace,
        })
        self._write_run_report(artifacts["run_report"], run_id, len(file_paths), len(extracted_items), failures, review_queue, archive_actions)
        primary_project_name = self._first_project_name(archive_actions, structured_outputs)

        return {
            "schema_version": "file_organization.run.v1",
            "run_id": run_id,
            "status": "success" if extracted_items and not failures else ("partial" if extracted_items else "failed"),
            "project_name": primary_project_name,
            "processed": len(extracted_items),
            "failed": len(failures),
            "structured_outputs": structured_outputs,
            "failures": failures,
            "review_queue": review_queue,
            "archive_actions": archive_actions,
            "artifacts": artifacts,
        }

    def verify_file_organization_run(self, run_id: str) -> Dict[str, Any]:
        """Read-only verification over a prepared file-organization run package."""
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        if not os.path.isdir(run_dir):
            return {
                "schema_version": "adversarial_verification.v1",
                "status": "failed",
                "run_id": run_id,
                "error": f"Run directory not found: {run_dir}",
            }

        extracted_items: List[Dict[str, Any]] = []
        extracted_dir = os.path.join(run_dir, "extracted")
        if os.path.isdir(extracted_dir):
            for name in sorted(os.listdir(extracted_dir)):
                if not name.endswith(".json"):
                    continue
                payload = self._load_json_file(os.path.join(extracted_dir, name))
                extraction = payload.get("extraction")
                if isinstance(extraction, dict):
                    extracted_items.append(extraction)

        archive_actions: List[Dict[str, Any]] = []
        plan_path = os.path.join(run_dir, "planned_archive_actions.json")
        if os.path.exists(plan_path):
            plan = self._load_json_file(plan_path)
            actions = plan.get("actions", [])
            if isinstance(actions, list):
                archive_actions = actions

        return AdversarialAgent(workspace_dir=self.workspace_dir).review(
            run_id=run_id,
            extracted_items=extracted_items,
            ledger_results=[],
            archive_actions=archive_actions,
        )

    def audit_file_organization_run(self, run_id: str) -> Dict[str, Any]:
        """Read-only audit over a prepared and verified file-organization run package."""
        return AuditAgent(workspace_dir=self.workspace_dir).review_run(run_id)

    def prepare_feedback_form(self, run_id: str) -> Dict[str, Any]:
        """Write a human-editable feedback form for a prepared run package."""
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        if not os.path.isdir(run_dir):
            return {
                "schema_version": "feedback_form.prepare.v1",
                "status": "failed",
                "run_id": run_id,
                "error": f"Run directory not found: {run_dir}",
            }

        review_queue = self._load_json_file(os.path.join(run_dir, "review_queue.json")) if os.path.exists(os.path.join(run_dir, "review_queue.json")) else {}
        audit_review = self._load_json_file(os.path.join(run_dir, "audit_review.json")) if os.path.exists(os.path.join(run_dir, "audit_review.json")) else {}
        adversarial_verification = self._load_json_file(os.path.join(run_dir, "adversarial_verification.json")) if os.path.exists(os.path.join(run_dir, "adversarial_verification.json")) else {}
        form = build_feedback_form(
            run_id=run_id,
            review_queue=review_queue,
            audit_review=audit_review,
            adversarial_verification=adversarial_verification,
        )

        artifacts = {
            "feedback_form_json": os.path.join(run_dir, "feedback_form.json"),
            "feedback_form_md": os.path.join(run_dir, "feedback_form.md"),
        }
        self._save_structured_json(artifacts["feedback_form_json"], form)
        with open(artifacts["feedback_form_md"], "w", encoding="utf-8") as f:
            f.write(render_feedback_form_markdown(form))

        return {
            "schema_version": "feedback_form.prepare.v1",
            "status": "success",
            "run_id": run_id,
            "item_count": len(form["items"]),
            "required_feedback_items": form["required_feedback_items"],
            "artifacts": artifacts,
            "boundary": {
                "moved_files": False,
                "updated_business_ledger": False,
                "archive_plan_executed": False,
            },
        }

    def apply_feedback_form(
        self,
        run_id: str,
        feedback_form: Optional[Dict[str, Any]] = None,
        feedback_form_path: str = "",
        generate_tests: bool = True,
    ) -> Dict[str, Any]:
        """Apply a filled feedback_form.v1 and optionally generate candidate tests."""
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        if not os.path.isdir(run_dir):
            return {
                "schema_version": "feedback_form.apply.v1",
                "status": "failed",
                "run_id": run_id,
                "error": f"Run directory not found: {run_dir}",
            }
        if feedback_form is None:
            form_path = feedback_form_path or os.path.join(run_dir, "feedback_form.json")
            if not os.path.exists(form_path):
                return {
                    "schema_version": "feedback_form.apply.v1",
                    "status": "failed",
                    "run_id": run_id,
                    "error": f"Feedback form not found: {form_path}",
                }
            feedback_form = self._load_json_file(form_path)

        decisions = feedback_decisions_from_form(feedback_form)
        feedback_apply = self.apply_feedback_decisions(run_id=run_id, feedback_decisions=decisions)
        candidate_tests = None
        if generate_tests and feedback_apply.get("accepted", 0):
            candidate_tests = self.generate_candidate_tests(run_id)

        artifacts = dict(feedback_apply.get("artifacts") or {})
        if candidate_tests:
            artifacts.update(candidate_tests.get("artifacts") or {})

        return {
            "schema_version": "feedback_form.apply.v1",
            "status": feedback_apply.get("status", "failed"),
            "run_id": run_id,
            "accepted": feedback_apply.get("accepted", 0),
            "failed": feedback_apply.get("failed", 0),
            "feedback_decisions": decisions,
            "feedback_apply": feedback_apply,
            "candidate_tests": candidate_tests,
            "artifacts": artifacts,
            "boundary": {
                "moved_files": False,
                "updated_business_ledger": False,
                "archive_plan_executed": False,
            },
        }

    def apply_feedback_decisions(self, run_id: str, feedback_decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Persist structured human feedback for later rule/test authoring.

        This does not move files, write business ledgers, or modify source artifacts.
        """
        if isinstance(feedback_decisions, dict):
            feedback_decisions = [feedback_decisions]
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        os.makedirs(run_dir, exist_ok=True)

        accepted: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        for index, raw in enumerate(feedback_decisions or [], 1):
            try:
                accepted.append(normalize_feedback_decision(raw, run_id=run_id, index=index))
            except FeedbackValidationError as exc:
                errors.append({
                    "index": index,
                    "error": exc.code,
                    "message": str(exc),
                    "item": raw,
                })

        events = [feedback_event(decision) for decision in accepted]
        rule_candidates = build_rule_candidates(accepted)
        parser_test_candidates = build_parser_test_candidates(accepted)

        artifacts = {
            "human_feedback_decisions": os.path.join(run_dir, "human_feedback_decisions.json"),
            "feedback_events": os.path.join(run_dir, "feedback_events.jsonl"),
            "rule_candidates": os.path.join(run_dir, "rule_candidates.json"),
            "parser_test_candidates": os.path.join(run_dir, "parser_test_candidates.json"),
        }
        review_queue_updates = self._apply_feedback_to_review_queue(run_dir, accepted)
        if review_queue_updates.get("artifact"):
            artifacts["review_queue"] = review_queue_updates["artifact"]
        self._save_structured_json(artifacts["human_feedback_decisions"], {
            "schema_version": "human_feedback.decisions.v1",
            "run_id": run_id,
            "decisions": accepted,
            "errors": errors,
        })
        with open(artifacts["feedback_events"], "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        self._save_structured_json(artifacts["rule_candidates"], {
            "schema_version": "rule_candidates.v1",
            "run_id": run_id,
            "items": rule_candidates,
        })
        self._save_structured_json(artifacts["parser_test_candidates"], {
            "schema_version": "parser_test_candidates.v1",
            "run_id": run_id,
            "items": parser_test_candidates,
        })

        return {
            "schema_version": "human_feedback.apply.v1",
            "status": "success" if accepted and not errors else ("partial" if accepted else "failed"),
            "run_id": run_id,
            "accepted": len(accepted),
            "failed": len(errors),
            "decisions": accepted,
            "errors": errors,
            "rule_candidates": rule_candidates,
            "parser_test_candidates": parser_test_candidates,
            "review_queue_updates": review_queue_updates,
            "artifacts": artifacts,
            "boundary": {
                "moved_files": False,
                "updated_business_ledger": False,
                "archive_plan_executed": False,
            },
        }

    def _apply_feedback_to_review_queue(
        self,
        run_dir: str,
        feedback_decisions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        review_queue_path = os.path.join(run_dir, "review_queue.json")
        if not os.path.exists(review_queue_path):
            return {
                "status": "skipped",
                "updated": 0,
                "pending": [],
                "artifact": "",
            }

        queue = self._load_json_file(review_queue_path)
        items = queue.get("items", [])
        if not isinstance(items, list):
            return {
                "status": "failed",
                "updated": 0,
                "pending": [],
                "artifact": review_queue_path,
                "error": "review_queue.items must be a list",
            }

        by_item_id: Dict[str, List[Dict[str, Any]]] = {}
        for decision in feedback_decisions:
            by_item_id.setdefault(str(decision.get("item_id") or ""), []).append(decision)

        updated = 0
        pending: List[str] = []
        now = datetime.now().isoformat()
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("item_id") or "")
            decisions = by_item_id.get(item_id, [])
            if decisions:
                item["feedback_status"] = "feedback_received"
                item["feedback_ids"] = [decision["feedback_id"] for decision in decisions]
                item["feedback_decisions"] = [decision["decision"] for decision in decisions]
                item["feedback_updated_at"] = now
                updated += 1
            else:
                item.setdefault("feedback_status", "pending")
                pending.append(item_id)

        queue["items"] = items
        queue["status"] = "reviewed" if items and not pending else ("needs_review" if items else "clear")
        queue["feedback_summary"] = {
            "updated": updated,
            "pending": pending,
            "updated_at": now,
        }
        self._save_structured_json(review_queue_path, queue)
        return {
            "status": "updated",
            "updated": updated,
            "pending": pending,
            "artifact": review_queue_path,
        }

    def generate_candidate_tests(self, run_id: str) -> Dict[str, Any]:
        """Generate runnable test-draft artifacts from feedback candidates.

        The generated files stay inside the run package. They are review inputs,
        not automatically promoted repository tests.
        """
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        if not os.path.isdir(run_dir):
            return {
                "schema_version": "candidate_test_generation.v1",
                "status": "failed",
                "run_id": run_id,
                "error": f"Run directory not found: {run_dir}",
            }

        parser_candidates = self._load_candidate_items(os.path.join(run_dir, "parser_test_candidates.json"))
        rule_candidates = self._load_candidate_items(os.path.join(run_dir, "rule_candidates.json"))

        generated_dir = os.path.join(run_dir, "generated_tests")
        artifacts = {
            "parser_candidate_tests": os.path.join(generated_dir, "parser_candidate_tests.py"),
            "rule_candidate_tests": os.path.join(generated_dir, "rule_candidate_tests.py"),
            "test_manifest": os.path.join(generated_dir, "test_manifest.json"),
        }
        os.makedirs(generated_dir, exist_ok=True)
        with open(artifacts["parser_candidate_tests"], "w", encoding="utf-8") as f:
            f.write(render_parser_candidate_tests(parser_candidates))
        with open(artifacts["rule_candidate_tests"], "w", encoding="utf-8") as f:
            f.write(render_rule_candidate_tests(rule_candidates))

        manifest = build_candidate_test_manifest(
            run_id=run_id,
            parser_candidates=parser_candidates,
            rule_candidates=rule_candidates,
            artifacts=artifacts,
        )
        self._save_structured_json(artifacts["test_manifest"], manifest)
        return manifest

    def _load_candidate_items(self, path: str) -> List[Dict[str, Any]]:
        if not os.path.exists(path):
            return []
        payload = self._load_json_file(path)
        items = payload.get("items", [])
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def _first_project_name(self, archive_actions: List[Dict[str, Any]], structured_outputs: List[str]) -> str:
        for action in archive_actions:
            name = action.get("project_name")
            if name and name != "未命名项目":
                return str(name)
        for output_path in structured_outputs:
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            name = data.get("project_name")
            if name and name != "未命名项目":
                return str(name)
        return "未命名项目"

    def _resolve_structured_source_files(
        self,
        file_paths: Optional[List[str]],
        source_dir: str,
        skip_backups: bool,
    ) -> List[Dict[str, Any]]:
        if isinstance(file_paths, str):
            file_paths = [file_paths]
        resolved: List[Dict[str, Any]] = []
        for path in file_paths or []:
            resolved.append({"path": str(path), "status": "ready"})
        if source_dir:
            if not os.path.isdir(source_dir):
                resolved.append({"file": source_dir, "status": "skipped", "reason": "source_dir_missing"})
            else:
                for name in sorted(os.listdir(source_dir)):
                    path = os.path.join(source_dir, name)
                    if not os.path.isfile(path) or name.startswith("."):
                        continue
                    if skip_backups and (".bak_" in name or name.endswith(".bak")):
                        resolved.append({"file": path, "status": "skipped", "reason": "backup_file_skipped"})
                        continue
                    resolved.append({"path": path, "status": "ready"})
        seen = set()
        deduped: List[Dict[str, Any]] = []
        for item in resolved:
            key = item.get("path") or item.get("file")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _first_project_name_from_documents(documents: List[Dict[str, Any]]) -> str:
        for document in documents:
            fields = document.get("fields") or {}
            name = fields.get("project_name")
            if name and name != "未命名项目":
                return str(name)
        return ""

    def _use_archive_metadata_passthrough(self, source_path: str) -> bool:
        ext = os.path.splitext(source_path)[1].lower()
        if ext not in {".pdf", ".png", ".jpg", ".jpeg", ".ofd"}:
            return False
        return bool(self._business_phase_from_path(source_path) and self._project_name_from_phase_path(self._path_parts(source_path)))

    def _business_fact_gate(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        """Decide whether extracted fields are allowed to update business ledgers."""
        if extracted.get("extract_method") == "metadata_passthrough":
            return {
                "trusted": False,
                "blocked_reason": "metadata_passthrough_archive_only",
            }
        quality = ((extracted.get("ocr") or {}).get("quality") or {})
        if extracted.get("needs_human_review") or quality.get("needs_human_review"):
            return {
                "trusted": False,
                "blocked_reason": "low_quality_extraction",
                "quality_flags": quality.get("flags", []),
            }
        return {"trusted": True, "blocked_reason": ""}

    def _archive_only_ledger_result(
        self,
        project_name: str,
        facts: Dict[str, Any],
        evidence: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        judgement = BidProjectRuleEngine().evaluate(facts, evidence_index=evidence)
        return {
            "project_name": project_name,
            "status": "skipped",
            "skip_reason": "archive_only_extraction",
            "business_judgement": judgement,
            "markdown_path": "",
            "state_path": "",
        }

    def _business_cases_for_structured_output(
        self,
        project_name: str,
        source_file: str,
        source_type: str,
        facts: Dict[str, Any],
        judgement: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        flags = set(judgement.get("data_quality_flags") or [])
        status_conflict = bool(flags.intersection({"报名状态与中标状态冲突", "执行阶段与中标状态冲突"}))
        if not status_conflict:
            return []
        return [{
            "schema_version": "business_case.v1",
            "case_type": "status_conflict",
            "entities": [{
                "project_name": project_name,
                "source_file": source_file,
                "source_type": source_type,
                "lifecycle_stage": facts.get("lifecycle_stage", ""),
                "registration_status": facts.get("registration_status", ""),
                "bid_status": facts.get("bid_status", ""),
                "contract_status": facts.get("contract_status", ""),
            }],
            "signals": {
                "status_conflict": True,
                "data_quality_flags": judgement.get("data_quality_flags", []),
                "risk_reasons": judgement.get("risk_reasons", []),
            },
            "output": {
                "decision": "needs_review",
                "reason": "状态冲突，业务层只输出结构化复核信号，不自动合并、不自动覆盖、不驱动文件移动",
            },
        }]

    def _ledger_base_for_source(self, source_path: str, fields: Dict[str, Any]) -> str:
        phase = self._business_phase_from_path(source_path)
        if not phase:
            status = str(fields.get("bid_status") or "")
            lifecycle_stage = str(fields.get("lifecycle_stage") or "")
            if "弃标" in status or "丢标" in status or "未中标" in status or lifecycle_stage in {"closed", "closed_lost"}:
                phase = "项目丢标"
            else:
                phase = "项目投标"
        return os.path.join(self.project_files_dir, phase)

    def _apply_source_path_context(self, fields: Dict[str, Any], source_path: str) -> Dict[str, Any]:
        enriched = dict(fields)
        parts_list = self._path_parts(source_path)
        parts = set(parts_list)
        project_name_from_path = self._project_name_from_phase_path(parts_list)
        if project_name_from_path:
            enriched["project_name"] = project_name_from_path
        if "项目丢标" in parts:
            if str(enriched.get("bid_status") or "") not in {"已丢标", "未中标"}:
                enriched["bid_status"] = "已弃标"
                enriched["registration_status"] = "已弃标"
                enriched["closed_reason_type"] = "abandoned_by_us"
            else:
                enriched["bid_status"] = "已丢标"
                enriched["closed_reason_type"] = "lost_to_competitor"
            enriched["lifecycle_stage"] = "closed"
        elif "项目执行" in parts:
            enriched["lifecycle_stage"] = "execution"
        return enriched

    @staticmethod
    def _project_name_from_phase_path(parts: List[str]) -> str:
        for phase in ("项目投标", "项目执行", "项目丢标"):
            if phase in parts:
                index = parts.index(phase)
                if index + 1 < len(parts):
                    project_name = parts[index + 1].strip()
                    if project_name:
                        return project_name
        return ""

    @staticmethod
    def _business_phase_from_path(source_path: str) -> str:
        parts = DataCleaningTools._path_parts(source_path)
        for phase in ("项目投标", "项目执行", "项目丢标"):
            if phase in parts:
                return phase
        return ""

    @staticmethod
    def _path_parts(source_path: str) -> List[str]:
        return [part for part in re.split(r"[\\/]+", os.path.normpath(str(source_path))) if part]

    def _structured_output_stem(self, source_path: str) -> str:
        parent = os.path.basename(os.path.dirname(source_path))
        basename = os.path.basename(source_path)
        if parent and basename == "项目记录.md":
            return f"{self._safe_name(parent)}_{self._safe_name(basename)}"
        return self._safe_name(basename)

    def _evidence_confidence(self, field: str, source_path: str) -> float:
        parts = set(self._path_parts(source_path))
        if "项目丢标" in parts and field in {"bid_status", "lifecycle_stage"}:
            return 0.95
        if field in {"project_name", "document_type"}:
            return 0.86
        return 0.78

    def apply_human_review(self, review_decisions: List[Dict[str, Any]], run_id: str = "") -> Dict[str, Any]:
        """Apply human review decisions as highest-weight ledger corrections."""
        if isinstance(review_decisions, dict):
            review_decisions = [review_decisions]

        results = []
        rule_candidates = []
        for decision in review_decisions:
            project_name = decision.get("project_name") or (decision.get("facts") or {}).get("project_name")
            facts = decision.get("facts") or {}
            if not project_name:
                results.append({"status": "failed", "error": "missing project_name", "decision": decision})
                continue
            facts.setdefault("project_name", project_name)
            archive_phase = decision.get("archive_phase", "")
            ledger_base = os.path.join(self.project_files_dir, archive_phase) if archive_phase else self.project_files_dir
            ledger = ProjectLedger(base_dir=ledger_base)
            evidence = [
                {
                    "field": field,
                    "source_type": "human_correction",
                    "source_ref": f"human_review:{run_id or 'ad_hoc'}",
                    "extract_method": "manual_review",
                    "confidence": 1.0,
                    "summary": decision.get("reason", "人工复核修正"),
                }
                for field in facts.keys()
            ]
            ledger_result = ledger.apply_patch({
                "project_name": project_name,
                "source_type": "human_correction",
                "facts": facts,
                "evidence": evidence,
                "skill": "data_cleaning_file_organization",
                "actor": "human_review",
            })
            # ── 错误案例收集（Error Case Collector）──
            try:
                from tools.adversarial_verification import ErrorCaseCollector
                collector = ErrorCaseCollector(workspace_dir=self.workspace_dir)
                # 尝试加载原始提取结果用于 diff
                extracted_before = None
                if run_id:
                    extracted_dir = os.path.join(self.workspace_dir, "runs", run_id, "extracted")
                    if os.path.exists(extracted_dir):
                        for fname in os.listdir(extracted_dir):
                            if fname.endswith("_extracted.json"):
                                try:
                                    with open(os.path.join(extracted_dir, fname), "r", encoding="utf-8") as f:
                                        extracted_data = json.load(f)
                                    ex_project = extracted_data.get("project_name", "")
                                    if ex_project == project_name or not project_name:
                                        extracted_before = extracted_data.get("extraction", {})
                                        break
                                except Exception:
                                    pass
                collector.collect(run_id=run_id, decision=decision, extracted_before=extracted_before)
            except Exception:
                pass  # 错误收集失败不应阻断主流程
            results.append({
                "status": "success",
                "project_name": project_name,
                "facts": facts,
                "business_judgement": ledger_result.get("business_judgement", {}),
                "artifacts": {
                    "project_overview_md": ledger_result["markdown_path"],
                    "project_ledger_json": ledger_result["state_path"],
                },
            })
            if any(field in facts for field in ["bid_status", "contract_status", "registration_status"]):
                rule_candidates.append({
                    "schema_version": "rule_candidate.v1",
                    "project_name": project_name,
                    "reviewed_fields": sorted(facts.keys()),
                    "reason": decision.get("reason", ""),
                    "status": "pending_rule_approval",
                    "requires_test": True,
                })

        artifacts = {}
        if run_id:
            run_dir = os.path.join(self.workspace_dir, "runs", run_id)
            os.makedirs(run_dir, exist_ok=True)
            artifacts["review_decisions"] = os.path.join(run_dir, "review_decisions.json")
            artifacts["rule_candidates"] = os.path.join(run_dir, "rule_candidates.json")
            self._save_structured_json(artifacts["review_decisions"], {
                "schema_version": "human_review.decisions.v1",
                "run_id": run_id,
                "decisions": review_decisions,
                "results": results,
            })
            self._save_structured_json(artifacts["rule_candidates"], {
                "schema_version": "rule_candidates.v1",
                "run_id": run_id,
                "items": rule_candidates,
            })

        return {
            "schema_version": "human_review.apply.v1",
            "status": "success" if results and all(r["status"] == "success" for r in results) else "partial",
            "reviewed": len([r for r in results if r["status"] == "success"]),
            "failed": len([r for r in results if r["status"] != "success"]),
            "results": results,
            "rule_candidates": rule_candidates,
            "artifacts": artifacts,
        }

    @staticmethod
    def _is_reparse_point(path: str) -> bool:
        try:
            details = os.lstat(path)
        except OSError:
            return False
        attributes = getattr(details, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)

    @staticmethod
    def _blocked_archive_execution(
        run_id: str, blocker: str, detail: str = "", *, failed: int = 0,
    ) -> Dict[str, Any]:
        gate = {
            "schema_version": "archive_intent_execution_gate.v1",
            "status": "blocked",
            "blockers": [blocker],
        }
        if detail:
            gate["detail"] = detail[:256]
        return {
            "schema_version": "archive_plan.execute.v1",
            "status": "blocked",
            "run_id": run_id,
            "moved": 0,
            "failed": failed,
            "results": [],
            "gate": gate,
        }

    def _runs_root(self) -> Path:
        return Path(self.workspace_dir).expanduser().resolve() / "runs"

    def _resolve_archive_run_dir(self, run_id: str) -> str:
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
            raise ValueError("run_id must be a bounded ASCII identifier")
        runs_root = self._runs_root()
        if self._is_reparse_point(str(runs_root)):
            raise ValueError("runs root must not be a symlink or reparse point")
        run_dir = runs_root / run_id
        if self._is_reparse_point(str(run_dir)):
            raise ValueError("run directory must not be a symlink or reparse point")
        resolved = run_dir.resolve()
        if resolved.parent != runs_root:
            raise ValueError("run directory escapes workspace/runs")
        return str(resolved)

    def _intent_registry_path(self, run_id: str) -> Path:
        return self._runs_root() / ".archive_intent_registry" / f"{run_id}.json"

    def _is_registered_archive_intent_run(self, run_id: str) -> bool:
        marker = self._intent_registry_path(run_id)
        registry = marker.parent
        if self._is_reparse_point(str(registry)) or self._is_reparse_point(str(marker)):
            return True
        return marker.is_file()

    def _claim_archive_intent_run(self) -> tuple[str, str]:
        runs_root = self._runs_root()
        runs_root.mkdir(parents=True, exist_ok=True)
        if self._is_reparse_point(str(runs_root)):
            raise ValueError("runs root must not be a symlink or reparse point")
        registry = runs_root / ".archive_intent_registry"
        registry.mkdir(exist_ok=True)
        if self._is_reparse_point(str(registry)):
            raise ValueError("archive intent registry must not be a reparse point")
        while True:
            run_id = f"run_{uuid.uuid4().hex}"
            run_dir = runs_root / run_id
            try:
                run_dir.mkdir(exist_ok=False)
            except FileExistsError:
                continue
            break
        self._save_structured_json(str(registry / f"{run_id}.json"), {
            "schema_version": "archive_intent_run_registration.v1",
            "run_id": run_id,
        })
        return run_id, str(run_dir)

    def execute_archive_plan(self, run_id: str, confirmed: bool = False) -> Dict[str, Any]:
        """Execute only legacy local plans; archive intents are never authorization."""
        try:
            run_dir = self._resolve_archive_run_dir(run_id)
        except ValueError as exc:
            return self._blocked_archive_execution(str(run_id), "invalid_run_boundary", str(exc))
        plan_path = os.path.join(run_dir, "planned_archive_actions.json")
        if not os.path.isfile(plan_path) or self._is_reparse_point(plan_path):
            return self._blocked_archive_execution(run_id, "archive_plan_not_found")
        try:
            plan = self._load_json_file(plan_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return self._blocked_archive_execution(run_id, "archive_plan_invalid", str(exc))
        actions = plan.get("actions")
        if plan.get("run_id") != run_id or not isinstance(actions, list) or any(
            not isinstance(action, dict)
            or ("run_id" in action and action.get("run_id") != run_id)
            for action in actions
        ):
            return self._blocked_archive_execution(run_id, "archive_plan_run_id_mismatch")

        registered_intent_run = self._is_registered_archive_intent_run(run_id)
        if re.fullmatch(r"run_[0-9a-f]{32}", run_id) and not registered_intent_run:
            return self._blocked_archive_execution(run_id, "archive_intent_run_not_registered")
        strict_intent_plan = registered_intent_run or plan.get("archive_intent_required") is True or any(
            "archive_intent" in action
            or "archive_intent_ref" in action
            or any(str(value).startswith(("STORAGE_BINDING.", "BUSINESS_CONTEXT.", "DOCUMENT_INTERPRETATION.", "ARCHIVE_TARGET.")) for value in action.get("blockers", []))
            for action in actions
        )
        if strict_intent_plan:
            return self._blocked_archive_execution(run_id, "archive_intent_not_executable", failed=len(actions))
        if not confirmed:
            return {
                "schema_version": "archive_plan.execute.v1",
                "status": "needs_confirmation",
                "run_id": run_id,
                "planned": len(actions),
                "message": "Archive plan prepared but not executed. Call with confirmed=True to move files.",
            }

        gate = self._evaluate_archive_execution_gate(run_id=run_id, run_dir=run_dir, confirmed=confirmed)
        if gate["status"] != "passed":
            return {
                "schema_version": "archive_plan.execute.v1",
                "status": "blocked",
                "run_id": run_id,
                "moved": 0,
                "failed": len(actions),
                "results": [],
                "gate": gate,
                "artifacts": {
                    "archive_execution_gate": os.path.join(run_dir, "archive_execution_gate.json"),
                },
            }

        results = []
        review_decisions_path = os.path.join(run_dir, "review_decisions.json")
        has_human_review = os.path.exists(review_decisions_path)
        for action in actions:
            source = action.get("source_file", "")
            target = action.get("target_path", "")
            if action.get("status") == "already_archived":
                results.append({
                    "status": "success",
                    "source_file": source,
                    "archived_path": target,
                    "archive_record": {
                        "original_path": source,
                        "archived_path": target,
                        "archive_status": "already_archived",
                        "run_id": run_id,
                    },
                })
                continue
            hard_blockers = [b for b in action.get("blockers", []) if b in {"source_missing", "target_exists", "unknown_project"}]
            if hard_blockers:
                results.append({"status": "blocked", "source_file": source, "blockers": hard_blockers})
                continue
            if "human_review_recommended" in action.get("blockers", []) and not has_human_review:
                results.append({
                    "status": "blocked",
                    "source_file": source,
                    "blockers": ["human_review_required"],
                    "message": "Archive action requires apply_human_review before confirmed execution.",
                })
                continue
            source_readiness = probe_readable_file(source)
            if source_readiness.get("status") != "ready":
                results.append({
                    "status": "blocked",
                    "source_file": source,
                    "blockers": ["source_not_local_or_unreadable"],
                    "blocked_reason": source_readiness.get("blocked_reason", "source_not_local_or_unreadable"),
                    "error": source_readiness.get("error", ""),
                    "readiness": source_readiness,
                })
                continue
            if os.path.exists(target):
                results.append({"status": "blocked", "source_file": source, "blockers": ["target_exists"]})
                continue
            target_dir_readiness = probe_writable_dir(os.path.dirname(target))
            if target_dir_readiness.get("status") != "ready":
                results.append({
                    "status": "blocked",
                    "source_file": source,
                    "target_path": target,
                    "blockers": ["target_not_writable"],
                    "blocked_reason": target_dir_readiness.get("blocked_reason", "target_not_writable"),
                    "error": target_dir_readiness.get("error", ""),
                    "target_readiness": target_dir_readiness,
                })
                continue
            shutil.move(source, target)
            archive_record = {
                "original_path": source,
                "archived_path": target,
                "document_type": action.get("document_type", ""),
                "archived_at": datetime.now().isoformat(),
                "run_id": run_id,
            }
            archive_phase = (action.get("archive_decision") or {}).get("archive_phase") or self._business_phase_from_path(target) or "项目投标"
            ledger = ProjectLedger(base_dir=os.path.join(self.project_files_dir, archive_phase))
            ledger_result = ledger.record_archive_event(action.get("project_name") or "未命名项目", {
                "original_path": source,
                "archived_path": target,
                "document_type": action.get("document_type", ""),
                "archive_phase": archive_phase,
                "run_id": run_id,
                "archived_at": archive_record["archived_at"],
                "skill": "data_cleaning_file_organization",
                "actor": "archive_executor",
            })
            results.append({
                "status": "success",
                "source_file": source,
                "archived_path": target,
                "archive_record": archive_record,
                "project_overview_md": ledger_result["markdown_path"],
            })

        artifacts = {
            "archive_result": os.path.join(run_dir, "archive_result.json"),
            "run_report": os.path.join(run_dir, "run_report.md"),
        }
        self._save_structured_json(artifacts["archive_result"], {
            "schema_version": "archive_result.v1",
            "run_id": run_id,
            "results": results,
        })
        self._append_archive_report(artifacts["run_report"], results)
        moved = len([item for item in results if item["status"] == "success"])
        failed = len([item for item in results if item["status"] != "success"])
        return {
            "schema_version": "archive_plan.execute.v1",
            "status": "success" if moved and not failed else ("partial" if moved else "failed"),
            "run_id": run_id,
            "moved": moved,
            "failed": failed,
            "results": results,
            "gate": gate,
            "artifacts": artifacts,
        }

    def _evaluate_archive_execution_gate(self, run_id: str, run_dir: str, confirmed: bool) -> Dict[str, Any]:
        audit_path = os.path.join(run_dir, "audit_review.json")
        review_queue_path = os.path.join(run_dir, "review_queue.json")
        audit_review = self._load_json_file(audit_path) if os.path.exists(audit_path) else {}
        review_queue = self._load_json_file(review_queue_path) if os.path.exists(review_queue_path) else {}
        if any(
            payload and payload.get("run_id") != run_id
            for payload in (audit_review, review_queue)
        ):
            return {
                "schema_version": "archive_execution_gate.v1",
                "status": "blocked",
                "run_id": run_id,
                "blockers": ["artifact_run_id_mismatch"],
                "pending_required_feedback_items": [],
            }
        gate = evaluate_archive_execution_gate(
            run_id=run_id,
            confirmed=confirmed,
            audit_review=audit_review,
            review_queue=review_queue,
        )
        self._save_structured_json(os.path.join(run_dir, "archive_execution_gate.json"), gate)
        return gate

    def _build_archive_action(
        self,
        run_id: str,
        source_file: str,
        project_name: str,
        extracted: Dict[str, Any],
        ledger_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        judgement = ledger_result.get("business_judgement", {})
        decision = evaluate_archive_decision(
            source_file=source_file,
            extracted=extracted,
            business_judgement=judgement,
            project_files_dir=self.project_files_dir,
        )
        document_type = decision.get("document_type") or extracted.get("document_type") or "未分类"
        target_dir = decision["target_dir"]
        target_path = decision["target_path"]
        project_name = decision.get("subject_name") or project_name
        blockers = list(decision.get("blockers", []))
        fact_gate = extracted.get("business_fact_gate") or {}
        if fact_gate.get("blocked_reason"):
            decision.setdefault("reasons", []).append(fact_gate["blocked_reason"])

        if decision.get("archive_phase") is None:
            # archive_decision 返回了未决标记（如 PM 内部文档归档待重做），
            # target_dir / target_path 暂时为 None，等 phase 重设计后再算。
            proposed_name = ""
            target_abs = ""
            already_archived = False
        else:
            proposed_name = os.path.basename(target_path)
            target_abs = os.path.normcase(os.path.abspath(target_path))

        source_abs = os.path.normcase(os.path.abspath(source_file))
        if target_path is not None:
            already_archived = source_abs == target_abs
            if os.path.exists(target_path) and not already_archived:
                blockers.append("target_exists")
        else:
            already_archived = False

        if not os.path.exists(source_file):
            blockers.append("source_missing")
        return {
            "schema_version": "archive_action.v1",
            "run_id": run_id,
            "status": "already_archived" if already_archived and not blockers else ("ready" if not blockers else "needs_review"),
            "source_file": source_file,
            "project_name": project_name,
            "document_type": document_type,
            "proposed_name": proposed_name,
            "target_dir": target_dir,
            "target_path": target_path,
            "blockers": blockers,
            "business_judgement": judgement,
            "archive_decision": decision,
        }

    def _build_review_queue(
        self,
        run_id: str,
        failures: List[Dict[str, Any]],
        archive_actions: List[Dict[str, Any]],
        ledger_results: List[Dict[str, Any]],
        quality_reviews: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        items = []
        items.extend(quality_reviews or [])
        for failure in failures:
            items.append({
                "type": "extraction_failure",
                "severity": "high",
                "file": failure.get("file"),
                "reason": failure.get("error"),
                "recommended_action": "人工检查文件是否可读或转换格式后重试",
            })
        for result in ledger_results:
            judgement = result.get("business_judgement", {})
            if judgement.get("human_review_required"):
                items.append({
                    "type": "business_judgement_review",
                    "severity": judgement.get("risk_level", "unknown"),
                    "project_name": result.get("project_name"),
                    "missing_fields": judgement.get("missing_fields", []),
                    "risk_reasons": judgement.get("risk_reasons", []),
                    "recommended_actions": judgement.get("next_actions", []),
                    "project_overview_md": result.get("markdown_path"),
                })
        for action in archive_actions:
            if action.get("status") not in {"ready", "already_archived"}:
                items.append({
                    "type": "archive_action_review",
                    "severity": "medium",
                    "project_name": action.get("project_name"),
                    "source_file": action.get("source_file"),
                    "target_path": action.get("target_path"),
                    "blockers": action.get("blockers", []),
                    "recommended_action": "确认归档计划后再执行 execute_archive_plan",
                })
        return normalize_review_queue(run_id=run_id, raw_items=items)

    def _write_run_report(
        self,
        report_path: str,
        run_id: str,
        total: int,
        processed: int,
        failures: List[Dict[str, Any]],
        review_queue: Dict[str, Any],
        archive_actions: List[Dict[str, Any]],
    ) -> None:
        lines = [
            f"# 文件整理运行报告：{run_id}",
            "",
            f"- 输入文件数：{total}",
            f"- 结构化成功：{processed}",
            f"- 失败：{len(failures)}",
            f"- 复核项：{len(review_queue.get('items', []))}",
            f"- 归档计划：{len(archive_actions)}",
            "",
            "## 失败项",
        ]
        if failures:
            for failure in failures:
                lines.append(f"- {failure.get('file')}: {failure.get('error')}")
        else:
            lines.append("- 无")
        lines.extend(["", "## 人工复核队列"])
        if review_queue.get("items"):
            for item in review_queue.get("items", []):
                title = item.get("project_name") or item.get("file")
                detail = item.get("reason") or item.get("recommended_action") or ", ".join(item.get("recommended_actions", []))
                lines.append(f"- [{item.get('type')}] {title}: {detail}")
        else:
            lines.append("- 无")
        lines.extend(["", "## 归档计划"])
        if archive_actions:
            for action in archive_actions:
                lines.append(f"- {action.get('status')}: {action.get('source_file')} -> {action.get('target_path')}")
        else:
            lines.append("- 无")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _append_archive_report(self, report_path: str, results: List[Dict[str, Any]]) -> None:
        lines = ["", "## 归档执行结果"]
        for item in results:
            if item.get("status") == "success":
                lines.append(f"- success: {item.get('source_file')} -> {item.get('archived_path')}")
            else:
                lines.append(f"- {item.get('status')}: {item.get('source_file')} {item.get('error') or item.get('blockers')}")
        with open(report_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def import_project_detail_workbook(self, file_path: str) -> Dict[str, Any]:
        """Import 项目明细表.xlsx and update one ledger per project.

        The workbook is treated as an xlsx_summary source. It can seed and
        update project master data, but higher-weight evidence such as CRM/BPM
        readback, contracts, and human corrections can override it later.
        """
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}

        try:
            from openpyxl import load_workbook
        except ImportError:
            return {"error": "openpyxl not installed. Run: pip install openpyxl"}

        wb = load_workbook(file_path, data_only=True)
        if "项目明细表" not in wb.sheetnames:
            return {"error": "Workbook missing required sheet: 项目明细表"}

        detail_rows = self._worksheet_records(wb["项目明细表"])
        execution_rows = self._worksheet_records(wb["项目执行"]) if "项目执行" in wb.sheetnames else []
        execution_by_key = self._execution_rows_by_project(execution_rows)

        ledger = ProjectLedger(base_dir=self.project_files_dir)
        structured_dir = os.path.join(self.workspace_dir, "structured_workbooks", self._safe_name(os.path.basename(file_path)))
        os.makedirs(structured_dir, exist_ok=True)

        projects = []
        failed = []
        for row_index, row in enumerate(detail_rows, start=2):
            facts = self._project_detail_facts(row)
            project_name = facts.get("project_name")
            if not project_name:
                failed.append({"row": row_index, "error": "missing project_name"})
                continue

            exec_row = self._match_execution_row(facts, execution_by_key)
            if exec_row:
                facts.update(self._project_execution_facts(exec_row))
            facts["lifecycle_stage"] = self._infer_lifecycle_stage(facts)
            facts["risk_level"] = self._infer_risk_level(facts)

            evidence = [
                {
                    "field": field,
                    "source_type": "xlsx_summary",
                    "source_ref": f"{file_path}#项目明细表!row={row_index}",
                    "extract_method": "xlsx_row",
                    "confidence": 0.90 if field in {"project_name", "project_code", "bid_status"} else 0.82,
                    "summary": "项目明细表导入",
                }
                for field in facts.keys()
            ]
            if exec_row:
                evidence.extend([
                    {
                        "field": field,
                        "source_type": "xlsx_summary",
                        "source_ref": f"{file_path}#项目执行",
                        "extract_method": "xlsx_row",
                        "confidence": 0.82,
                        "summary": "项目执行表补充",
                    }
                    for field in self._project_execution_facts(exec_row).keys()
                ])

            ledger_result = ledger.apply_patch({
                "project_name": project_name,
                "source_type": "xlsx_summary",
                "facts": facts,
                "evidence": evidence,
                "skill": "data_cleaning_file_organization",
                "actor": "data_cleaning_file_organization",
            })

            structured_path = os.path.join(structured_dir, f"{self._safe_name(project_name)}.json")
            self._save_structured_json(structured_path, {
                "schema_version": "project_detail.row.v1",
                "source_file": file_path,
                "source_row": row_index,
                "facts": facts,
                "raw_row": row,
                "execution_row": exec_row or {},
                "artifacts": {
                    "project_overview_md": ledger_result["markdown_path"],
                    "project_ledger_json": ledger_result["state_path"],
                },
                "processed_at": datetime.now().isoformat(),
            })

            projects.append({
                "project_name": project_name,
                "facts": facts,
                "structured_output": structured_path,
                "artifacts": {
                    "project_overview_md": ledger_result["markdown_path"],
                    "project_ledger_json": ledger_result["state_path"],
                    "project_dir": ledger_result["project_dir"],
                },
            })

        return {
            "schema_version": "project_detail_workbook.import.v1",
            "status": "success" if projects and not failed else ("partial" if projects else "failed"),
            "source_file": file_path,
            "processed_projects": len(projects),
            "failed": len(failed),
            "execution_rows": len(execution_rows),
            "projects": projects,
            "failures": failed,
            "structured_dir": structured_dir,
        }

    def generate_bid_progress_html(self, output_file: str = "") -> Dict[str, Any]:
        """Generate 投标进度总览.html from project ledger facts.

        The HTML is a derived view. The authoritative evidence remains each
        project's project_ledger.json and 项目总览.md.
        """
        ledger_dir = self.project_files_dir
        if not os.path.exists(ledger_dir):
            return {"error": f"Project ledger directory not found: {ledger_dir}"}

        projects = self._load_project_ledger_summaries(ledger_dir)
        if not projects:
            return {"error": f"No project ledgers found in: {ledger_dir}"}

        groups = {
            "已中标": [p for p in projects if p["overview_status"] == "已中标"],
            "已丢标": [p for p in projects if p["overview_status"] == "已丢标"],
            "已弃标": [p for p in projects if p["overview_status"] == "已弃标"],
            "参与中": [p for p in projects if p["overview_status"] == "参与中"],
        }
        output = output_file or os.path.join(self.business_root, "投标进度总览.html")
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            f.write(self._render_bid_progress_html(projects, groups))

        return {
            "schema_version": "bid_progress_html.v1",
            "status": "success",
            "source_dir": ledger_dir,
            "output_file": output,
            "total_projects": len(projects),
            "counts": {name: len(items) for name, items in groups.items()},
        }

    def _worksheet_records(self, ws) -> List[Dict[str, Any]]:
        headers = [self._cell_text(cell.value) for cell in next(ws.iter_rows(min_row=1, max_row=1))]
        records = []
        for row in ws.iter_rows(min_row=2):
            record = {}
            has_value = False
            for idx, cell in enumerate(row):
                if idx >= len(headers) or not headers[idx]:
                    continue
                value = self._normalize_cell_value(cell.value)
                if value not in (None, ""):
                    has_value = True
                record[headers[idx]] = value
            if has_value:
                records.append(record)
        return records

    def _project_detail_facts(self, row: Dict[str, Any]) -> Dict[str, Any]:
        mapping = {
            "项目名称": "project_name",
            "项目编号": "project_code",
            "招标人/客户": "customer_name",
            "负责销售": "sales_owner",
            "报名截止": "registration_deadline",
            "开标时间": "bid_open_time",
            "投标保证金": "bid_bond_amount",
            "保证金已支付": "bid_bond_paid",
            "项目类型": "project_type",
            "报名状态": "registration_status",
            "中标状态": "bid_status",
            "签约状态": "contract_status",
            "备注": "note",
            "立项金额": "project_amount",
            "招标编号": "bid_code",
        }
        facts = {}
        for source_field, target_field in mapping.items():
            value = row.get(source_field)
            if value in (None, ""):
                continue
            facts[target_field] = value
        return facts

    def _project_execution_facts(self, row: Dict[str, Any]) -> Dict[str, Any]:
        mapping = {
            "客户": "customer_name",
            "合同金额": "contract_amount",
            "合同编号": "contract_code",
            "签订日期": "contract_signed_date",
            "签约状态": "contract_status",
            "里程碑节点": "milestone_name",
            "预计完成": "planned_finish_date",
            "实际完成": "actual_finish_date",
            "备注": "execution_note",
        }
        facts = {}
        for source_field, target_field in mapping.items():
            value = row.get(source_field)
            if value in (None, ""):
                continue
            facts[target_field] = value
        return facts

    def _execution_rows_by_project(self, rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            for key in [row.get("项目编号"), row.get("项目名称")]:
                if key:
                    grouped.setdefault(str(key), []).append(row)
        return grouped

    def _match_execution_row(self, facts: Dict[str, Any], grouped: Dict[str, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        for key in [facts.get("project_code"), facts.get("project_name")]:
            if key and str(key) in grouped:
                return grouped[str(key)][0]
        return None

    def _infer_lifecycle_stage(self, facts: Dict[str, Any]) -> str:
        bid_status = str(facts.get("bid_status", ""))
        contract_status = str(facts.get("contract_status", ""))
        registration_status = str(facts.get("registration_status", ""))
        if "弃标" in bid_status or "丢标" in bid_status or "未中标" in bid_status:
            return "closed"
        if "已签" in contract_status or facts.get("contract_code"):
            return "execution"
        if "已中标" in bid_status:
            return "won_pending_contract"
        if "已报名" in registration_status or "待开标" in bid_status:
            return "bidding"
        if "待报名" in registration_status:
            return "lead_or_pending_registration"
        return "unknown"

    def _infer_risk_level(self, facts: Dict[str, Any]) -> str:
        if facts.get("lifecycle_stage") in {"closed", "closed_lost"}:
            return "low"
        if facts.get("bid_bond_amount") and str(facts.get("bid_bond_paid", "")) not in {"是", "已支付", "✅ 已支付"}:
            return "medium"
        if facts.get("bid_status") == "已中标" and "未签" in str(facts.get("contract_status", "")):
            return "medium"
        if not facts.get("customer_name") or not facts.get("sales_owner"):
            return "unknown"
        return "low"

    def _load_project_ledger_summaries(self, ledger_dir: str) -> List[Dict[str, Any]]:
        projects = []
        seen = set()
        root = Path(ledger_dir)
        state_paths = sorted(root.rglob("project_ledger.json")) if root.exists() else []
        for state_path_obj in state_paths:
            state_path = str(state_path_obj)
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            facts = state.get("current_facts") or {}
            judgement = state.get("business_judgement") or {}
            project_name = str(facts.get("project_name") or state.get("project_name") or state_path_obj.parent.parent.name)
            if project_name in seen:
                continue
            seen.add(project_name)
            row = {
                "project_name": project_name,
                "project_code": facts.get("project_code", ""),
                "bpm_contract_code": facts.get("bpm_contract_code", ""),
                "business_type": facts.get("business_type", ""),
                "customer_name": facts.get("customer_name", ""),
                "sales_owner": facts.get("sales_owner", ""),
                "registration_deadline": facts.get("registration_deadline", ""),
                "bid_open_time": facts.get("bid_open_time", ""),
                "bid_bond_amount": facts.get("bid_bond_amount", ""),
                "bid_bond_paid": facts.get("bid_bond_paid", ""),
                "project_type": facts.get("project_type", ""),
                "registration_status": facts.get("registration_status", ""),
                "bid_status": facts.get("bid_status", ""),
                "contract_status": facts.get("contract_status", ""),
                "note": facts.get("note") or facts.get("execution_note", ""),
                "project_amount": facts.get("project_amount") or facts.get("contract_amount", ""),
                "lifecycle_stage": facts.get("lifecycle_stage", ""),
                "business_stage": judgement.get("business_stage", facts.get("lifecycle_stage", "")),
                "risk_level": judgement.get("risk_level", facts.get("risk_level", "")),
                "risk_reasons": judgement.get("risk_reasons", []),
                "next_actions": judgement.get("next_actions", []),
                "human_review_required": judgement.get("human_review_required", False),
                "planned_finish_date": facts.get("planned_finish_date", ""),
                "actual_finish_date": facts.get("actual_finish_date", ""),
                "ledger_path": state_path,
            }
            row["overview_status"] = judgement.get("display_status") or self._overview_status(row)
            projects.append(row)
        return sorted(projects, key=lambda p: (p["overview_status"], str(p.get("bid_open_time") or ""), p["project_name"]))

    def _overview_status(self, facts: Dict[str, Any]) -> str:
        bid_status = str(facts.get("bid_status", ""))
        contract_status = str(facts.get("contract_status", ""))
        lifecycle_stage = str(facts.get("lifecycle_stage", ""))
        if "弃标" in bid_status:
            return "已弃标"
        if "丢标" in bid_status or "未中标" in bid_status:
            return "已丢标"
        if lifecycle_stage in {"closed", "closed_lost"}:
            return "已丢标" if facts.get("closed_reason_type") == "lost_to_competitor" else "已弃标"
        if "已中标" in bid_status or "已签" in contract_status or lifecycle_stage in {"execution", "won_pending_contract"}:
            return "已中标"
        return "参与中"

    def _render_bid_progress_html(self, projects: List[Dict[str, Any]], groups: Dict[str, List[Dict[str, Any]]]) -> str:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        won = len(groups.get("已中标", []))
        lost = len(groups.get("已丢标", []))
        win_rate_denominator = won + lost
        win_rate = f"{(won / win_rate_denominator * 100):.1f}%" if win_rate_denominator else "0%"
        review_count = len([p for p in projects if p.get("human_review_required")])
        risk_count = len([p for p in projects if p.get("risk_level") in {"high", "medium"}])
        stat_cards = [
            ("全部项目", len(projects), "slate"),
            ("已中标", len(groups.get("已中标", [])), "green"),
            ("已丢标", len(groups.get("已丢标", [])), "orange"),
            ("已弃标", len(groups.get("已弃标", [])), "red"),
            ("参与中", len(groups.get("参与中", [])), "blue"),
            ("中标率", win_rate, "amber"),
            ("需复核", review_count, "violet"),
        ]
        cards_html = "\n".join(
            (
                f'<div class="stat-card {cls}">'
                f'<div class="label">{html.escape(label)}</div>'
                f'<div class="number">{count}</div>'
                f'</div>'
            )
            for label, count, cls in stat_cards
        )
        tab_styles = {
            "全部项目": ("#334155", "#f1f5f9"),
            "已中标": ("#16a34a", "#dcfce7"),
            "已丢标": ("#ea580c", "#ffedd5"),
            "已弃标": ("#dc2626", "#fee2e2"),
            "参与中": ("#2563eb", "#dbeafe"),
        }
        display_groups = {"全部项目": projects, **groups}
        tabs_html = "\n".join(
            (
                f'<button class="tab-btn" data-tab="{html.escape(name)}" '
                f'style="--active-color: {tab_styles[name][0]}; --active-bg: {tab_styles[name][1]}">'
                f'{html.escape(name)} <span class="tab-count">{len(items)}</span></button>'
            )
            for name, items in display_groups.items()
        )
        sections_html = "\n".join(
            (
                f'<div class="tab-content" data-tab="{html.escape(name)}">'
                f'<div class="section">'
                f'<div class="section-title" style="--section-color: {tab_styles[name][0]}; --section-bg: {tab_styles[name][1]}">'
                f'<h2>{html.escape(name)} <span class="count-badge">{len(items)}</span></h2>'
                f'</div>'
                f'{self._render_bid_progress_table(items)}'
                f'<div class="detail-subsection"><h3>项目详情</h3>{self._render_project_detail_cards(items)}</div>'
                f'</div></div>'
            )
            for name, items in display_groups.items()
        )
        sales_rows = self._render_sales_stats(projects)
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>投标进度总览</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    :root {{ color-scheme: light; --border: #d9e0ea; --muted: #64748b; --ink: #0f172a; --panel: #ffffff; --bg: #f6f8fb; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--ink); line-height: 1.55; padding: 20px; }}
    .container {{ max-width: 1760px; margin: 0 auto; }}
    .hero {{ background: linear-gradient(135deg, #0f172a 0%, #1f2937 56%, #0b7667 100%); color: #fff; border-radius: 8px; padding: 22px 26px; margin-bottom: 16px; box-shadow: 0 14px 30px rgba(15, 23, 42, .14); }}
    .hero-row {{ display: flex; justify-content: space-between; gap: 20px; align-items: flex-end; flex-wrap: wrap; }}
    h1 {{ font-size: 30px; line-height: 1.2; font-weight: 800; margin-bottom: 8px; }}
    .meta {{ color: #dbeafe; font-size: 13px; }}
    .stats-bar {{ display: grid; grid-template-columns: repeat(6, minmax(145px, 1fr)); gap: 12px; margin-bottom: 16px; }}
    .stat-card {{ background: var(--panel); border-radius: 8px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(15, 23, 42, .07); border: 1px solid var(--border); border-top: 4px solid #64748b; min-height: 88px; }}
    .stat-card .label {{ font-size: 12px; color: var(--muted); font-weight: 700; }}
    .stat-card .number {{ font-size: 30px; line-height: 1.1; font-weight: 800; margin-top: 8px; color: #111827; }}
    .stat-card.green {{ border-top-color: #16a34a; }} .stat-card.green .number {{ color: #15803d; }}
    .stat-card.red {{ border-top-color: #dc2626; }} .stat-card.red .number {{ color: #b91c1c; }}
    .stat-card.orange {{ border-top-color: #ea580c; }} .stat-card.orange .number {{ color: #c2410c; }}
    .stat-card.blue {{ border-top-color: #2563eb; }} .stat-card.blue .number {{ color: #1d4ed8; }}
    .stat-card.amber {{ border-top-color: #d97706; }} .stat-card.amber .number {{ color: #b45309; }}
    .stat-card.violet {{ border-top-color: #7c3aed; }} .stat-card.violet .number {{ color: #6d28d9; }}
    .stat-card.slate {{ border-top-color: #334155; }} .stat-card.slate .number {{ color: #0f172a; }}
    .tab-nav {{ position: sticky; top: 0; z-index: 20; display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; background: rgba(255,255,255,.94); padding: 10px; border-radius: 8px; box-shadow: 0 8px 22px rgba(15, 23, 42, .08); border: 1px solid var(--border); backdrop-filter: blur(10px); }}
    .tab-btn {{ min-height: 38px; padding: 8px 14px; border: 1px solid #d7dde6; border-radius: 7px; background: #fff; font-size: 13px; font-weight: 700; cursor: pointer; transition: all .16s; color: #334155; display: flex; align-items: center; gap: 6px; }}
    .tab-btn:hover {{ border-color: #aab4c2; background: #f8fafc; }}
    .tab-btn.active {{ border-color: var(--active-color); background: var(--active-bg); color: var(--active-color); }}
    .tab-count {{ font-size: 12px; padding: 2px 8px; border-radius: 10px; background: #f3f4f6; color: #6b7280; }}
    .tab-btn.active .tab-count {{ background: #fff; color: var(--active-color); }}
    .search-box {{ min-width: 280px; flex: 1; max-width: 460px; min-height: 38px; border: 1px solid #d7dde6; border-radius: 7px; padding: 0 12px; color: #111827; outline: none; background: #fff; }}
    .search-box:focus {{ border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37, 99, 235, .12); }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}
    .section {{ background: var(--panel); border-radius: 8px; padding: 18px; margin-bottom: 22px; box-shadow: 0 1px 3px rgba(15, 23, 42, .08); border: 1px solid var(--border); }}
    .section-title {{ border-left: 4px solid var(--section-color); padding: 2px 0 2px 12px; margin-bottom: 14px; display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }}
    .section-title h2 {{ font-size: 18px; color: #111827; display: flex; align-items: center; gap: 8px; }}
    .count-badge {{ font-size: 12px; padding: 2px 10px; border-radius: 999px; font-weight: 800; background: var(--section-bg); color: var(--section-color); }}
    .table-wrap {{ width: 100%; overflow-x: auto; border: 1px solid #e2e8f0; border-radius: 8px; }}
    table {{ width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13px; min-width: 1460px; background: #fff; }}
    .project-table {{ table-layout: fixed; min-width: 3040px; }}
    .project-table .col-index {{ width: 72px; }}
    .project-table .col-project {{ width: 300px; }}
    .project-table .col-code {{ width: 132px; }}
    .project-table .col-bpm {{ width: 190px; }}
    .project-table .col-business {{ width: 110px; }}
    .project-table .col-customer {{ width: 220px; }}
    .project-table .col-owner {{ width: 96px; }}
    .project-table .col-date {{ width: 118px; }}
    .project-table .col-money {{ width: 120px; }}
    .project-table .col-flag {{ width: 110px; }}
    .project-table .col-type {{ width: 96px; }}
    .project-table .col-status {{ width: 105px; }}
    .project-table .col-action {{ width: 260px; }}
    .project-table .col-review {{ width: 96px; }}
    .project-table .col-note {{ width: 260px; }}
    th {{ background: #eef3f8; padding: 9px 10px; text-align: left; font-weight: 800; color: #475569; border-bottom: 1px solid #cbd5e1; white-space: nowrap; font-size: 12px; }}
    td {{ padding: 9px 10px; border-bottom: 1px solid #edf1f5; vertical-align: top; background: transparent; }}
    tbody tr:hover {{ background: #f1f5f9 !important; }}
    .center {{ text-align: center; }}
    .name-cell {{ font-weight: 750; color: #111827; white-space: normal; overflow-wrap: anywhere; line-height: 1.35; }}
    .note {{ min-width: 220px; max-width: 360px; color: #334155; line-height: 1.4; }}
    .badge {{ display: inline-flex; align-items: center; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap; }}
    .badge-red, .badge-danger {{ background: #fee2e2; color: #dc2626; }}
    .badge-yellow, .badge-warning {{ background: #fef3c7; color: #d97706; }}
    .badge-green, .badge-success {{ background: #dcfce7; color: #16a34a; }}
    .badge-blue, .badge-info {{ background: #dbeafe; color: #2563eb; }}
    .badge-gray, .badge-neutral {{ background: #f3f4f6; color: #6b7280; }}
    .text-red {{ color: #dc2626; font-weight: 700; }} .text-yellow {{ color: #d97706; font-weight: 700; }}
    .muted {{ color: #94a3b8; font-style: italic; }}
    .empty-state {{ padding: 28px; color: var(--muted); border: 1px dashed #cbd5e1; border-radius: 8px; background: #f8fafc; }}
    .detail-subsection {{ margin-top: 18px; padding-top: 18px; border-top: 1px solid #e2e8f0; }}
    .detail-subsection h3 {{ font-size: 16px; margin-bottom: 14px; color: #1a1a1a; }}
    .detail-card {{ background: #fafbfc; border-radius: 8px; padding: 16px; margin-bottom: 12px; border: 1px solid #e2e8f0; }}
    .detail-card.warning {{ border-left: 4px solid #f59e0b; background: #fffbeb; }}
    .detail-card h3 {{ font-size: 15px; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .info-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px 12px; margin-bottom: 12px; }}
    .info-item {{ display: flex; flex-direction: column; }}
    .info-item .label {{ font-size: 12px; color: #667085; margin-bottom: 2px; }}
    .info-item .value {{ font-size: 13px; font-weight: 550; color: #111827; }}
    .subsection {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid #e5e7eb; }}
    .subsection h4 {{ font-size: 13px; color: #667085; margin-bottom: 10px; }}
    .sub-table {{ font-size: 12px; min-width: 720px; }}
    .sub-table th {{ padding: 6px 10px; font-size: 11px; }}
    .sub-table td {{ padding: 6px 10px; }}
    .task-list {{ display: flex; flex-direction: column; gap: 3px; }}
    .task-item {{ font-size: 12px; padding: 3px 0; }}
    .risk-item {{ font-size: 12px; padding: 6px 10px; border-radius: 6px; margin-bottom: 3px; }}
    .risk-high {{ background: #fee2e2; }} .risk-medium {{ background: #fef3c7; }} .risk-low {{ background: #dcfce7; }}
    .weekly-content {{ font-size: 12px; color: #334155; padding: 10px; background: #f8fafc; border-radius: 8px; }}
    .stats-table th {{ background: #7c3aed; color: #fff; font-size: 13px; }}
    .stats-table td {{ padding: 12px; }}
    @media (max-width: 1080px) {{ body {{ padding: 12px; }} .stats-bar {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .hero {{ padding: 18px; }} .tab-nav {{ position: static; }} }}
    @media print {{ body {{ background: #fff; padding: 0; }} .hero {{ color: #111827; background: #fff; box-shadow: none; border: 1px solid #e5e7eb; }} .section, .detail-card {{ box-shadow: none; border: 1px solid #e5e7eb; }} .tab-nav {{ display: none; }} .tab-content {{ display: block !important; }} }}
  </style>
</head>
<body>
  <main class="container">
    <section class="hero">
      <div class="hero-row">
        <div>
          <h1>投标进度总览</h1>
          <div class="meta">生成时间: {html.escape(generated_at)} | 投标项目总数: {len(projects)}</div>
        </div>
      </div>
    </section>
    <div class="stats-bar">{cards_html}</div>
    <div class="tab-nav">{tabs_html}<button class="tab-btn" data-tab="stats" style="--active-color: #7c3aed; --active-bg: #ede9fe">销售统计</button><input class="search-box" id="tableSearch" type="search" placeholder="搜索项目、客户、销售、编号"></div>
    {sections_html}
    <div class="tab-content" data-tab="stats">
      <div class="section">
        <div class="section-title" style="--section-color: #7c3aed; --section-bg: #ede9fe"><h2>销售统计</h2></div>
        <div class="stats-bar" style="margin-bottom: 20px;">{cards_html}</div>
        {sales_rows}
      </div>
    </div>
  </main>
  <script>
    const tabs = document.querySelectorAll('.tab-btn');
    const contents = document.querySelectorAll('.tab-content');
    const search = document.getElementById('tableSearch');
    function activateTab(name) {{
      tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === name));
      contents.forEach(c => c.classList.toggle('active', c.dataset.tab === name));
      if (search) search.value = '';
      filterRows('');
    }}
    tabs.forEach(btn => btn.addEventListener('click', () => activateTab(btn.dataset.tab)));
    function filterRows(term) {{
      const active = document.querySelector('.tab-content.active');
      if (!active) return;
      active.querySelectorAll('tbody tr').forEach(row => {{
        row.style.display = row.innerText.toLowerCase().includes(term.toLowerCase()) ? '' : 'none';
      }});
    }}
    if (search) search.addEventListener('input', event => filterRows(event.target.value));
    activateTab(tabs[0]?.dataset.tab || '已中标');
  </script>
</body>
</html>
"""

    def _render_bid_progress_table(self, rows: List[Dict[str, Any]]) -> str:
        headers = [
            "序号", "项目名称", "项目编号", "BPM销售合同号/非订单编号", "业务类型", "招标人/客户",
            "负责销售", "报名截止", "开标时间", "投标保证金", "保证金已支付", "项目类型",
            "报名状态", "中标状态", "签约状态", "风险等级", "下一步动作", "人工复核", "备注", "立项金额", "状态",
        ]
        if not rows:
            return '<div class="empty-state">暂无项目</div>'
        colgroup = "".join(
            f'<col class="{cls}">'
            for cls in [
                "col-index", "col-project", "col-code", "col-bpm", "col-business", "col-customer",
                "col-owner", "col-date", "col-date", "col-money", "col-flag", "col-type",
                "col-status", "col-status", "col-status", "col-status", "col-action", "col-review",
                "col-note", "col-money", "col-status",
            ]
        )
        header_classes = ["center", "project-heading"] + [""] * (len(headers) - 2)
        body = []
        empty_cell = '<span class="muted">-</span>'
        for idx, row in enumerate(rows, start=1):
            values = [
                ("center", idx, ""),
                ("name-cell", row.get("project_name", ""), ""),
                ("", row.get("project_code", ""), ""),
                ("", row.get("bpm_contract_code", ""), ""),
                ("", row.get("business_type", ""), "neutral"),
                ("", row.get("customer_name", ""), ""),
                ("", row.get("sales_owner", ""), ""),
                ("", row.get("registration_deadline", ""), ""),
                ("", row.get("bid_open_time", ""), ""),
                ("", row.get("bid_bond_amount", ""), ""),
                ("", row.get("bid_bond_paid", ""), "paid"),
                ("", row.get("project_type", ""), "neutral"),
                ("", row.get("registration_status", ""), "registration"),
                ("", row.get("bid_status", ""), "bid"),
                ("", row.get("contract_status", ""), "contract"),
                ("", row.get("risk_level", ""), "risk"),
                ("note", "；".join(row.get("next_actions") or []), ""),
                ("", "是" if row.get("human_review_required") else "否", "review"),
                ("note", row.get("note", ""), ""),
                ("", row.get("project_amount", ""), ""),
                ("", row.get("overview_status", ""), "overview"),
            ]
            cells = "".join(
                f'<td class="{css_class}">{self._format_table_cell(value, badge_type, empty_cell)}</td>'
                for css_class, value, badge_type in values
            )
            body.append(f'<tr style="background-color: {self._row_background(row)}">{cells}</tr>')
        head = "".join(
            f'<th class="{css_class}">{html.escape(header)}</th>' if css_class else f"<th>{html.escape(header)}</th>"
            for header, css_class in zip(headers, header_classes)
        )
        return (
            f'<div class="table-wrap"><table class="project-table"><colgroup>{colgroup}</colgroup>'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
        )

    def _render_project_detail_cards(self, rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return '<div class="detail-card"><span class="muted">暂无项目详情</span></div>'
        return "\n".join(self._render_project_detail_card(row) for row in rows)

    def _render_project_detail_card(self, row: Dict[str, Any]) -> str:
        warning_class = " warning" if row.get("human_review_required") or row.get("risk_level") in {"high", "medium"} else ""
        info_items = [
            ("CRM 编号", row.get("project_code", "")),
            ("BPM销售合同号/非订单编号", row.get("bpm_contract_code", "")),
            ("业务类型", row.get("business_type", "")),
            ("招标人/客户", row.get("customer_name", "")),
            ("负责销售", row.get("sales_owner", "")),
            ("报名截止", row.get("registration_deadline", "")),
            ("开标时间", row.get("bid_open_time", "")),
            ("投标保证金", row.get("bid_bond_amount", "")),
            ("保证金已支付", row.get("bid_bond_paid", "")),
            ("项目类型", row.get("project_type", "")),
            ("报名状态", row.get("registration_status", "")),
            ("中标状态", row.get("bid_status", "")),
            ("签约状态", row.get("contract_status", "")),
            ("风险等级", row.get("risk_level", "")),
            ("人工复核", "是" if row.get("human_review_required") else "否"),
            ("备注", row.get("note", "")),
            ("立项金额", row.get("project_amount", "")),
        ]
        info_html = "".join(
            f'<div class="info-item"><span class="label">{html.escape(label)}</span><span class="value">{self._plain_or_empty(value)}</span></div>'
            for label, value in info_items
        )
        tasks = row.get("next_actions") or ["等待更多证据后更新下一步动作"]
        task_html = "".join(f'<div class="task-item">{"☑" if idx == 0 and row.get("overview_status") == "已中标" else "☐"} {html.escape(str(task))}</div>' for idx, task in enumerate(tasks))
        risk_html = self._risk_items(row)
        latest = row.get("note") or "项目账本已更新，待进一步跟进"
        return (
            f'<div class="detail-card{warning_class}">'
            f'<h3>{html.escape(str(row.get("project_name") or ""))} {self._status_badge(row)}</h3>'
            f'<div class="info-grid detail-grid">{info_html}</div>'
            f'<div class="subsection"><h4>里程碑进度</h4>{self._milestone_table(row)}</div>'
            f'<div class="subsection"><h4>任务跟踪（{len(tasks)}）</h4><div class="task-list">{task_html}</div></div>'
            f'<div class="subsection"><h4>风险与问题</h4>{risk_html}</div>'
            f'<div class="subsection"><h4>最新进展</h4><p class="weekly-content">{html.escape(str(latest))}</p></div>'
            f'</div>'
        )

    def _milestone_table(self, row: Dict[str, Any]) -> str:
        planned = [
            ("报名截止", row.get("registration_deadline", ""), row.get("registration_status", ""), "", "来自项目账本"),
            ("开标", row.get("bid_open_time", ""), row.get("bid_status", ""), "", "来自项目账本"),
            ("签约", "", row.get("contract_status", ""), "", "需 CRM/BPM 或合同文件补充"),
            ("执行跟踪", row.get("planned_finish_date", ""), row.get("business_stage", ""), row.get("actual_finish_date", ""), "业务判断派生"),
        ]
        rows = "".join(
            f"<tr><td>{html.escape(str(name))}</td><td>{self._plain_or_empty(deadline)}</td><td>{self._plain_or_empty(status)}</td><td>{self._plain_or_empty(done)}</td><td>{self._plain_or_empty(note)}</td></tr>"
            for name, deadline, status, done, note in planned
        )
        return '<table class="sub-table"><thead><tr><th>里程碑</th><th>截止日期</th><th>状态</th><th>完成日期</th><th>备注</th></tr></thead><tbody>' + rows + "</tbody></table>"

    def _risk_items(self, row: Dict[str, Any]) -> str:
        risk_level = row.get("risk_level") or "low"
        reasons = row.get("risk_reasons") or []
        if not reasons:
            if risk_level == "low":
                reasons = ["当前无高风险，按下一步动作继续推进"]
            else:
                reasons = ["需结合项目证据进一步复核"]
        css = {"high": "risk-high", "medium": "risk-medium", "low": "risk-low"}.get(str(risk_level), "risk-medium")
        return "".join(f'<div class="risk-item {css}">{html.escape(str(reason))}</div>' for reason in reasons)

    def _status_badge(self, row: Dict[str, Any]) -> str:
        status = str(row.get("overview_status") or "")
        if status == "已中标":
            return '<span class="badge badge-green">已中标</span>'
        if status == "已丢标":
            return '<span class="badge badge-red">已丢标</span>'
        if status == "已弃标":
            return '<span class="badge badge-red">已弃标</span>'
        if row.get("risk_level") == "high":
            return '<span class="badge badge-red">高风险</span>'
        return '<span class="badge badge-blue">参与中</span>'

    def _row_background(self, row: Dict[str, Any]) -> str:
        if row.get("risk_level") == "high":
            return "#fef2f2"
        if row.get("overview_status") == "已中标":
            return "#f0fdf4"
        if row.get("overview_status") in {"已丢标", "已弃标"}:
            return "#fef2f2"
        return "#f8fafc" if row.get("human_review_required") else "#ffffff"

    def _plain_or_empty(self, value: Any) -> str:
        if value in (None, ""):
            return '<span class="muted">—</span>'
        return html.escape(str(value))

    def _format_table_cell(self, value: Any, badge_type: str, empty_cell: str) -> str:
        if value in (None, ""):
            return empty_cell
        text = str(value)
        if badge_type:
            return f'<span class="badge {self._badge_class(text, badge_type)}">{html.escape(text)}</span>'
        return html.escape(text)

    def _badge_class(self, value: str, badge_type: str) -> str:
        if badge_type == "overview":
            if "丢标" in value or "弃标" in value or "未中标" in value:
                return "badge-danger"
            if "中标" in value:
                return "badge-success"
            return "badge-info"
        if badge_type == "paid":
            return "badge-success" if value in {"是", "已支付"} else "badge-warning"
        if badge_type == "risk":
            if value == "high":
                return "badge-danger"
            if value == "medium":
                return "badge-warning"
            if value == "low":
                return "badge-success"
            return "badge-neutral"
        if badge_type == "review":
            return "badge-warning" if value == "是" else "badge-success"
        if badge_type == "bid":
            if "丢标" in value or "弃标" in value or "未中标" in value:
                return "badge-danger"
            if "中标" in value:
                return "badge-success"
            if "待" in value:
                return "badge-warning"
            return "badge-info"
        if badge_type == "contract":
            if "已签" in value:
                return "badge-success"
            if "未签" in value or "待" in value:
                return "badge-warning"
            return "badge-neutral"
        if badge_type == "registration":
            if "已报名" in value:
                return "badge-info"
            if "待" in value:
                return "badge-warning"
            return "badge-neutral"
        return "badge-neutral"

    def _render_sales_stats(self, projects: List[Dict[str, Any]]) -> str:
        stats: Dict[str, Dict[str, int]] = {}
        for row in projects:
            owner = self._sales_owner_display_name(row.get("sales_owner"))
            status = row.get("overview_status", "参与中")
            stats.setdefault(owner, {"total": 0, "已中标": 0, "已丢标": 0, "已弃标": 0, "参与中": 0})
            stats[owner]["total"] += 1
            stats[owner][status if status in stats[owner] else "参与中"] += 1
        rows = []
        for owner, item in sorted(stats.items(), key=lambda pair: (-pair[1]["total"], pair[0])):
            denominator = item["已中标"] + item["已丢标"]
            win_rate = f"{(item['已中标'] / denominator * 100):.1f}%" if denominator else "0%"
            rows.append(
                "<tr>"
                f"<td>{html.escape(owner)}</td><td>{item['total']}</td><td>{item['已中标']}</td>"
                f"<td>{item['已丢标']}</td><td>{item['已弃标']}</td><td>{item['参与中']}</td>"
                f"<td>{win_rate}</td>"
                "</tr>"
            )
        return '<div class="table-wrap"><table><thead><tr><th>负责销售</th><th>项目数</th><th>已中标</th><th>已丢标</th><th>已弃标</th><th>参与中</th><th>中标率</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"

    def _sales_owner_display_name(self, value: Any) -> str:
        return str(value or "未指定").strip() or "未指定"

    def _cell_text(self, value: Any) -> str:
        return "" if value is None else str(value).strip()

    def _normalize_cell_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped in {"待录入", "待确认", "待补充", "（待补充）", "(待补充)", "nan", "NaN"}:
                return None
            return stripped if stripped else None
        return value

    def _first_field(self, items: List[Dict[str, Any]], field: str) -> str:
        for item in items:
            value = (item.get("fields") or {}).get(field)
            if value:
                return str(value)
        return ""

    def _safe_name(self, value: str) -> str:
        safe = re.sub(r'[<>:"/\\|?*\s]+', "_", value).strip("_")
        return safe or "unnamed"

    def _save_structured_json(self, output_path: str, data: Dict[str, Any]) -> None:
        output_dir = os.path.dirname(output_path)
        os.makedirs(output_dir, exist_ok=True)
        encoded = json.dumps(
            data, ensure_ascii=False, indent=2, allow_nan=False,
        ).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=".artifact-", dir=output_dir)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _load_json_file(path: str) -> Dict[str, Any]:
        return strict_json_load(path)

    def classify_document(self, file_path: str) -> Dict:
        """根据文件名/内容分类文档到业务领域"""
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}
        
        filename = os.path.basename(file_path).lower()
        
        # 先尝试文件名匹配
        matched_category = None
        for category, keywords in CLASSIFICATION_KEYWORDS.items():
            for kw in keywords:
                if kw in filename:
                    matched_category = category
                    break
            if matched_category:
                break
        
        # 如果文件名未匹配，尝试内容匹配（仅文本文件）
        if not matched_category:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read().lower()[:5000]
                for category, keywords in CLASSIFICATION_KEYWORDS.items():
                    for kw in keywords:
                        if kw in content:
                            matched_category = category
                            break
                    if matched_category:
                        break
            except:
                pass
        
        if not matched_category:
            matched_category = "未分类"
        
        return {
            "file": file_path,
            "category": matched_category,
            "filename": filename,
        }

    def batch_process(self, source_dir: Optional[str] = None) -> Dict:
        """批量处理目录中的文件：扫描 → 提取 → 分类 → 保存"""
        scan_result = self.scan_raw_files(source_dir)
        files = scan_result.get("files", [])
        
        processed = []
        failed = []
        
        for f in files:
            try:
                # 提取
                extract = self.extract_pdf(f["path"])
                if "error" in extract:
                    failed.append({"file": f["name"], "error": extract["error"]})
                    continue
                
                # 分类
                classification = self.classify_document(f["path"])
                category = classification.get("category", "未分类")
                
                # 保存结构化数据
                output = {
                    "source_file": f["name"],
                    "category": category,
                    "extraction": extract,
                    "processed_at": datetime.now().isoformat(),
                }
                
                # 保存到对应分类目录
                cat_dir = os.path.join(self.cleaned_dir, category)
                os.makedirs(cat_dir, exist_ok=True)
                base_name = os.path.splitext(f["name"])[0]
                out_path = os.path.join(cat_dir, f"{base_name}_extracted.json")
                with open(out_path, "w", encoding="utf-8") as of:
                    json.dump(output, of, ensure_ascii=False, indent=2)
                
                # 保存 Markdown 版本
                md_path = os.path.join(cat_dir, f"{base_name}_extracted.md")
                with open(md_path, "w", encoding="utf-8") as mf:
                    mf.write(f"# {f['name']}\n\n")
                    mf.write(f"**分类**: {category}\n\n")
                    mf.write(f"**提取时间**: {output['processed_at']}\n\n")
                    mf.write(f"**文本长度**: {extract.get('text_length', 0)}\n\n")
                    mf.write("## 提取字段\n\n")
                    for k, v in extract.get("fields", {}).items():
                        mf.write(f"- **{k}**: {v}\n")
                    mf.write(f"\n## 原文摘录\n\n```\n{extract.get('extracted_text', '')}\n```\n")
                
                processed.append({
                    "file": f["name"],
                    "category": category,
                    "output": out_path,
                })
                
                # 移动到已处理
                processed_dir = os.path.join(self.ocr_dir, f["name"])
                shutil.move(f["path"], processed_dir)
                
            except Exception as e:
                failed.append({"file": f["name"], "error": str(e)})
        
        return {
            "scanned": len(files),
            "processed": len(processed),
            "failed": len(failed),
            "items": processed,
            "failures": failed,
        }

    def save_structured(self, data: Dict, output_path: str) -> str:
        """将结构化数据保存到指定路径"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return f"Saved to {output_path}"

    def update_project_ledger(
        self,
        project_name: str = "",
        facts: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        source_type: str = "local_file",
    ) -> Dict[str, Any]:
        """Submit cleaned candidate facts into the shared project ledger.

        This tool does not move, rename, overwrite, or delete source files. It
        records candidate facts, evidence, conflicts, and decisions in the shared
        project ledger for later Excel/HTML/Word/CRM loops.
        """
        facts = facts or {}
        if project_name and "project_name" not in facts:
            facts = {**facts, "project_name": project_name}
        if not project_name:
            project_name = facts.get("project_name", "未命名项目")

        ledger_dir = self.project_files_dir
        ledger = ProjectLedger(base_dir=ledger_dir)
        result = ledger.apply_patch({
            "project_name": project_name,
            "source_type": source_type,
            "facts": facts,
            "evidence": evidence or [],
            "skill": "data_cleaning_file_organization",
            "actor": "data_cleaning_file_organization",
        })

        return {
            "schema_version": "project_ledger.update.v1",
            "status": result["status"],
            "project_name": result["project_name"],
            "decisions": result["decisions"],
            "current_facts": result["current_facts"],
            "conflicts": result["conflicts"],
            "artifacts": {
                "project_overview_md": result["markdown_path"],
                "project_ledger_json": result["state_path"],
                "project_dir": result["project_dir"],
            },
            "next_steps": [
                "review conflicts before overwriting high-risk facts",
                "use project_overview_md as the evidence ledger for downstream Excel/HTML/CRM outputs",
            ],
        }
