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
import re
import html
from typing import Callable, Dict, List, Any, Optional
from datetime import datetime

from ledger import ProjectLedger
from ocr import normalize_ocr_result


# 工作目录
WORKSPACE_DIR = os.path.expanduser("~/Desktop/工作文件/project_manager")
RAW_DIR = os.path.join(WORKSPACE_DIR, "00-原始文件（待处理）")
OCR_DIR = os.path.join(WORKSPACE_DIR, "01-OCR输出（待清洗）")
CLEANED_DIR = os.path.join(WORKSPACE_DIR, "02-已清洗（结构化数据）")

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
        import time
        try:
            import easyocr
        except ImportError:
            return {"status": "failed", "engine": "easyocr", "error": "easyocr not installed"}
        
        try:
            start = time.time()
            reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
            
            # easyocr 直接接受文件路径
            result = reader.readtext(file_path, detail=0)
            elapsed = time.time() - start
            
            text = "\n".join(result)
            if not text.strip():
                return {"status": "failed", "engine": "easyocr", "error": "easyocr returned empty text"}
            
            return {
                "status": "success",
                "engine": "easyocr",
                "text": text,
                "pages": [{"page": 1, "text": text, "confidence": 0.85}],
                "elapsed_seconds": round(elapsed, 2),
            }
        except Exception as e:
            return {"status": "failed", "engine": "easyocr", "error": str(e)}

    def _default_ocr_adapter(self, file_path: str) -> Dict[str, Any]:
        """默认 OCR adapter：优先 easyocr（已下载模型），其次 Tesseract，最后 Vision。"""
        # 1. 尝试 easyocr（纯 Python，模型已下载，中文支持好）
        easyocr_result = self._ocr_with_easyocr(file_path)
        if easyocr_result.get("status") == "success":
            return easyocr_result
        
        # 2. 尝试 Tesseract（如果已安装）
        tesseract_result = self._ocr_with_tesseract(file_path)
        if tesseract_result.get("status") == "success":
            return tesseract_result
        
        # 3. 尝试 macOS Vision（macOS 原生，但中文支持取决于系统语言包）
        vision_result = self._ocr_with_vision_macos(file_path)
        if vision_result.get("status") == "success":
            # 检查 Vision 结果是否有意义的内容
            text = vision_result.get("text", "")
            if len(text.strip()) < 10:
                return {
                    "status": "failed",
                    "engine": "vision_quality_check",
                    "error": "OCR engine (Vision) returned empty or too short text.",
                }
            chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
            if chinese_chars > 10 or len(text) < 100:
                return vision_result
            return {
                "status": "failed",
                "engine": "vision_quality_check",
                "error": "OCR engine (Vision) returned low-quality Chinese text. "
                         "Consider installing Tesseract for better OCR: brew install tesseract tesseract-lang",
                "vision_text_sample": text[:200],
            }
        
        # 4. 都失败
        return {
            "status": "failed",
            "engine": "unavailable",
            "error": "No OCR engine available. "
                     "Options: (1) pip install easyocr (models auto-download) "
                     "(2) brew install tesseract tesseract-lang",
        }

    def __init__(self, workspace_dir: str = WORKSPACE_DIR, ocr_adapter: Optional[Callable[[str], Dict[str, Any]]] = None):
        self.workspace_dir = workspace_dir
        self.raw_dir = os.path.join(workspace_dir, "00-原始文件（待处理）")
        self.ocr_dir = os.path.join(workspace_dir, "01-OCR输出（待清洗）")
        self.cleaned_dir = os.path.join(workspace_dir, "02-已清洗（结构化数据）")
        self.ocr_adapter = ocr_adapter

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
        if ext not in [".pdf", ".png", ".jpg", ".jpeg"]:
            return {"error": f"Unsupported file type: {ext}"}

        if ext in [".png", ".jpg", ".jpeg"]:
            return self._extract_ocr_document(file_path, file_type=ext)
        
        # 尝试使用 PyMuPDF 提取文本
        try:
            import fitz
            doc = fitz.open(file_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            
            is_scanned = len(text.strip()) < 100
            if is_scanned:
                ocr_result = self._extract_ocr_document(file_path, file_type=ext)
                return ocr_result

            result = {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": file_path,
                "filename": os.path.basename(file_path),
                "file_type": ext,
                "extract_method": "pdf_text",
                "text_length": len(text),
                "extracted_text": text[:2000] + ("..." if len(text) > 2000 else ""),
                "is_scanned": is_scanned,
            }
            
            # 尝试提取结构化字段
            result["fields"] = self._extract_fields(text)
            result["document_type"] = self._classify_text_document(file_path, text)
            return result
            
        except ImportError:
            ocr_result = self._extract_ocr_document(file_path, file_type=ext)
            if ocr_result.get("status") == "success":
                return ocr_result
            ocr_result["error"] = "PyMuPDF not installed and OCR adapter unavailable"
            return ocr_result
        except Exception as e:
            ocr_result = self._extract_ocr_document(file_path, file_type=ext)
            if ocr_result.get("status") == "success":
                return ocr_result
            return {"error": str(e)}

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
        if ext in [".pdf", ".png", ".jpg", ".jpeg"]:
            extracted = self.extract_pdf(file_path)
            if "error" not in extracted:
                extracted["document_type"] = self.classify_document(file_path).get("category", "未分类")
            return extracted

        if ext == ".md":
            # Markdown 文件直接读取文本
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                fields = self._extract_fields(text)
                document_type = self._classify_text_document(file_path, text)
                return {
                    "schema_version": "document.extract.v1",
                    "file": file_path,
                    "filename": os.path.basename(file_path),
                    "file_type": ext,
                    "document_type": document_type,
                    "text_length": len(text),
                    "extracted_text": text[:4000] + ("..." if len(text) > 4000 else ""),
                    "fields": fields,
                }
            except Exception as e:
                return {"error": str(e)}

        if ext != ".docx":
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
            fields = self._extract_fields(text)
            fields.update(self._extract_docx_business_fields(paragraphs, table_rows, file_path))
            document_type = self._classify_text_document(file_path, text)

            return {
                "schema_version": "document.extract.v1",
                "file": file_path,
                "filename": os.path.basename(file_path),
                "file_type": ext,
                "document_type": document_type,
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
            return {
                "schema_version": "document.extract.v1",
                "status": "blocked",
                "blocked_reason": ocr.get("blocked_reason", "ocr_adapter_unavailable"),
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
        fields = self._extract_fields(text)
        document_type = self._classify_text_document(file_path, text)
        needs_human_review = bool(ocr.get("quality", {}).get("needs_human_review"))
        return {
            "schema_version": "document.extract.v1",
            "status": "success",
            "file": file_path,
            "filename": os.path.basename(file_path),
            "file_type": file_type,
            "document_type": document_type,
            "extract_method": "ocr",
            "text_length": len(text),
            "extracted_text": text[:4000] + ("..." if len(text) > 4000 else ""),
            "is_scanned": True,
            "fields": fields,
            "ocr": ocr,
            "needs_human_review": needs_human_review,
        }

    def _ocr_with_vision_macos(self, file_path: str) -> Dict[str, Any]:
        """使用 macOS Vision 框架对 PDF/图片做 OCR。
        
        将 PDF 每页渲染为图片，调用 VNRecognizeTextRequest 提取文本。
        验证基准：约 0.38s/页，目标 ≤ 0.5s/页。
        """
        import time
        start = time.time()
        
        try:
            import fitz
            from Foundation import NSData
            from Vision import VNRecognizeTextRequest, VNImageRequestHandler
        except ImportError as e:
            return {"status": "failed", "engine": "macos_vision", "error": f"macOS Vision 依赖未安装: {e}"}
        
        ext = os.path.splitext(file_path)[1].lower()
        all_text = []
        pages = []
        
        try:
            if ext in [".png", ".jpg", ".jpeg"]:
                # 图片文件：直接读取
                with open(file_path, "rb") as f:
                    image_data_raw = f.read()
                image_data = NSData.dataWithBytes_length_(image_data_raw, len(image_data_raw))
                handler = VNImageRequestHandler.alloc().initWithData_options_(image_data, None)
                request = VNRecognizeTextRequest.alloc().init()
                request.setRecognitionLevel_(1)  # 1 = accurate
                # 设置中文为主要识别语言
                from Foundation import NSArray
                request.setRecognitionLanguages_(
                    NSArray.arrayWithObjects_count_(["zh-Hans"], 1)
                )
                
                success, error = handler.performRequests_error_([request], None)
                if not success and error:
                    return {"status": "failed", "engine": "macos_vision", "error": str(error)}
                
                page_text = []
                for observation in request.results() or []:
                    for candidate in observation.topCandidates_(1):
                        page_text.append(candidate.string())
                
                text = "\n".join(page_text)
                all_text.append(text)
                pages.append({"page": 1, "text": text, "confidence": 0.85})
            else:
                # PDF 文件：逐页渲染后 OCR
                doc = fitz.open(file_path)
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(dpi=200)
                    png_data = pix.tobytes("png")
                    
                    image_data = NSData.dataWithBytes_length_(png_data, len(png_data))
                    handler = VNImageRequestHandler.alloc().initWithData_options_(image_data, None)
                    request = VNRecognizeTextRequest.alloc().init()
                    request.setRecognitionLevel_(1)
                    # 设置中文为主要识别语言
                    from Foundation import NSArray
                    request.setRecognitionLanguages_(
                        NSArray.arrayWithObjects_count_(["zh-Hans"], 1)
                    )
                    
                    success, error = handler.performRequests_error_([request], None)
                    if not success and error:
                        doc.close()
                        return {"status": "failed", "engine": "macos_vision", "error": f"Page {i+1}: {error}"}
                    
                    page_text = []
                    for observation in request.results() or []:
                        for candidate in observation.topCandidates_(1):
                            page_text.append(candidate.string())
                    
                    text = "\n".join(page_text)
                    all_text.append(text)
                    pages.append({"page": i + 1, "text": text, "confidence": 0.85})
                
                doc.close()
            
            elapsed = time.time() - start
            return {
                "status": "success",
                "engine": "macos_vision",
                "text": "\n".join(all_text),
                "pages": pages,
                "elapsed_seconds": round(elapsed, 2),
            }
            
        except Exception as e:
            return {"status": "failed", "engine": "macos_vision", "error": str(e)}

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
            "blocked_reason": "ocr_engine_failed",
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
    
    def _extract_fields(self, text: str) -> Dict:
        """从文本中提取常见字段"""
        fields = {}
        
        # 招标编号
        m = re.search(r'(?:招标编号|项目编号|采购编号)\s*[:：]?\s*([A-Z0-9\-]{5,})', text)
        if m:
            fields["project_code"] = m.group(1)
        
        # 项目名称
        m = re.search(r'(?:项目名称|采购名称|标的名称)\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            value = m.group(1).strip()
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
        m = re.search(r'(?:招标人|采购人|甲方|业主)\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            value = m.group(1).strip()
            if 2 <= len(value) <= 80 and not any(k in value for k in ["技术成果", "培训", "合同标的", "第三方"]):
                fields["customer"] = value
                fields.setdefault("customer_name", value)

        m = re.search(r'(?:中标结果|中标状态|投标结果|投标状态)\s*[:：]?\s*(已中标|未中标|弃标|待开标|已报名|待报名)', text)
        if m:
            value = m.group(1).strip()
            if value in {"未中标", "弃标"}:
                fields["bid_status"] = "弃标"
            else:
                fields["bid_status"] = value

        m = re.search(r'(?:合同状态|签约状态)\s*[:：]?\s*(已签约|已签订|未签约|待签约)', text)
        if m:
            fields["contract_status"] = m.group(1).strip()

        m = re.search(r'(?:报名状态|报名情况)\s*[:：]?\s*(待报名|已报名)', text)
        if m:
            fields["registration_status"] = m.group(1).strip()

        m = re.search(r'(?:销售负责人|客户经理|负责人)\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            value = m.group(1).strip()
            if 1 <= len(value) <= 40:
                fields["sales_owner"] = value
        
        return fields

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
            if line.endswith("有限公司") and "单位名称" not in line and "北京华胜天成" not in line:
                fields.setdefault("customer_name", line.strip())
                break

        if "采购公告" in joined or "响应须知" in joined:
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

    def _classify_text_document(self, file_path: str, text: str) -> str:
        filename = os.path.basename(file_path)
        haystack = f"{filename}\n{text[:5000]}"
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
            extracted = self.extract_document(path)
            if "error" in extracted:
                failures.append({"file": path, "error": extracted["error"]})
                continue
            extracted_items.append(extracted)

        inferred_project_name = project_name or self._first_field(extracted_items, "project_name") or "未命名项目"
        structured_dir = os.path.join(self.workspace_dir, "structured_documents", self._safe_name(inferred_project_name))
        os.makedirs(structured_dir, exist_ok=True)

        ledger_dir = os.path.join(self.workspace_dir, "项目文件")
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
            evidence = [
                {
                    "field": field,
                    "source_type": source_type,
                    "source_ref": item["file"],
                    "extract_method": item.get("extract_method") or ("docx_text_table" if item.get("file_type") == ".docx" else "document_text"),
                    "confidence": 0.86 if field in {"project_name", "document_type"} else 0.78,
                    "summary": f"{item.get('filename', '')} extracted as {item.get('document_type', '')}",
                }
                for field in facts.keys()
            ]

            ledger_result = ledger.apply_patch({
                "project_name": inferred_project_name,
                "source_type": source_type,
                "facts": facts,
                "evidence": evidence,
                "skill": "data_cleaning_file_organization",
                "actor": "data_cleaning_file_organization",
            })

            output_path = os.path.join(structured_dir, f"{self._safe_name(item['filename'])}_extracted.json")
            self._save_structured_json(output_path, {
                "schema_version": "document.structured_extraction.v1",
                "project_name": inferred_project_name,
                "source_type": source_type,
                "extraction": item,
                "ledger_artifacts": {
                    "project_overview_md": ledger_result["markdown_path"],
                    "project_ledger_json": ledger_result["state_path"],
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
                "project_overview_md": ledger_result["markdown_path"] if ledger_result else "",
                "project_ledger_json": ledger_result["state_path"] if ledger_result else "",
                "project_dir": ledger_result["project_dir"] if ledger_result else "",
            },
        }

    def prepare_file_organization_run(self, file_paths: List[str], project_name: str = "") -> Dict[str, Any]:
        """Prepare a full file-organization run package without moving source files."""
        if isinstance(file_paths, str):
            file_paths = [file_paths]

        run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        extracted_dir = os.path.join(run_dir, "extracted")
        patches_dir = os.path.join(run_dir, "project_patches")
        os.makedirs(extracted_dir, exist_ok=True)
        os.makedirs(patches_dir, exist_ok=True)

        input_manifest = {
            "schema_version": "file_organization.input_manifest.v1",
            "run_id": run_id,
            "created_at": datetime.now().isoformat(),
            "files": [
                {
                    "path": path,
                    "name": os.path.basename(path),
                    "exists": os.path.exists(path),
                    "size": os.path.getsize(path) if os.path.exists(path) else None,
                }
                for path in file_paths
            ],
        }

        extracted_items: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        structured_outputs: List[str] = []
        ledger_results: List[Dict[str, Any]] = []
        archive_actions: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []

        ledger = ProjectLedger(base_dir=os.path.join(self.workspace_dir, "项目文件"))

        for path in file_paths:
            extracted = self.extract_document(path)
            if "error" in extracted:
                failure = {"file": path, "stage": "extract_document", "error": extracted["error"]}
                failures.append(failure)
                trace.append({"stage": "extract_document", "file": path, "status": "failed", "error": extracted["error"]})
                continue

            extracted_items.append(extracted)
            fields = extracted.get("fields") or {}
            inferred_project_name = project_name or fields.get("project_name") or "未命名项目"
            facts = {key: value for key, value in fields.items() if key not in {"document_type", "source_filename"}}
            facts.setdefault("project_name", inferred_project_name)
            source_type = self._source_type_for_document(extracted.get("document_type", ""), extracted.get("filename", ""))
            evidence = [
                {
                    "field": field,
                    "source_type": source_type,
                    "source_ref": path,
                    "extract_method": extracted.get("extract_method") or ("docx_text_table" if extracted.get("file_type") == ".docx" else "document_text"),
                    "confidence": 0.86 if field in {"project_name", "document_type"} else 0.78,
                    "summary": f"{extracted.get('filename', '')} extracted as {extracted.get('document_type', '')}",
                }
                for field in facts.keys()
            ]

            ledger_result = ledger.apply_patch({
                "project_name": inferred_project_name,
                "source_type": source_type,
                "facts": facts,
                "evidence": evidence,
                "skill": "data_cleaning_file_organization",
                "actor": "data_cleaning_file_organization",
            })
            ledger_results.append(ledger_result)

            extracted_path = os.path.join(extracted_dir, f"{self._safe_name(os.path.basename(path))}_extracted.json")
            self._save_structured_json(extracted_path, {
                "schema_version": "file_organization.extracted_document.v1",
                "run_id": run_id,
                "project_name": inferred_project_name,
                "source_file": path,
                "source_type": source_type,
                "extraction": extracted,
                "business_judgement": ledger_result.get("business_judgement", {}),
                "ledger_artifacts": {
                    "project_overview_md": ledger_result["markdown_path"],
                    "project_ledger_json": ledger_result["state_path"],
                },
                "processed_at": datetime.now().isoformat(),
            })
            structured_outputs.append(extracted_path)

            patch_path = os.path.join(patches_dir, f"{self._safe_name(inferred_project_name)}_{self._safe_name(os.path.basename(path))}.json")
            self._save_structured_json(patch_path, {
                "schema_version": "project_patch.v1",
                "project_name": inferred_project_name,
                "source_type": source_type,
                "facts": facts,
                "evidence": evidence,
            })

            action = self._build_archive_action(run_id, path, inferred_project_name, extracted, ledger_result)
            archive_actions.append(action)
            trace.append({"stage": "process_file", "file": path, "status": "success", "project_name": inferred_project_name})

        review_queue = self._build_review_queue(run_id, failures, archive_actions, ledger_results)

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

    def apply_human_review(self, review_decisions: List[Dict[str, Any]], run_id: str = "") -> Dict[str, Any]:
        """Apply human review decisions as highest-weight ledger corrections."""
        if isinstance(review_decisions, dict):
            review_decisions = [review_decisions]

        ledger = ProjectLedger(base_dir=os.path.join(self.workspace_dir, "项目文件"))
        results = []
        rule_candidates = []
        for decision in review_decisions:
            project_name = decision.get("project_name") or (decision.get("facts") or {}).get("project_name")
            facts = decision.get("facts") or {}
            if not project_name:
                results.append({"status": "failed", "error": "missing project_name", "decision": decision})
                continue
            facts.setdefault("project_name", project_name)
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
