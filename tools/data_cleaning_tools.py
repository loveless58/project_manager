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
                "next_actions": judgement.get("next_actions", []),
                "human_review_required": judgement.get("human_review_required", False),
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
        stat_cards = [
            ("总项目", len(projects), "全部账本项目", "neutral"),
            ("已中标", len(groups["已中标"]), "进入签约或执行", "success"),
            ("已弃标", len(groups["已弃标"]), "已关闭机会", "danger"),
            ("参与中", len(groups["参与中"]), "报名、投标、待开标", "info"),
            (
                "保证金待确认",
                sum(1 for p in projects if p.get("bid_bond_amount") and str(p.get("bid_bond_paid")) not in {"是", "已支付"}),
                "有金额但未标记支付",
                "warning",
            ),
        ]
        cards_html = "\n".join(
            (
                f'<article class="metric-card {cls}">'
                f'<div class="metric-label">{html.escape(label)}</div>'
                f'<div class="metric-row"><span class="metric-value">{count}</span><span class="metric-dot"></span></div>'
                f'<div class="metric-hint">{html.escape(hint)}</div>'
                f'</article>'
            )
            for label, count, hint, cls in stat_cards
        )
        tabs_html = "\n".join(
            f'<button class="tab-btn" data-tab="{html.escape(name)}"><span>{html.escape(name)}</span><strong>{len(items)}</strong></button>'
            for name, items in groups.items()
        )
        sections_html = "\n".join(
            (
                f'<section class="tab-content" data-tab="{html.escape(name)}">'
                f'<div class="section-head"><div><h2>{html.escape(name)}</h2><p>{self._section_hint(name)}</p></div><span class="section-count">{len(items)} 项</span></div>'
                f'{self._render_bid_progress_table(items)}'
                f'</section>'
            )
            for name, items in groups.items()
        )
        sales_rows = self._render_sales_stats(projects)
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>投标进度总览</title>
  <style>
    :root {{
      --page: #f6f7f9;
      --surface: #ffffff;
      --surface-soft: #fafbfc;
      --border: #e6e8ec;
      --border-strong: #d4d8df;
      --text: #1d2433;
      --muted: #667085;
      --muted-soft: #98a2b3;
      --blue: #2563eb;
      --green: #16a34a;
      --red: #dc2626;
      --amber: #d97706;
      --violet: #7c3aed;
      --shadow: 0 10px 26px rgba(15, 23, 42, 0.06);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      font-size: 14px;
    }}
    .app-shell {{ max-width: 1720px; margin: 0 auto; padding: 28px; }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: flex-start;
      margin-bottom: 20px;
    }}
    .eyebrow {{ color: var(--blue); font-size: 12px; font-weight: 700; letter-spacing: 0; text-transform: uppercase; margin-bottom: 6px; }}
    h1 {{ margin: 0; font-size: 30px; line-height: 1.18; letter-spacing: 0; }}
    .meta {{ color: var(--muted); margin-top: 8px; }}
    .source-pill {{
      border: 1px solid var(--border);
      background: var(--surface);
      border-radius: 999px;
      padding: 8px 12px;
      color: var(--muted);
      white-space: nowrap;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }}
    .metrics-grid {{ display: grid; grid-template-columns: repeat(5, minmax(160px, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .metric-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
    }}
    .metric-label {{ color: var(--muted); font-weight: 650; }}
    .metric-row {{ display: flex; justify-content: space-between; align-items: center; margin: 10px 0 6px; }}
    .metric-value {{ font-size: 30px; line-height: 1; font-weight: 760; }}
    .metric-dot {{ width: 9px; height: 9px; border-radius: 99px; background: var(--muted-soft); }}
    .metric-hint {{ color: var(--muted-soft); font-size: 12px; }}
    .success .metric-value, .success .metric-dot {{ color: var(--green); background: var(--green); }}
    .danger .metric-value, .danger .metric-dot {{ color: var(--red); background: var(--red); }}
    .info .metric-value, .info .metric-dot {{ color: var(--blue); background: var(--blue); }}
    .warning .metric-value, .warning .metric-dot {{ color: var(--amber); background: var(--amber); }}
    .workspace {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .toolbar {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: center;
      padding: 14px;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(180deg, #ffffff 0%, #fbfcfe 100%);
    }}
    .tab-nav {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .tab-btn {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      height: 34px;
      border: 1px solid var(--border);
      background: var(--surface);
      border-radius: 7px;
      padding: 0 10px;
      cursor: pointer;
      color: #344054;
      font-weight: 650;
    }}
    .tab-btn strong {{
      min-width: 24px;
      padding: 2px 7px;
      border-radius: 999px;
      color: var(--muted);
      background: #f2f4f7;
      font-size: 12px;
      text-align: center;
    }}
    .tab-btn.active {{ border-color: #9db7ff; color: var(--blue); background: #f5f8ff; }}
    .tab-btn.active strong {{ color: var(--blue); background: #e6eeff; }}
    .search-box {{
      min-width: 280px;
      height: 34px;
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 0 12px;
      color: var(--text);
      background: var(--surface);
      outline: none;
    }}
    .search-box:focus {{ border-color: #9db7ff; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.10); }}
    .tab-content {{ display: none; padding: 16px; }}
    .tab-content.active {{ display: block; }}
    .section-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 12px; }}
    h2 {{ margin: 0; font-size: 18px; line-height: 1.3; }}
    .section-head p {{ margin: 4px 0 0; color: var(--muted); font-size: 13px; }}
    .section-count {{ color: var(--muted); background: #f8fafc; border: 1px solid var(--border); border-radius: 999px; padding: 5px 10px; }}
    .table-wrap {{ overflow: auto; border: 1px solid var(--border); border-radius: 8px; max-height: 68vh; }}
    table {{ width: 100%; min-width: 1520px; border-collapse: separate; border-spacing: 0; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--border); padding: 10px 12px; text-align: left; vertical-align: top; background: var(--surface); }}
    th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: var(--surface-soft);
      color: #475467;
      font-weight: 700;
      white-space: nowrap;
      box-shadow: inset 0 -1px 0 var(--border);
    }}
    tr:hover td {{ background: #fbfdff; }}
    td.index {{ width: 56px; text-align: center; color: var(--muted); }}
    td.project-name {{ min-width: 260px; font-weight: 650; color: #111827; }}
    td.note {{ min-width: 220px; color: #475467; }}
    .muted {{ color: var(--muted-soft); }}
    .badge {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }}
    .badge-success {{ color: #067647; background: #ecfdf3; }}
    .badge-danger {{ color: #b42318; background: #fef3f2; }}
    .badge-info {{ color: #175cd3; background: #eff8ff; }}
    .badge-warning {{ color: #b54708; background: #fffaeb; }}
    .badge-neutral {{ color: #475467; background: #f2f4f7; }}
    .sales table {{ min-width: 620px; }}
    .empty-state {{ color: var(--muted); border: 1px dashed var(--border-strong); border-radius: 8px; padding: 22px; background: #fcfcfd; }}
    @media (max-width: 960px) {{
      .app-shell {{ padding: 18px; }}
      .topbar, .toolbar {{ flex-direction: column; align-items: stretch; }}
      .metrics-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .search-box {{ min-width: 0; width: 100%; }}
    }}
  </style>
</head>
<body>
  <main class="app-shell">
    <header class="topbar">
      <div>
        <div class="eyebrow">Project Bid Operations</div>
        <h1>投标进度总览</h1>
        <div class="meta">生成时间：{html.escape(generated_at)}</div>
      </div>
      <div class="source-pill">来源：project_ledgers / project_ledger.json</div>
    </header>
    <section class="metrics-grid">{cards_html}</section>
    <section class="workspace">
      <div class="toolbar">
        <nav class="tab-nav">{tabs_html}<button class="tab-btn" data-tab="销售统计"><span>销售统计</span><strong>{len(set(str(p.get("sales_owner") or "未指定") for p in projects))}</strong></button></nav>
        <input class="search-box" id="tableSearch" type="search" placeholder="搜索项目、客户、销售、编号">
      </div>
      {sections_html}
      <section class="tab-content sales" data-tab="销售统计">
        <div class="section-head"><div><h2>销售统计</h2><p>按负责销售聚合当前项目状态。</p></div></div>
        {sales_rows}
      </section>
    </section>
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

    def _section_hint(self, name: str) -> str:
        hints = {
            "已中标": "需要继续跟踪合同、归档和执行节点。",
            "已弃标": "已关闭机会，保留原因和证据便于复盘。",
            "参与中": "重点关注报名、保证金、开标和状态回填。",
        }
        return hints.get(name, "当前项目列表。")

    def _render_bid_progress_table(self, rows: List[Dict[str, Any]]) -> str:
        headers = [
            "序号", "项目名称", "项目编号", "BPM销售合同号/非订单编号", "业务类型", "招标人/客户",
            "负责销售", "报名截止", "开标时间", "投标保证金", "保证金已支付", "项目类型",
            "报名状态", "中标状态", "签约状态", "风险等级", "下一步动作", "人工复核", "备注", "立项金额", "状态",
        ]
        if not rows:
            return '<div class="empty-state">暂无项目</div>'
        body = []
        empty_cell = '<span class="muted">-</span>'
        for idx, row in enumerate(rows, start=1):
            values = [
                ("index", idx, ""),
                ("project-name", row.get("project_name", ""), ""),
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
            body.append(f"<tr>{cells}</tr>")
        head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
        return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'

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
