---
name: archive_files
version: 0.2.0
description: |
  文件归档能力模块。负责评估文件归档动作、执行文件移动、生成归档清单。
  不写项目账本（属于 ledger skill），不调 CloudCC/CRM（属于 crm skill），
  不解析招标公告（属于 bid_files skill）。
---

# archive_files

## 定位

把源文件移动到业务目录（项目投标/项目弃标/项目丢标/项目执行）下的对应项目子目录，并产出 archive_manifest.json 作为审计清单。

**不做的事**（边界硬约束）：
- 不写 project_ledger.json / 项目总览.md（属于 ledger skill）
- 不调 CloudCC/CRM（属于 crm skill）
- 不解析招标公告（属于 bid_files skill）

## 输入输出

- 输入：`capability_request.json`（main.run 通过 Intent Router 路由进来）
- 输出：`capability_result.json`（含 archive_manifest.json 路径 + 执行状态）

错误码：

```
0 = success          动作完成
2 = blocked          被治理规则拦截（不是错误）
3 = failure          执行失败 / 不受信请求
```

## 工作流

```
1. prepare_run_package.py        收集 source files、生成 run_id
2. build_archive_decision.py     决定每个文件去哪个 phase 目录
3. archive_files_capability.py   入口：校验 schema → 调 2 → 调 execute → 返回 result
4. execute_archive.py            在 execution_gate 通过后移动文件、写 manifest
```

## 硬规则

1. **不写 project_ledger.json / 项目总览.md**（属于 ledger skill）
2. **不调 CloudCC/CRM**（属于 crm skill）
3. **execute 必须等 execution_gate 通过**才能移动文件
4. **capability_request 不符合 schema → exit 3**（untrusted request）
5. **archive_phase 必须是 4 状态之一**（项目投标/项目弃标/项目丢标/项目执行）
6. **LLM 通用兜底仅在业务知识库真正缺失时触发**（`reason ∈ {knowledge_base_missing, knowledge_base_no_match}`）；`knowledge_base_low_confidence` 不触发 LLM，避免噪声→幻觉

## 业务知识库（v0.2.0 新增）

物理位置：`<项目根>/business_knowledge/*.md`

三层 fallback（按 `scripts/build_archive_decision.py` 实现）：
1. 业务知识库（LLM 读 md 总结）—— 高置信度（≥0.7）直接用
2. 硬编码规则（`business_rules/archive_decision.py`）—— 置信度 ≥0.5 时用
3. LLM 通用兜底 —— 仅在知识库缺失/无结果时触发

边界约束（硬规则 #6）：低置信度（`kb_low_confidence`）走硬编码，**不触发 LLM 兜底**，避免知识库噪声被 LLM 放大成幻觉。

## 软规则

- 失败的部分不污染成功的部分（部分失败保留已成功的结果）
- 所有 run artifacts 在 Skill repo 外的 workspace
- PM 内部文档归档返回 `needs_redesign` 标记，不尝试归档
