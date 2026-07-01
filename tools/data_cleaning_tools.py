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
from typing import Dict, List, Any, Optional
from datetime import datetime

from ledger import ProjectLedger


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

    def __init__(self, workspace_dir: str = WORKSPACE_DIR):
        self.workspace_dir = workspace_dir
        self.raw_dir = os.path.join(workspace_dir, "00-原始文件（待处理）")
        self.ocr_dir = os.path.join(workspace_dir, "01-OCR输出（待清洗）")
        self.cleaned_dir = os.path.join(workspace_dir, "02-已清洗（结构化数据）")

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
        
        # 尝试使用 PyMuPDF 提取文本
        try:
            import fitz
            doc = fitz.open(file_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            
            result = {
                "file": file_path,
                "text_length": len(text),
                "extracted_text": text[:2000] + ("..." if len(text) > 2000 else ""),
                "is_scanned": len(text.strip()) < 100,  # 文本极少则认为是扫描件
            }
            
            # 尝试提取结构化字段
            result["fields"] = self._extract_fields(text)
            return result
            
        except ImportError:
            return {"error": "PyMuPDF not installed. Run: pip install pymupdf"}
        except Exception as e:
            return {"error": str(e)}

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
                    "extract_method": "docx_text_table" if item.get("file_type") == ".docx" else "document_text",
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
