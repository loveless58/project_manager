"""
Opportunity Manager Tools — 商机管理工具层

职责：
- 扫描招标公告目录
- 解析招标公告提取关键字段（招标编号、项目名、预算、截止时间、客户）
- 与现有商机列表比对，检测重复
- 生成标准化的 bid_context.json
- 生成 CRM 录入建议

数据流：
  招标公告文件 → 解析 → bid_context.json → CRM 建议

工作目录：
  新机会与线索/
  ├── 招标公告/              ← 原始文件
  ├── bid_contexts/          ← 解析后的结构化数据
  ├── 商机列表.md             ← 人类可读商机总览
  └── 重复检测记录.md        ← 检测历史
"""
import os
import json
import re
from typing import Dict, List, Any, Optional
from datetime import datetime

from common.workspace_config import default_opportunity_dir

# 工作目录在 OpportunityManagerTools 实例化时按实际路由解析。


class OpportunityManagerTools:
    """商机管理工具集合"""

    def __init__(self, opportunity_dir: Optional[str] = None):
        self.opportunity_dir = opportunity_dir or default_opportunity_dir()
        self.bid_notice_dir = os.path.join(self.opportunity_dir, "招标公告")
        self.bid_context_dir = os.path.join(self.opportunity_dir, "bid_contexts")

    def scan_bid_notices(self, source_dir: Optional[str] = None) -> Dict:
        """扫描招标公告目录，返回文件列表"""
        src = source_dir or self.bid_notice_dir
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

    def parse_bid_notice(self, file_path: str) -> Dict:
        """解析招标公告，提取关键字段"""
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}

        ext = os.path.splitext(file_path)[1].lower()
        text = ""
        
        # 读取文本内容
        if ext == ".txt":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        elif ext == ".pdf":
            try:
                import fitz
                doc = fitz.open(file_path)
                for page in doc:
                    text += page.get_text()
                doc.close()
            except ImportError:
                return {"error": "PyMuPDF not installed"}
        elif ext == ".md":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        else:
            return {"error": f"Unsupported file type: {ext}"}

        # 提取关键字段
        result = {
            "file": file_path,
            "text_length": len(text),
            "text_preview": text[:2000] + ("..." if len(text) > 2000 else ""),
        }
        result["fields"] = self._extract_bid_fields(text)
        return result

    def _extract_bid_fields(self, text: str) -> Dict:
        """从招标公告文本中提取关键字段"""
        fields = {}
        
        # 招标编号
        m = re.search(r'[招标编号|项目编号|采购编号|招标编号|项目编号]\s*[:：]?\s*([A-Z0-9\-]{5,})', text)
        if m:
            fields["project_code"] = m.group(1)
        
        # 项目名称
        m = re.search(r'[项目名称|采购名称|标的名称|招标项目名称]\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            fields["project_name"] = m.group(1).strip()
        
        # 预算金额
        m = re.search(r'[预算金额|采购预算|最高限价|预算]\s*[:：]?\s*([\d,\.]+)\s*万元?', text)
        if m:
            fields["budget"] = m.group(1)
        
        # 截止时间
        m = re.search(r'[投标截止|报名截止|截止时间|递交截止时间]\s*[:：]?\s*(\d{4}[年\-/]\d{1,2}[月\-/]\d{1,2})', text)
        if m:
            fields["deadline"] = m.group(1)
        
        # 客户/招标人
        m = re.search(r'[招标人|采购人|甲方|业主|招标单位]\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            fields["customer"] = m.group(1).strip()
        
        # 招标方式
        m = re.search(r'[招标方式|采购方式|招标类型]\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            fields["bid_type"] = m.group(1).strip()
        
        # 项目联系人/电话
        m = re.search(r'[联系人|联系电话|联系方式]\s*[:：]?\s*(.+?)(?:\n|$)', text)
        if m:
            fields["contact"] = m.group(1).strip()
        
        return fields

    def check_duplicate(self, project_code: str) -> Dict:
        """检测招标编号是否已存在"""
        # 扫描已有 bid_contexts
        existing = []
        if os.path.exists(self.bid_context_dir):
            for f in os.listdir(self.bid_context_dir):
                if f.endswith(".json"):
                    fpath = os.path.join(self.bid_context_dir, f)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fp:
                            data = json.load(fp)
                        if data.get("project_code") == project_code:
                            existing.append({
                                "file": f,
                                "path": fpath,
                                "project_name": data.get("project_name", "N/A"),
                                "created": data.get("created_at", "N/A"),
                            })
                    except:
                        pass
        
        return {
            "project_code": project_code,
            "is_duplicate": len(existing) > 0,
            "existing": existing,
            "message": "已存在商机" if existing else "未发现重复商机",
        }

    def generate_bid_context(self, parsed_data: Dict) -> Dict:
        """生成标准化的 bid_context.json"""
        fields = parsed_data.get("fields", {})
        
        bid_context = {
            "project_code": fields.get("project_code", ""),
            "project_name": fields.get("project_name", ""),
            "customer": fields.get("customer", ""),
            "budget": fields.get("budget", ""),
            "deadline": fields.get("deadline", ""),
            "bid_type": fields.get("bid_type", ""),
            "contact": fields.get("contact", ""),
            "source_file": parsed_data.get("file", ""),
            "status": "待录入",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        
        # 保存到 bid_contexts 目录
        if bid_context["project_code"]:
            filename = f"{bid_context['project_code']}.json"
        else:
            # 使用时间戳生成文件名
            filename = f"bid_context_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        output_path = os.path.join(self.bid_context_dir, filename)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(bid_context, f, ensure_ascii=False, indent=2)
        
        bid_context["saved_to"] = output_path
        return bid_context

    def create_crm_suggestion(self, bid_context: Dict) -> Dict:
        """根据 bid_context 生成 CRM 录入建议"""
        suggestion = {
            "opportunity_name": bid_context.get("project_name", ""),
            "customer": bid_context.get("customer", ""),
            "amount": bid_context.get("budget", ""),
            "expected_close_date": bid_context.get("deadline", ""),
            "source": "招标公告",
            "priority": "高" if "智能" in bid_context.get("project_name", "") else "中",
            "next_action": "录入 CRM 并分配销售",
            "contacts": [
                {
                    "role": "招标人",
                    "info": bid_context.get("contact", ""),
                }
            ],
            "project_code": bid_context.get("project_code", ""),
        }
        
        # 生成 CRM 录入模板
        lines = [
            f"# CRM 录入建议：{suggestion['opportunity_name']}",
            "",
            f"**商机名称**: {suggestion['opportunity_name']}",
            f"**客户**: {suggestion['customer']}",
            f"**预计金额**: {suggestion['amount']} 万元",
            f"**预计成交日期**: {suggestion['expected_close_date']}",
            f"**来源**: {suggestion['source']}",
            f"**优先级**: {suggestion['priority']}",
            f"**招标编号**: {suggestion['project_code']}",
            "",
            "## 联系人",
            f"- 招标人: {suggestion['contacts'][0]['info']}",
            "",
            "## 建议操作",
            f"1. {suggestion['next_action']}",
            "2. 创建项目目录",
            "3. 录入项目记录",
            "",
            "---",
            f"*生成时间: {datetime.now().isoformat()}*",
        ]
        
        suggestion["template"] = "\n".join(lines)
        return suggestion
