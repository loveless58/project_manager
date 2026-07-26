---
name: data_cleaning_file_organization
version: 0.2.0
status: draft   # 无实现,仅 SKILL.md,2026-07-23 标记
description: |
  数据清洗及文件整理 skill。负责把本地资料转换为候选事实、证据引用、复核队列和归档计划。
---

# 数据清洗及文件整理

## 定位

本 skill 是项目资料进入 agent-loop 的入口能力。它读取 Word、PDF、图片、表格和网页导出等本地材料，产出候选事实和证据，再交给项目账本与业务规则层裁决。

它不拥有项目事实的最终裁决权，不直接替代 CloudCC、BPM 或人工复核。运行时工具事实来源是 `ToolRegistry`、`main.SKILL_TOOL_MAP` 和 `governance/project_schema.json`；本文只描述边界，不维护第二份完整工具表。

## 输入输出

输入可以是文件路径、目录、项目明细表或自然语言整理目标。输出应落在隔离工作区或默认 `state/` 下，包括结构化提取结果、项目账本、复核队列、归档计划和运行报告。

项目级事实账本由 `ledger/ProjectLedger` 管理，典型产物是：

```text
state/project_ledgers/<project>/
  项目总览.md
  project_ledger.json
  decision_log.jsonl
```

## 硬规则

- 不静默移动、重命名、覆盖或删除源文件。
- 文件归档必须先生成归档计划；真实移动必须经过显式确认。
- 低置信度、冲突字段和高风险字段必须进入复核或冲突处理。
- OCR 不可用时返回 `blocked`，不能伪造扫描件抽取成功。
- 语义结构化只能输出 JSON 候选事实；未经 evidence_refs、置信度和字段质量门控的字段不能进入账本或归档判断。
- HTML、汇总表和看板是派生产物，不是事实账本的权威来源。
- LLM 摘要不能直接覆盖账本事实；事实必须带来源和证据。

## 软规则

优先走“读取证据 -> 写入候选事实 -> 生成复核队列 -> 准备归档计划”的闭环。需要人工确认或外部系统写入时停在 `needs_confirmation`，由调用方继续调度。

## 派生说明

完整工具列表和参数由 `ToolRegistry` 生成，治理校验通过 `python -X utf8 -B governance\validate.py tools` 覆盖。README 可以摘要能力，但不得重新定义本 skill 的工具契约。

## Safe business-file judgement CLI

For an explicit small business-file review run on Windows, macOS, or a Synology node, use `scripts/prepare_business_file_run.py` with config, catalog, source binding, optional target binding, and explicit files. Do not infer a source, target, or unique root from SynologyDrive, a drive letter, or a default directory.

The runtime and artifacts must be node-local and non-synced. The command creates review artifacts only, calls the archive gate once with `confirmed=False`, does not apply feedback, and never physically moves, overwrites, renames, or deletes a source. It injects `DisabledOcrProvider`: native PDF/DOCX/XLSX/Markdown/XML use native parsing, while a scanned input must stop with `OCR.CAPABILITY_DISABLED`, never falling back to Vision, RapidOCR, EasyOCR, PaddleOCR, Tesseract, MinerU, or an OFD converter. See `docs/operations/business-file-judgement-quickstart.md`.
