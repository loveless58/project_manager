---
name: opportunity_management
version: 0.2.0
status: draft   # 无实现,仅 SKILL.md,2026-07-23 标记
description: |
  商机管理 skill。负责招标公告解析、本地重复检测、bid_context 和 CRM 建议草稿。
---

# 商机管理

## 定位

本 skill 处理“新机会与线索”阶段的本地商机材料。它从招标公告等原始资料中提取候选字段，生成标准化商机上下文和 CRM 建议草稿。

它不读取或填写 CloudCC 页面，不执行最终商机创建，也不把本地重复检测当成 CRM 权威查重。运行时工具事实来源是 `ToolRegistry`、`main.SKILL_TOOL_MAP` 和 `governance/project_schema.json`。

## 输入输出

输入可以是公告目录、公告文件路径、项目编号、候选字段或结构化商机上下文。输出可以是公告解析结果、本地重复检测证据、`bid_context` 或 CRM 建议草稿。

默认工作区是：

```text
新机会与线索/
  招标公告/
  bid_contexts/
  商机列表.md
  重复检测记录.md
```

## 硬规则

- 公告解析结果是候选事实，进入项目账本前仍需证据和冲突处理。
- 本地重复检测不能替代 CloudCC/CRM 只读查重。
- CRM 写入必须转交 CloudCC/CRM skill，并停在确认门前。
- 不覆盖人工确认后的客户、销售、金额等高风险字段。
- 文件不可读、格式不支持、字段缺失时必须保留失败原因。

## 软规则

优先先定位公告，再解析候选字段，之后做本地重复检测并生成结构化上下文。需要外部系统创建或更新时，输出草稿和下一步，不直接执行。

## 派生说明

完整工具列表和参数由 `ToolRegistry` 生成，治理校验通过 `python -X utf8 -B governance\validate.py tools` 覆盖。README 可以摘要能力，但不得重新定义本 skill 的工具契约。
