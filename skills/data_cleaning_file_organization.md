---
name: data_cleaning_file_organization
version: 0.1.0
description: |
  数据清洗及文件整理 skill。负责从本地文件、表格、文档、PDF、网页导出等资料中提取候选事实，
  通过项目账本规则循环写入项目总览.md，并为后续归档、汇总表、看板、CRM/BPM 自动化提供可信输入。
---

# 数据清洗及文件整理

## 定位

本 skill 是项目资料进入 agent-loop 的入口能力之一。它不拥有项目事实的最终裁决权，而是把文件解析、字段提取、文件分类和整理过程中得到的候选事实提交到项目账本。

项目级事实账本由 `ledger/ProjectLedger` 管理，运行时产物默认写入：

```text
state/project_ledgers/<项目名称>/
├── 项目总览.md
├── project_ledger.json
└── decision_log.jsonl
```

## 当前工具

| 工具 | 职责 |
|---|---|
| `scan_raw_files` | 扫描原始文件目录 |
| `extract_pdf` | 提取 PDF / 图片类文件的候选结构化字段 |
| `extract_document` | 提取 Word / PDF / 图片文件的文本、表格和候选字段 |
| `classify_document` | 根据文件名和内容做文件类型判断 |
| `batch_process` | 批量处理目录文件 |
| `save_structured` | 保存结构化数据 |
| `process_documents_to_ledger` | 读取多个源文件，输出结构化 JSON，并更新项目总览账本 |
| `update_project_ledger` | 将候选事实、证据和来源类型写入项目账本 |

## 最小循环

```text
扫描文件 / 接收文件路径
  ↓
extract_document / process_documents_to_ledger 提取候选字段
  ↓
记录证据引用
  ↓
update_project_ledger
  ↓
ProjectLedger 规则决议
  ↓
项目总览.md / project_ledger.json / decision_log.jsonl
```

## 写入边界

- 本 skill 不直接覆盖高风险事实。
- 本 skill 不静默移动、重命名、覆盖、删除原始文件。
- 文件归档、汇总表更新和外部系统写入必须在后续工具中显式执行。
- 低置信度、冲突字段和高风险字段必须进入冲突队列或人工确认。

## 和其他能力的关系

- CloudCC/CRM skill 可以读取项目账本，生成 CRM 草稿、执行受控浏览器操作并回写系统回读证据。
- BPM skill 可以读取项目账本，补充流程编号、合同状态、审批状态等系统来源事实。
- 文档输出 skill 可以读取项目账本，生成 Word 报告和管理汇报。
- 数据分析和看板生成应从项目账本或由账本派生的结构化文件读取数据。
