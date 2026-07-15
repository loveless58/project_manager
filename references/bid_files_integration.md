# bid-files 集成规范

本 Agent 与 bid-files 技能的集成方式——数据互通、格式兼容、渐进式替代。

> 本 Agent 项目名为 `project_manager`（取代历史名 `loop-project-lifecycle`），但与 bid-files 的数据契约保持不变。

## 数据格式兼容

### 项目记录.md 区块解析

Agent 解析以下区块，与 bid-files 完全一致：

| 区块 | 解析方式 | 数据结构 |
|------|---------|---------|
| `## 基本信息` | 正则匹配 `**字段名**: 值` | dict |
| `## 时间节点` | 同上 | dict |
| `## 项目状态` | 同上 | dict |
| `## 里程碑管理` | Markdown 表格解析 | list[dict] |
| `## 任务跟踪` | 解析 `- [ ]` / `- [x]` | list[str] |
| `## 周报记录` | 解析 `- YYYY-MM-DD: 内容` | list[str] |
| `## 交付物清单` | 解析 `- [ ]` / `- [x]` | list[str] |
| `## 风险与问题` | 解析 `- 🔴/🟡` | list[str] |
| `## 关键文件` | 解析 `` `路径` — 描述 `` | list[str] |
| `## 待办事项` | 解析 `- [ ]` | list[str] |

### index.json 兼容

Agent 读取 `项目文件/index.json` 作为项目索引，格式与 bid-files 完全一致。
生成时刷新 index.json，不修改数据结构。

### 目录结构兼容

```
项目文件/
├── 项目投标/
│   └── <项目名>/
│       ├── 项目记录.md
│       ├── 招标文件/
│       ├── 报名材料/
│       ├── 投标文件/
│       ├── 合同文件/      # (可选)
│       ├── 变更记录/      # (可选)
│       ├── 中标文件/      # (可选)
│       └── 验收文档/      # (可选)
├── 项目弃标/
│   └── ...
├── 项目丢标/
│   └── ...
└── 项目执行/
    └── ...
```

## 与 bid-files 脚本的关系

| bid-files 脚本 | Agent 调用方式 | 说明 |
|---------------|--------------|------|
| `project_init.py` | 直接调用 | 新项目录入时触发 |
| `archive_daily.py` | 直接调用 | 智能归档时触发 |
| `project_migrate.py` | 直接调用 | 状态迁移时触发 |
| `generate_bid_overview.py` | 直接调用 | 投标总览生成时触发 |
| `01e_project_overview.py` | 直接调用 | 全局总览生成时触发 |
| `01a_track_progress.py` | 直接调用 | 里程碑检查时触发 |
| `01c_generate_report.py` | 直接调用 | 报告生成时触发 |
| `01d_deliverable_manager.py` | 直接调用 | 交付物核对时触发 |
| `01_process_core.py` | 不调用 | 由 Agent 自身编排替代 |

## 调用约定

### 脚本调用路径

Agent 运行时通过 `subprocess` 调用 bid-files 脚本：

```python
import subprocess
import os

BID_FILES_DIR = os.path.expanduser("~/Desktop/工作文件/项目文件/.oa-manager/scripts")

def run_bid_script(script_name, *args):
    script_path = os.path.join(BID_FILES_DIR, script_name)
    result = subprocess.run(
        ["python3", script_path] + list(args),
        capture_output=True, text=True, cwd=os.path.expanduser("~/Desktop/工作文件")
    )
    return result.stdout, result.stderr, result.returncode
```

### 返回码处理

| 返回码 | 含义 | Agent 行为 |
|--------|------|-----------|
| 0 | 成功 | 继续下一步 |
| 1 | 输入错误 | 提示用户补充信息 |
| 2 | Excel/文件处理失败 | 记录错误，尝试 Markdown 替代 |
| 3 | 项目未找到 | 提示用户检查项目名 |
| 其他 | 未知错误 | 记录 trace，返回错误摘要 |

## 渐进式替代策略

### 第一阶段（当前）：编排层
- Agent 通过脚本调用 bid-files 功能
- 自然语言意图 → 脚本调用 → 结果汇总
- bid-files 保持独立运行

### 第二阶段：内联化
- 将频繁调用的脚本逻辑内联到 Agent 工具层
- 减少 subprocess 开销
- 直接读写文件系统

### 第三阶段：增强层
- 在 bid-files 基础上增加 Agent 特有功能：
  - 智能意图识别
  - 多轮推理
  - 跨项目关联分析
  - 预测性风险提醒
- bid-files 作为底层基础设施保留

## 目录定位

Agent 默认工作目录：
```python
PROJECT_BASE_DIR = os.path.expanduser("~/Desktop/工作文件/项目文件")
INDEX_PATH = os.path.join(PROJECT_BASE_DIR, "index.json")
```

通过环境变量可覆盖：
```bash
export LOOP_PROJECT_BASE_DIR="/path/to/项目文件"
```
