# 工具层详细 API

本文件描述 31 个工具的完整 API。分四个受控工具域：

- **ProjectTools**（10 个）：项目管理
- **DataCleaningTools**（10 个）：数据清洗及文件整理
- **OpportunityManagerTools**（5 个）：商机检测
- **CloudCCCrmTools**（6 个）：CloudCC/CRM 只读查重、草稿准备、提交前确认

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

### extract_document(file_path)

提取 Word/PDF/图片文件的文本、表格和候选业务字段。当前 Word 路径使用 `python-docx` 读取段落和表格，不移动源文件。

**参数**：
- `file_path` (str, required): 源文件绝对路径

**返回**：
```json
{
  "schema_version": "document.extract.v1",
  "file": "...",
  "document_type": "采购公告|投标文件|未分类",
  "paragraph_count": 96,
  "table_count": 4,
  "fields": {
    "project_name": "...",
    "customer_name": "...",
    "supplier_name": "..."
  }
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

### process_documents_to_ledger(file_paths, project_name="")

读取多个源文档，保存每个文件的结构化 JSON，并把候选事实写入项目账本。

**参数**：
- `file_paths` (array, required): 源文件绝对路径列表
- `project_name` (str, optional): 手工指定项目名；为空时从文件中推断

**返回**：
```json
{
  "schema_version": "data_cleaning.documents_to_ledger.v1",
  "status": "success|partial|failed",
  "processed": 2,
  "project_name": "...",
  "structured_outputs": ["..."],
  "artifacts": {
    "project_overview_md": "...",
    "project_ledger_json": "..."
  }
}
```

### import_project_detail_workbook(file_path)

读取 `项目明细表.xlsx`，将 `项目明细表` 和 `项目执行` sheet 标准化为项目级字段，并按项目写入多个项目账本。

**参数**：
- `file_path` (str, required): `项目明细表.xlsx` 绝对路径

**返回**：
```json
{
  "schema_version": "project_detail_workbook.import.v1",
  "status": "success|partial|failed",
  "processed_projects": 27,
  "execution_rows": 3,
  "projects": [
    {
      "project_name": "...",
      "facts": {
        "project_code": "C000027902",
        "customer_name": "...",
        "bid_status": "已中标",
        "lifecycle_stage": "execution"
      },
      "artifacts": {
        "project_overview_md": "...",
        "project_ledger_json": "..."
      }
    }
  ]
}
```

### generate_bid_progress_html(output_file="")

从 `project_ledgers/*/project_ledger.json` 汇总当前事实，生成 `投标进度总览.html`。这是展示层派生产物；项目事实仍以每个项目的 `项目总览.md` 和 `project_ledger.json` 为准。

**参数**：
- `output_file` (str, optional): 输出 HTML 路径，默认 `<workspace_dir>/投标进度总览.html`

**返回**：
```json
{
  "schema_version": "bid_progress_html.v1",
  "status": "success",
  "source_dir": ".../project_ledgers",
  "output_file": ".../投标进度总览.html",
  "total_projects": 27,
  "counts": {
    "已中标": 3,
    "已弃标": 4,
    "参与中": 20
  }
}
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

---

## CloudCCCrmTools

CloudCC/CRM 工具域是受控执行边界。默认使用 fake adapter，不访问真实浏览器、不提交 CRM。所有工具返回统一 envelope：

```json
{
  "schema_version": "cloudcc.crm.result.v1",
  "ok": false,
  "status": "success|blocked|needs_confirmation|failed",
  "operation": "...",
  "object_type": "opportunity",
  "data": {},
  "evidence": {},
  "pending_confirmation": null,
  "blocked_reason": null,
  "next_steps": [],
  "secrets_included": false
}
```

### cloudcc_session_probe()

检查 CloudCC 登录态和浏览器适配器可用性。fake adapter 下返回 `blocked`。

**返回重点**：
- `status`: `success|blocked`
- `blocked_reason`: `browser_adapter_unavailable|login_required|permission_denied`
- `evidence.session_state`: 登录态证据

### cloudcc_search_record(object_type, query)

只读查询 CRM 对象记录。允许的对象包括 `opportunity`、`customer`、`contact`、`contract`。

**安全规则**：如果登录态或浏览器不可用，返回 `blocked`，不能返回未查到。

### cloudcc_duplicate_check(project_code="", project_name="", customer="")

基于项目编号、项目名称、客户证据做 CRM 商机查重。

**安全规则**：
- `blocked` 不能解释为 `no_duplicate_found`
- `customer` 必须来自客户/采购人/招标人证据，不能由销售负责人推断

### cloudcc_prepare_opportunity_draft(bid_context)

将 `bid_context` 转换成本地 CRM 商机草稿，不写入 CloudCC。

**返回重点**：
- `data.draft`: 建议字段
- `data.missing_required_fields`: 缺失字段
- `evidence.crm_write_performed`: 必须为 `false`

### cloudcc_fill_draft_gated(draft)

受控填充草稿并停在提交前。当前 fake adapter 不填真实页面，只返回 `needs_confirmation`。

**返回重点**：
- `status`: `needs_confirmation`
- `pending_confirmation.action`: `submit_opportunity`
- `data.crm_write_performed`: `false`

### cloudcc_readback_record(record_id="", record_url="")

提交后回读 CRM 记录并校验字段。没有真实浏览器适配器时返回 `blocked`。
