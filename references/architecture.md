# 架构设计：Loop Project Lifecycle Manager

## 数据流架构

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   用户自然语言   │────▶│   ReAct Loop     │────▶│   工具执行层    │
│   意图输入      │     │   (LoopEngine)   │     │   (ToolRegistry)│
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │                         │
                               ▼                         ▼
                        ┌──────────────┐        ┌──────────────┐
                        │ StateManager │        │ 项目文件/     │
                        │ (sliding_window)│     │ 目录系统     │
                        └──────────────┘        └──────────────┘
                               │                         │
                               ▼                         ▼
                        ┌──────────────┐        ┌──────────────┐
                        │   MemoryStore │       │  报告输出    │
                        │   (state/)    │       │  (state/项目文件/)│
                        └──────────────┘        └──────────────┘
```

## 核心组件职责

### 1. LoopEngine (复用)
- ReAct 循环执行
- 终止条件：max_rounds=15, dedup=2, token_budget=8000
- 状态管理：sliding_window（保留头部+最近3轮）
- 错误恢复：retry_max=2
- Trace 记录：每轮保存到 logs/

### 2. ToolRegistry (复用)
- 10 个工具注册
- 自动参数推断
- 工具 schema 生成供 LLM 决策

### 3. 工具层（新建）

#### ProjectTools
- `scan_projects()` — 扫描三阶段目录，读取 index.json
- `read_project_record()` — 解析 Markdown 为结构化数据
- `check_milestones()` — 日期计算，风险分级
- `check_deliverables()` — 目录扫描 vs 清单对比
- `write_response()` — 文件写入 state/

#### ArchiveTools
- `archive_files()` — 内容匹配 + 关键词提取 + 目录归档
- 调用 bid-files 的 archive_daily.py 逻辑

#### ReportTools
- `generate_bid_overview()` — 扫描投标阶段，生成 Markdown 报告
- `generate_project_overview()` — 扫描所有阶段，生成全局报告
- `generate_report()` — 单个项目周报/简报/状态报告

#### MigrateTools
- `migrate_project()` — 目录移动 + 状态更新
- 遵循 bid-files 迁移规则（目录即真相）

## 与 bid-files 的关系

```
bid-files (脚本层)           loop-project-lifecycle (编排层)
─────────────────           ─────────────────────────────────
project_init.py    ──────▶  (被调用) 新项目录入时触发
archive_daily.py   ──────▶  (被调用) 智能归档时触发
generate_bid_overview.py ──▶ (被调用) 投标总览生成时触发
01e_project_overview.py ──▶ (被调用) 全局总览生成时触发
01a_track_progress.py  ───▶ (被调用) 里程碑检查时触发
01c_generate_report.py ──▶ (被调用) 报告生成时触发
01d_deliverable_manager.py ▶ (被调用) 交付物核对时触发
project_migrate.py  ─────▶ (被调用) 状态迁移时触发
```

Agent 不替代这些脚本，而是：
1. 通过自然语言理解用户意图
2. 规划多轮执行路径
3. 调用这些脚本完成具体任务
4. 汇总结果，生成统一报告

## 数据格式约定

### 项目记录.md 解析结构

```python
ProjectRecord = {
    "basic_info": {
        "name": str,
        "crm_id": str,
        "customer": str,
        "sales": str,
        "amount": int,
        "type": str,
    },
    "timeline": {
        "bid_deadline": str,  # YYYY-MM-DD HH:MM
        "bid_open_date": str,
    },
    "status": {
        "bid_status": str,    # 已报名/未报名/...
        "win_status": str,    # 已中标/未中标/...
        "contract_status": str,  # 已签约/未签约/...
    },
    "milestones": [
        {"name": str, "deadline": str, "status": str, "completed_date": str, "note": str}
    ],
    "tasks": [str],  # - [ ] / - [x] 列表
    "weekly_reports": [str],  # - YYYY-MM-DD: content
    "deliverables": [str],  # - [ ] / - [x] 列表
    "risks": [str],  # - 🔴/🟡 描述
    "key_files": [str],  # - `路径` — 描述
    "todos": [str],  # - [ ] 列表
}
```

### index.json 结构

```json
{
  "projects": [
    {
      "name": "项目名称",
      "phase": "项目投标|项目执行|项目归档",
      "path": "项目投标/项目名称",
      "crm_id": "C000028096",
      "customer": "客户名",
      "status": "当前状态",
      "milestones": {
        "total": 5,
        "completed": 0,
        "pending": 5
      },
      "deliverables": {
        "total": 4,
        "completed": 0,
        "pending": 4
      },
      "next_milestone": "招标信息获取",
      "next_deadline": "2026-06-27",
      "risk_level": "low|medium|high",
      "last_updated": "2026-06-25T12:00:00"
    }
  ]
}
```

## 状态管理策略

### Sliding Window 裁剪规则

```
消息结构：
[0] system_prompt
[1] user_goal
[2] assistant_round1
[3] user_observation1
[4] assistant_round2
[5] user_observation2
...

当消息数 > 2 + 3*3 = 11 时：
  保留 [0:2] 系统提示 + 初始请求
  保留 [-9:] 最近 3 轮
  丢弃 [2:-9] 中间历史
```

### MemoryStore 存储结构

```
state/
├── risk_summary_YYYYMMDD.md      # 风险报告
├── project_overview_YYYYMMDD.md  # 项目总览
├── bid_overview_YYYYMMDD.md      # 投标进度
├── archive_log_YYYYMMDD.md        # 归档日志
├── trace_YYYYMMDD.json           # 执行轨迹
└── project_progress_<name>.md    # 单个项目进度
```
