---
name: cloudcc_crm
version: 0.2.0
description: |
  CloudCC/CRM skill。负责只读探针、查重证据、CRM 草稿、提交前确认和提交后回读。
---

# CloudCC/CRM

## 定位

本 skill 是外部 CRM 的受控边界。默认实现使用安全 fake adapter；无真实浏览器适配器或登录态不可验证时，只能返回 `blocked` 或本地草稿结果。

运行时工具事实来源是 `ToolRegistry`、`main.SKILL_TOOL_MAP` 和 `governance/project_schema.json`。本文只描述权限边界和模型参考策略，不维护第二份完整工具表。

## 输入输出

输入可以是项目编号、项目名称、客户名称、对象类型、查询条件或 CRM 草稿上下文。输出必须包含状态、操作名、证据、阻塞原因或待确认动作。

工具返回应使用 CloudCC 结果信封：

```json
{
  "schema_version": "cloudcc.crm.result.v1",
  "status": "success | blocked | needs_confirmation | failed",
  "evidence": {},
  "pending_confirmation": null,
  "blocked_reason": "",
  "secrets_included": false
}
```

## 硬规则

- `blocked` 不能解释为未查到重复。
- 任何 CRM 写入都必须停在提交前确认门。
- 不暴露账号、密码、Cookie、token 或其他 secret。
- 不把模型摘要当成 CRM 页面证据。
- 外部系统写入成功与否以回读证据为准，不以工具意图为准。
- 回读只能校验已确认提交的记录，不能补写字段。

## 软规则

优先先探测登录态和浏览器适配器，再进行只读查询或查重。草稿准备可以在本地完成，但填写和提交必须分开，提交前必须返回 `needs_confirmation`。

## 派生说明

完整工具列表和参数由 `ToolRegistry` 生成，治理校验通过 `python -X utf8 -B governance\validate.py tools` 覆盖。README 可以摘要能力，但不得重新定义本 skill 的工具契约。
