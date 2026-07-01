# 工具层详细 API

本文件描述 20 个工具的完整 API。分三大领域：

- **ProjectTools**（10 个）：项目管理
- **DataCleaningTools**（5 个）：数据清洗
- **OpportunityManagerTools**（5 个）：商机检测

---

## ProjectTools

### scan_projects(phase=None)

扫描三阶段目录，返回项目列表。

**参数**：
- `phase` (str, optional): 指定阶段，"项目投标" / "项目执行" / "项目归档"

**返回**：
```json
{
  "projects": [
    {
      "name": "项目名称",
      "phase": "项目投标",
      "path": "项目投标/项目名称",
      "has_record": true
    }
  ],
  "count": 21,
  "phase_counts": {
    "项目投标": 21,
    "项目执行": 3,
    "项目归档": 4
  }
}
```

### read_project_record(project_name)

读取指定项目的项目记录.md，解析为结构化数据。

**参数**：
- `project_name` (str, required): 项目名称

**返回**：
```json
{
  "basic_info": {"name": "...", "customer": "...", ...},
  "timeline": {"bid_deadline": "...", ...},
  "status": {"bid_status": "...", ...},
  "milestones": [
    {"name": "...", "deadline": "YYYY-MM-DD", "status": "进行中/已完成", "completed_date": "", "note": ""}
  ],
  "tasks": {"completed": 5, "total": 10},
  "deliverables": {"completed": 2, "total": 5},
  "risks": ["🔴 高风险: ...", "🟡 中风险: ..."],
  "next_milestone": "...",
  "next_deadline": "YYYY-MM-DD"
}
```

### check_milestones(project_name=None, days=7)

检查里程碑状态，识别逾期和即将到期项目。

**参数**：
- `project_name` (str, optional): 指定项目，None 则检查所有
- `days` (int, default=7): 即将到期天数阈值

**返回**：
```json
{
  "overdue": [
    {"project": "...", "milestone": "...", "deadline": "YYYY-MM-DD", "days_overdue": 3}
  ],
  "upcoming": [
    {"project": "...", "milestone": "...", "deadline": "YYYY-MM-DD", "days_remaining": 2}
  ],
  "normal": [
    {"project": "...", "milestone": "...", "deadline": "YYYY-MM-DD", "days_remaining": 15}
  ]
}
```

### check_deliverables(project_name)

扫描项目子目录，与交付物清单对比。

**参数**：
- `project_name` (str, required): 项目名称

**返回**：
```json
{
  "deliverables": [
    {"name": "...", "required": true, "found": true, "path": "投标文件/..."}
  ],
  "missing_count": 2,
  "found_count": 3
}
```

### write_response(content, filename)

将内容写入 state/ 目录。

**参数**：
- `content` (str, required): 内容
- `filename` (str, required): 文件名

**返回**：`"Saved to state/<filename>"`

## ArchiveTools

### archive_files(source_dir=None)

智能归档散落文件。

**参数**：
- `source_dir` (str, default="~/Desktop/工作文件"): 源目录

**返回**：
```json
{
  "scanned": 50,
  "matched": 30,
  "archived": 28,
  "failed": 2,
  "migrations": ["建议迁移: XX项目 -> 项目执行"]
}
```

## ReportTools

### generate_bid_overview(output_file=None)

生成投标进度总览。

**参数**：
- `output_file` (str, optional): 输出路径，默认 `项目文件/投标进度总览.md`

**返回**：`"投标进度总览已生成: <path>"`

### generate_project_overview(phase=None)

生成全局项目状态概览。

**参数**：
- `phase` (str, optional): 指定阶段

**返回**：`"项目总览已生成: <path>"`

### generate_report(project_name, report_type="weekly")

生成单个项目报告。

**参数**：
- `project_name` (str, required): 项目名
- `report_type` (str, default="weekly"): "weekly" / "brief" / "status"

**返回**：`"报告已生成"`

## MigrateTools

### migrate_project(project_name, to_phase=None, update_status=None)

迁移项目或更新状态。

**参数**：
- `project_name` (str, required): 项目名
- `to_phase` (str, optional): 目标阶段
- `update_status` (str, optional): 仅更新状态文字

**返回**：
```json
{
  "action": "migrated|updated|no_change",
  "from_phase": "项目投标",
  "to_phase": "项目执行",
  "message": "迁移成功"
}
```


---

## DataCleaningTools

数据清洗工具，作用于 `数据清洗工作台/` 目录。

### scan_raw_files(source_dir=None)

扫描原始文件目录，列出待处理文件。

**参数**：
- `source_dir` (str, optional): 默认 `数据清洗工作台/00-原始文件（待处理）`

**返回**：
```json
{
  "files": [
    {"path": "...", "size": 1024, "ext": ".pdf", "modified": "2026-06-30"}
  ],
  "count": 5,
  "source_dir": "数据清洗工作台/00-原始文件（待处理）"
}
```

### extract_pdf(file_path)

提取 PDF 结构化数据。

**参数**：
- `file_path` (str, required): PDF 文件绝对路径

**返回**：
```json
{
  "file": "...",
  "pages": 3,
  "text": "...",
  "tables": [...],
  "metadata": {"title": "...", "author": "..."}
}
```

### classify_document(file_path)

根据文件名/内容分类文档（投标/合同/发票/报告）。

**参数**：
- `file_path` (str, required)

**返回**：
```json
{
  "file": "...",
  "category": "投标文件",
  "confidence": 0.92,
  "target_subdir": "投标文件"
}
```

### batch_process(source_dir=None)

批量处理目录下所有文件。

**参数**：
- `source_dir` (str, optional)

**返回**：
```json
{
  "processed": 8,
  "failed": 1,
  "results": [{"file": "...", "status": "ok|failed", "error": "..."}]
}
```

### save_structured(data, output_path)

保存结构化数据到指定路径。

**参数**：
- `data` (object, required)
- `output_path` (str, required)

**返回**：
```json
{"saved": true, "path": "..."}
```

---

## OpportunityManagerTools

商机管理工具，作用于 `新机会与线索/` 目录。

### scan_bid_notices(source_dir=None)

扫描招标公告目录。

**参数**：
- `source_dir` (str, optional): 默认 `新机会与线索/招标公告/`

**返回**：
```json
{
  "notices": [
    {"path": "...", "filename": "...", "size": 1024, "modified": "..."}
  ],
  "count": 12
}
```

### parse_bid_notice(file_path)

解析招标公告，提取关键字段。

**参数**：
- `file_path` (str, required)

**返回**：
```json
{
  "project_name": "...",
  "project_code": "...",
  "buyer": "...",
  "deadline": "YYYY-MM-DD HH:MM",
  "budget": "...",
  "qualification": "...",
  "raw_text": "..."
}
```

### check_duplicate(project_code)

检测商机是否已存在（基于 `商机列表.md` + bid_contexts 目录）。

**参数**：
- `project_code` (str, required)

**返回**：
```json
{
  "duplicate": true,
  "matched_project": "...",
  "matched_path": "...",
  "match_type": "exact|fuzzy"
}
```

### generate_bid_context(parsed_data)

生成标准 `bid_context.json`。

**参数**：
- `parsed_data` (object, required): `parse_bid_notice` 的返回

**返回**：
```json
{
  "saved": true,
  "path": "新机会与线索/bid_contexts/<project_code>.json",
  "context": {...}
}
```

### create_crm_suggestion(bid_context)

基于 bid_context 生成 CRM 录入建议。

**参数**：
- `bid_context` (object, required)

**返回**：
```json
{
  "suggestion": {
    "project_name": "...",
    "customer": "...",
    "amount": "...",
    "sales_owner": "...",
    "deadline": "...",
    "next_action": "..."
  },
  "save_to": "新机会与线索/CRM录入建议.md"
}
```
