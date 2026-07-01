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

展示类产物默认从账本派生，例如：

```text
state/投标进度总览.html
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
| `prepare_file_organization_run` | 准备文件整理闭环运行包，输出结构化数据、复核队列、归档计划和运行报告 |
| `apply_human_review` | 应用人工复核修正，写回项目账本并生成规则候选 |
| `execute_archive_plan` | 在显式确认后执行重命名归档，并更新项目总览账本 |
| `import_project_detail_workbook` | 读取项目明细表.xlsx，标准化多项目字段，并更新多个项目总览账本 |
| `generate_bid_progress_html` | 从项目账本汇总生成投标进度总览 HTML 展示页 |
| `update_project_ledger` | 将候选事实、证据和来源类型写入项目账本 |

## 最小循环

```text
扫描文件 / 接收文件路径 / 接收项目明细表
  ↓
prepare_file_organization_run / process_documents_to_ledger / import_project_detail_workbook 提取候选字段
  ↓
记录证据引用
  ↓
update_project_ledger
  ↓
ProjectLedger 规则决议
  ↓
BidProjectRuleEngine 业务判断
  ↓
项目总览.md / project_ledger.json / decision_log.jsonl
  ↓
review_queue.json / planned_archive_actions.json / run_report.md
  ↓
apply_human_review 写入人工修正和规则候选
  ↓
execute_archive_plan(confirmed=True) 重命名并移动源文件
  ↓
generate_bid_progress_html 派生展示页
```

`prepare_file_organization_run` 是面向 agent-loop 的首选入口。它会先输出运行包，不移动源文件；中间即使有文件不可读，也会把已成功处理的结构化数据、项目总览、失败项和复核队列落盘。只有显式调用 `execute_archive_plan` 且 `confirmed=True` 时，才会实际重命名和移动原文件。

## 业务判断输出

项目账本每次更新后都会生成 `business_judgement`：

```json
{
  "business_stage": "bidding | won_pending_contract | execution | closed_lost | pending_registration | unknown",
  "display_status": "参与中 | 已中标 | 已弃标",
  "risk_level": "low | medium | high | unknown",
  "risk_reasons": [],
  "next_actions": [],
  "missing_fields": [],
  "human_review_required": false,
  "crm_required": false,
  "bpm_required": false
}
```

第一版只使用确定性规则：报名/开标截止、保证金支付、中标未签约、关键字段缺失、冲突字段和低置信度字段。LLM、RAG、CRM/BPM 回读属于后续增强来源，不在本规则层里直接调用。

## 写入边界

- 本 skill 不直接覆盖高风险事实。
- 本 skill 不静默移动、重命名、覆盖、删除原始文件。
- 文件归档、汇总表更新和外部系统写入必须在后续工具中显式执行。
- `execute_archive_plan` 默认返回 `needs_confirmation`，不会静默移动原文件。
- HTML、汇总表、看板是派生产物，不作为覆盖项目事实的权威来源。
- 低置信度、冲突字段和高风险字段必须进入冲突队列或人工确认。

## 和其他能力的关系

- CloudCC/CRM skill 可以读取项目账本，生成 CRM 草稿、执行受控浏览器操作并回写系统回读证据。
- BPM skill 可以读取项目账本，补充流程编号、合同状态、审批状态等系统来源事实。
- 文档输出 skill 可以读取项目账本，生成 Word 报告和管理汇报。
- 数据分析和看板生成应从项目账本或由账本派生的结构化文件读取数据。
