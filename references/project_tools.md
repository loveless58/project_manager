# 工具层详细 API

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
