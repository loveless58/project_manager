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
from typing import Dict, List, Any, Optional
from datetime import datetime


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
        for d in [self.raw_dir, self.ocr_dir, self.cleaned_dir]:
            os.makedirs(d, exist_ok=True)

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
    
    def _extract_fields(self, text: str) -> Dict:
        """从文本中提取常见字段"""
        import re
        fields = {}
        
        # 招标编号
        m = re.search(r'[招标编号|项目编号|采购编号|招标编号]\s*[:：]?\s*([A-Z0-9\-]{5,})', text)
        if m:
            fields["project_code"] = m.group(1)
        
        # 项目名称
        m = re.search(r'[项目名称|采购名称|标的名称]\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            fields["project_name"] = m.group(1).strip()
        
        # 预算金额
        m = re.search(r'[预算金额|采购预算|最高限价]\s*[:：]?\s*([\d,\.]+)\s*万元?', text)
        if m:
            fields["budget"] = m.group(1)
        
        # 截止时间
        m = re.search(r'[投标截止|报名截止|截止时间]\s*[:：]?\s*(\d{4}[年\-/]\d{1,2}[月\-/]\d{1,2})', text)
        if m:
            fields["deadline"] = m.group(1)
        
        # 客户/招标人
        m = re.search(r'[招标人|采购人|甲方|业主]\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            fields["customer"] = m.group(1).strip()
        
        return fields

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
