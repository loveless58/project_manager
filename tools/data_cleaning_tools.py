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
WORKSPACE_DIR = os.path.expanduser("~/Desktop/工作文件/数据清洗工作台")
RAW_DIR = os.path.join(WORKSPACE_DIR, "00-原始文件（待处理）")
OCR_DIR = os.path.join(WORKSPACE_DIR, "01-OCR输出（待清洗）")
CLEANED_DIR = os.path.join(WORKSPACE_DIR, "02-已清洗（结构化数据）")

# 分类关键词映射
CLASSIFICATION_KEYWORDS = {
    "新机会与线索": ["招标公告", "招标", "询价", "采购公告", "邀标", "标书"],
    "项目投标": ["投标", "标书", "投标文件", "报价", "居间", "合同"],
    "项目执行": ["合同", "验收", "交付", "实施", "变更"],
    "项目归档": ["验收", "归档", "结算", "结项"],
}


class DataCleaningTools:
    """数据清洗工具集合"""

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
            if is_scanned:
                result["ocr"] = {
                    "status": "blocked",
                    "blocked_reason": "ocr_adapter_unavailable",
                }
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

    def _run_ocr(self, file_path: str) -> Dict[str, Any]:
        """Run the configured OCR adapter, or read a sidecar OCR text file when present."""
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

        return normalize_ocr_result({
            "status": "blocked",
            "engine": "unavailable",
            "blocked_reason": "ocr_adapter_unavailable",
            "error": "ocr_adapter_unavailable: no OCR adapter configured and no .ocr.txt sidecar found",
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

        ledger_dir = os.path.join(self.workspace_dir, "project_ledgers")
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

        ledger = ProjectLedger(base_dir=os.path.join(self.workspace_dir, "project_ledgers"))

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

        ledger = ProjectLedger(base_dir=os.path.join(self.workspace_dir, "project_ledgers"))
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

    def execute_archive_plan(self, run_id: str, confirmed: bool = False) -> Dict[str, Any]:
        """Execute a prepared archive plan only after explicit confirmation."""
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        plan_path = os.path.join(run_dir, "planned_archive_actions.json")
        if not os.path.exists(plan_path):
            return {"error": f"Archive plan not found for run_id: {run_id}"}
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        actions = plan.get("actions", [])
        if not confirmed:
            return {
                "schema_version": "archive_plan.execute.v1",
                "status": "needs_confirmation",
                "run_id": run_id,
                "planned": len(actions),
                "message": "Archive plan prepared but not executed. Call with confirmed=True to move files.",
            }

        results = []
        ledger = ProjectLedger(base_dir=os.path.join(self.workspace_dir, "project_ledgers"))
        review_decisions_path = os.path.join(run_dir, "review_decisions.json")
        has_human_review = os.path.exists(review_decisions_path)
        for action in actions:
            source = action.get("source_file", "")
            target = action.get("target_path", "")
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
            if not os.path.exists(source):
                results.append({"status": "failed", "source_file": source, "error": "source_missing"})
                continue
            if os.path.exists(target):
                results.append({"status": "blocked", "source_file": source, "blockers": ["target_exists"]})
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(source, target)
            archive_record = {
                "original_path": source,
                "archived_path": target,
                "document_type": action.get("document_type", ""),
                "archived_at": datetime.now().isoformat(),
                "run_id": run_id,
            }
            ledger_result = ledger.apply_patch({
                "project_name": action.get("project_name") or "未命名项目",
                "source_type": "system_export",
                "facts": {"last_archived_file": target, "archive_status": "archived"},
                "evidence": [{
                    "field": "archive_status",
                    "source_type": "system_export",
                    "source_ref": target,
                    "extract_method": "archive_plan_execute",
                    "confidence": 1.0,
                    "summary": "确认后执行文件归档",
                }],
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
            "artifacts": artifacts,
        }

    def _build_archive_action(
        self,
        run_id: str,
        source_file: str,
        project_name: str,
        extracted: Dict[str, Any],
        ledger_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        document_type = extracted.get("document_type") or (extracted.get("fields") or {}).get("document_type") or "未分类"
        ext = os.path.splitext(source_file)[1]
        proposed_name = f"{self._safe_name(project_name)}_{self._safe_name(document_type)}_{datetime.now().strftime('%Y%m%d')}{ext}"
        target_dir = os.path.join(self.workspace_dir, "project_ledgers", self._safe_name(project_name), "source_files")
        target_path = os.path.join(target_dir, proposed_name)
        judgement = ledger_result.get("business_judgement", {})
        blockers = []
        if not os.path.exists(source_file):
            blockers.append("source_missing")
        if project_name in {"", "未命名项目"}:
            blockers.append("unknown_project")
        if os.path.exists(target_path):
            blockers.append("target_exists")
        if judgement.get("human_review_required"):
            blockers.append("human_review_recommended")
        return {
            "schema_version": "archive_action.v1",
            "run_id": run_id,
            "status": "ready" if not blockers else "needs_review",
            "source_file": source_file,
            "project_name": project_name,
            "document_type": document_type,
            "proposed_name": proposed_name,
            "target_dir": target_dir,
            "target_path": target_path,
            "blockers": blockers,
            "business_judgement": judgement,
        }

    def _build_review_queue(
        self,
        run_id: str,
        failures: List[Dict[str, Any]],
        archive_actions: List[Dict[str, Any]],
        ledger_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        items = []
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
            if action.get("status") != "ready":
                items.append({
                    "type": "archive_action_review",
                    "severity": "medium",
                    "project_name": action.get("project_name"),
                    "source_file": action.get("source_file"),
                    "target_path": action.get("target_path"),
                    "blockers": action.get("blockers", []),
                    "recommended_action": "确认归档计划后再执行 execute_archive_plan",
                })
        return {
            "schema_version": "review_queue.v1",
            "run_id": run_id,
            "status": "needs_review" if items else "clear",
            "items": items,
        }

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

        ledger = ProjectLedger(base_dir=os.path.join(self.workspace_dir, "project_ledgers"))
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
        ledger_dir = os.path.join(self.workspace_dir, "project_ledgers")
        if not os.path.exists(ledger_dir):
            return {"error": f"Project ledger directory not found: {ledger_dir}"}

        projects = self._load_project_ledger_summaries(ledger_dir)
        if not projects:
            return {"error": f"No project ledgers found in: {ledger_dir}"}

        groups = {
            "已中标": [p for p in projects if p["overview_status"] == "已中标"],
            "已弃标": [p for p in projects if p["overview_status"] == "已弃标"],
            "参与中": [p for p in projects if p["overview_status"] == "参与中"],
        }
        output = output_file or os.path.join(self.workspace_dir, "投标进度总览.html")
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
        if "弃标" in bid_status:
            return "closed_lost"
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
        if facts.get("lifecycle_stage") == "closed_lost":
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
        for name in sorted(os.listdir(ledger_dir)):
            state_path = os.path.join(ledger_dir, name, "project_ledger.json")
            if not os.path.exists(state_path):
                continue
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            facts = state.get("current_facts") or {}
            judgement = state.get("business_judgement") or {}
            project_name = str(facts.get("project_name") or state.get("project_name") or name)
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
        if "弃标" in bid_status or lifecycle_stage == "closed_lost":
            return "已弃标"
        if "已中标" in bid_status or "已签" in contract_status or lifecycle_stage in {"execution", "won_pending_contract"}:
            return "已中标"
        return "参与中"

    def _render_bid_progress_html(self, projects: List[Dict[str, Any]], groups: Dict[str, List[Dict[str, Any]]]) -> str:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        win_rate_denominator = len(groups["已中标"]) + len(groups["已弃标"])
        win_rate = f"{(len(groups['已中标']) / win_rate_denominator * 100):.1f}%" if win_rate_denominator else "0%"
        review_count = len([p for p in projects if p.get("human_review_required")])
        risk_count = len([p for p in projects if p.get("risk_level") in {"high", "medium"}])
        stat_cards = [
            ("全部项目", len(projects), "slate"),
            ("已中标", len(groups["已中标"]), "green"),
            ("已弃标", len(groups["已弃标"]), "red"),
            ("参与中", len(groups["参与中"]), "blue"),
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
        if row.get("overview_status") == "已弃标":
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
            if "中标" in value:
                return "badge-success"
            if "弃标" in value:
                return "badge-danger"
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
            if "中标" in value:
                return "badge-success"
            if "弃标" in value:
                return "badge-danger"
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
            owner = str(row.get("sales_owner") or "未指定")
            status = row.get("overview_status", "参与中")
            stats.setdefault(owner, {"total": 0, "已中标": 0, "已弃标": 0, "参与中": 0})
            stats[owner]["total"] += 1
            stats[owner][status] += 1
        rows = []
        for owner, item in sorted(stats.items(), key=lambda pair: (-pair[1]["total"], pair[0])):
            rows.append(
                "<tr>"
                f"<td>{html.escape(owner)}</td><td>{item['total']}</td><td>{item['已中标']}</td>"
                f"<td>{item['已弃标']}</td><td>{item['参与中']}</td>"
                "</tr>"
            )
        return '<div class="table-wrap"><table><thead><tr><th>负责销售</th><th>项目数</th><th>已中标</th><th>已弃标</th><th>参与中</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"

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
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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

        ledger_dir = os.path.join(self.workspace_dir, "project_ledgers")
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
