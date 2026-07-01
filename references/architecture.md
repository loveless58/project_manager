# 架构设计：Project Manager Agent

## 数据流架构

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   用户自然语言   │────▶│   Planner        │────▶│   LoopEngine    │
│   意图输入      │     │ (LLM/RuleBased)  │     │  (ReAct 循环)   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                        │
                                                        ▼
                                                ┌─────────────────┐
                                                │  ToolRegistry   │
                                                │  (20 个工具)    │
                                                └─────────────────┘
                                                        │
                                ┌───────────────────────┼───────────────────────┐
                                ▼                       ▼                       ▼
                        ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
                        │ ProjectTools │        │DataCleaning  │        │OpportunityMgr│
                        │  (项目工具)  │        │  (数据清洗)  │        │  (商机检测)  │
                        └──────────────┘        └──────────────┘        └──────────────┘
                                │                       │                       │
                                ▼                       ▼                       ▼
                        ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
                        │  项目文件/    │        │ 数据清洗工作台/│       │ 新机会与线索/ │
                        │ (三阶段目录)  │        │  (三阶段)    │        │  (四阶段)    │
                        └──────────────┘        └──────────────┘        └──────────────┘
```

## 核心组件职责

### 1. Planner（新增）
- **LLMPlanner**：调用真实 LLM（minimax 兼容 OpenAI 协议），每轮根据 Observation 实时决策
- **RuleBasedPlanner**：基于关键词和已执行工具的 fallback，无需 API key
- 由 `main.run()` 根据 `LLM_API_KEY` 是否存在自动选择（`planner_mode='auto'`）

### 2. LoopEngine（复用）
- ReAct 循环执行
- 终止条件：`max_rounds=15`, `dedup=2`, `token_budget=8000`
- 状态管理：`sliding_window`（保留头部+最近 3 轮）
- 错误恢复：`retry_max=2`
- Trace 记录：每轮保存到 `logs/project_manager_*.json`

### 3. ToolRegistry（复用）
- 20 个工具统一注册（项目管理 10 + 数据清洗 5 + 商机 5）
- 自动参数推断（从 Python 函数签名）
- 工具 schema 生成供 LLM 决策

### 4. 业务工具层（三大领域）

#### ProjectTools（10 个工具）
- `scan_projects()` — 扫描三阶段目录，读取 index.json
- `read_project_record()` — 解析 Markdown 为结构化数据
- `check_milestones()` — 日期计算，风险分级
- `check_deliverables()` — 目录扫描 vs 清单对比
- `archive_files()` — 内容匹配 + 关键词提取 + 目录归档
- `migrate_project()` — 目录移动 + 状态更新
- `generate_bid_overview()` — 生成投标总览
- `generate_project_overview()` — 生成全局总览
- `generate_report()` — 周报/简报/状态报告
- `write_response()` — 文件写入 state/

#### DataCleaningTools（5 个工具）
- `scan_raw_files()` — 扫描待处理文件目录
- `extract_pdf()` — 提取 PDF 结构化数据
- `classify_document()` — 文档分类
- `batch_process()` — 批量处理
- `save_structured()` — 保存结构化结果

#### OpportunityManagerTools（5 个工具）
- `scan_bid_notices()` — 扫描招标公告
- `parse_bid_notice()` — 解析公告关键字段
- `check_duplicate()` — 商机去重
- `generate_bid_context()` — 生成标准 bid_context.json
- `create_crm_suggestion()` — 生成 CRM 录入建议

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
├── archive_log_YYYYMMDD.md       # 归档日志
└── project_progress_<name>.md    # 单个项目进度
```

## 与 bid-files 的关系

```
bid-files (脚本层)           project_manager (编排层)
─────────────────           ──────────────────────────────
project_init.py    ──────▶  (被调用) 新项目录入时触发
archive_daily.py   ──────▶  (被调用) 智能归档时触发
generate_bid_overview.py ──▶ (被调用) 投标总览生成时触发
01e_project_overview.py ──▶ (被调用) 全局总览生成时触发
01a_track_progress.py  ───▶ (被调用) 里程碑检查时触发
01c_generate_report.py ──▶ (被调用) 报告生成时触发
01d_deliverable_manager.py ▶ (被调用) 交付物核对时触发
project_migrate.py  ─────▶  (被调用) 状态迁移时触发
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
        "bid_deadline": str,
        "bid_open_date": str,
    },
    "status": {
        "bid_status": str,
        "win_status": str,
        "contract_status": str,
    },
    "milestones": [
        {"name": str, "deadline": str, "status": str, "completed_date": str, "note": str}
    ],
    "tasks": [str],
    "weekly_reports": [str],
    "deliverables": [str],
    "risks": [str],
    "key_files": [str],
    "todos": [str],
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
      "milestones": {"total": 5, "completed": 0, "pending": 5},
      "deliverables": {"total": 4, "completed": 0, "pending": 4},
      "next_milestone": "招标信息获取",
      "next_deadline": "2026-06-27",
      "risk_level": "low|medium|high",
      "last_updated": "2026-06-25T12:00:00"
    }
  ]
}
```

## 入口与调用约定

- `agent.md` 是触发入口，被 Kimi Work 加载
- `main.run(goal, planner_mode)` 是 Python 调用入口
- `main.py` 不接受命令行参数（`__main__` 块直接 `sys.exit(1)`）
- 工作目录默认 `~/Desktop/工作文件/`，可通过环境变量 `LOOP_PROJECT_BASE_DIR` 覆盖

## 治理契约（governance/）

- `directory_contract.json`：项目目录契约（哪些目录必须存在）
- `project_schema.json`：工具 schema 校验规则
- `validate.py`：可执行的校验脚本

由 `feat/governance` 分支建立。
