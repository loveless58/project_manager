# Loop Project Lifecycle Manager

基于 Loop Engineering Framework 的项目全生命周期管理 Agent。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行 Agent（演示模式）

```bash
# 查看所有可用工具
python main.py --list-tools

# 运行风险检查
python main.py --goal "今天有什么风险项目"

# 运行项目进度查询
python main.py --goal "中原消金项目进度怎么样"

# 运行归档
python main.py --goal "帮我把桌面文件归档一下"

# 生成投标进度
python main.py --goal "生成本周投标进度"

# 生成项目总览
python main.py --goal "生成项目总览"
```

### 3. 查看结果

```bash
# 查看生成的报告
ls state/
cat state/risk_summary.md

# 查看执行轨迹
ls logs/
cat logs/project_lifecycle_*.json
```

## 项目结构

```
project_manager/
├── common/                         # ReAct Loop 核心框架（已内联）
│   ├── loop_engine.py              # 循环引擎
│   ├── tool_registry.py            # 工具注册系统
│   ├── state_manager.py            # 状态管理
│   └── llm_adapter.py              # LLM 适配层
├── tools/                          # 业务工具层
│   ├── project_tools.py            # 项目管理工具
│   ├── data_cleaning_tools.py      # 数据清洗工具
│   └── opportunity_tools.py        # 商机管理工具
├── planner.py                      # 规划器（LLM / RuleBased）
├── references/
│   ├── architecture.md             # 架构设计
│   ├── bid_files_integration.md    # bid-files 集成规范
│   └── project_tools.md           # 工具层 API 文档
├── skills/
│   └── project_manager.md          # Skill 文件
├── state/                          # 运行状态/输出
├── logs/                           # 执行轨迹
├── main.py                         # 主入口
├── requirements.txt
└── README.md
```

## 核心能力

| 能力 | 工具 | 触发场景 |
|------|------|---------|
| 项目扫描 | `scan_projects` | 查看所有项目 |
| 里程碑检查 | `check_milestones` | 风险预警、到期检查 |
| 项目记录读取 | `read_project_record` | XX项目进度 |
| 交付物核对 | `check_deliverables` | 交付物清单 |
| 智能归档 | `archive_files` | 文件归档、整理 |
| 状态迁移 | `migrate_project` | 项目移动、状态变更 |
| 投标进度 | `generate_bid_overview` | 投标进度总览 |
| 项目总览 | `generate_project_overview` | 全局状态 |
| 报告生成 | `generate_report` | 周报、简报 |

## 与 bid-files 的关系

本 Agent 是 `bid-files` 技能的智能编排层：
- **数据互通**：直接复用 `项目文件/` 目录结构和 `index.json`
- **渐进式**：当前通过脚本调用 bid-files 功能，未来可内联化
- **不替代**：`bid-files` 脚本体系继续保留，Agent 提供自然语言入口

## 在 Kimi Work 中运行

### 方式一：PythonRun（单次执行）

```python
import subprocess
result = subprocess.run(
    ["python", "main.py", "--goal", "今天有什么风险"],
    capture_output=True, text=True, cwd="/Users/zhang/Desktop/工作文件/project_manager"
)
print(result.stdout)
```

### 方式二：Cron 定时调度

```json
{
  "name": "daily-risk-check",
  "trigger": {"kind": "cron", "expr": "0 9 * * 1-5"},
  "execution": {
    "kind": "local_conversation",
    "workspacePath": "/Users/zhang/Desktop/工作文件",
    "prompt": "请运行 project_manager 的风险检查，生成风险预警报告"
  }
}
```

## 依赖

- Python 3.9+
- `requests` 库（用于 API 调用）
- 项目文件目录结构已初始化（项目投标/项目执行/项目归档）

## License

MIT
