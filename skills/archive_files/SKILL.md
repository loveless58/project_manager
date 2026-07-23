---
name: archive_files
description: |
  文件归档能力。评估文件归档动作、生成归档决策清单(archive_manifest.v1)。

  Use when:
  - 调用方/上层能力请求"归档这个文件/这批文件到项目目录"
  - 评估"这个文件应该归到项目投标/项目弃标/项目丢标/项目执行 哪个 phase"
  - 批量生成 archive_manifest.v1 作为审计清单

  Don't use for:
  - 写项目账本(project_ledger.json / 项目总览.md) → 属于 ledger skill
  - 调 CloudCC/CRM → 属于 crm skill
  - 解析招标公告 → 属于 bid_files skill
  - 移动文件(execute_archive.py 当前是契约占位,未实现) → 需配合 execute_gate

  Output: archive_manifest.v1 (含 N 个 archive_action.v1)
  Input: capability_request.v1
---

# Archive Files Skill

## Core Concept

```
1 file  → 1 ArchiveAction (archive_action.v1)
N files → 1 ArchiveManifest (archive_manifest.v1)
1 run   → 1 CapabilityRequest → 1 CapabilityResult
```

**关键不变量**: schema 是稳定契约;**决策策略**可迭代(数据驱动)。

- **schema 稳定**: archive_action.v1 字段语义版本化(v1 已固化,未来 v2 向后兼容)
- **决策策略迭代**: 业务知识库(business_knowledge/*.md)是 source of truth,硬编码规则是 fallback,LLM 是 last resort

## Structured Output(中间产物契约)

### archive_action.v1 — 单文件决策

```json
{
  "schema_version": "archive_action.v1",
  "run_id": "run-2026-07-17-001",
  "status": "ready | needs_review | already_archived",
  "source_file": "/abs/path/to/file.pdf",
  "project_name": "...",
  "document_type": "...",
  "proposed_name": "...",
  "target_dir": "/abs/path/项目投标/<project>/原始文件",
  "target_path": "/abs/path/项目投标/<project>/原始文件/file.pdf",
  "blockers": [],
  "business_judgement": {...},
  "archive_decision": {...},
  "reason": "success | knowledge_base_missing | knowledge_base_no_match | knowledge_base_low_confidence | human_review_required | target_exists | source_missing",
  "knowledge_base_used": true,
  "pageindex_query_id": null
}
```

**关键约束**:
- `reason` 反映**决策层**语义
- `blockers` 反映**文件系统层**语义
- 调用方可执行条件: `reason == "success" and "target_exists" not in blockers and "source_missing" not in blockers`

### archive_manifest.v1 — 批量清单

聚合 N 个 ArchiveAction,加上 `by_status` 计数 + `execution_summary`(execute_archive.py 完成后填充)。

详见 [schemas/archive_manifest.schema.json](schemas/archive_manifest.schema.json)。

### capability_request.v1 / capability_result.v1 — 入口出口契约

Intent Router 路由进来 → archive_files_capability.py(规划中,当前直接调 build_archive_decision)→ 返回 result。

status_code 三值:
- `0` success — manifest 生成成功
- `2` blocked — 治理规则拦截(非错误,如 needs_review 全员)
- `3` failure — 执行失败 / 不信任请求

## High-Level Workflow

```
Phase 1: 准备         prepare_run_package.py    (规划中)
         ↓
Phase 2: 知识检索     business_knowledge/*.md + LLM 检索
         ↓
Phase 3: 三层决策     scripts/build_archive_decision.py
                       1. 知识库(高置信)
                       2. 硬编码(business_rules/archive_decision.py)
                       3. LLM 兜底(仅在 kb_missing/no_match)
         ↓
Phase 4: 文件系统校验  blockers 收集 (source_missing / target_exists)
         ↓
Phase 5: 决策组合     archive_action.v1 dict
         ↓
Phase 6: 清单聚合     archive_manifest.v1
         ↓
Phase 7: 执行         execute_archive.py        (规划中)
```

Phase 2-6 已实现(build_archive_decision.py 单文件决策 + batch 聚合)。
Phase 1 / 7 是规划中,当前通过直接调 `build_archive_action()` 跳过(由 data_cleaning_tools.py 调用)。

详见 [../../../business_rules/archive_files/workflow.md](../../../business_rules/archive_files/workflow.md)。

## Three-Layer Fallback(数据驱动核心)

**优先级**(高到低):

| 层 | 来源 | 置信度要求 | 触发条件 |
|---|---|---|---|
| 1. 业务知识库 | business_knowledge/*.md | ≥ 0.7 (高置信) | 永远 |
| 2. 硬编码规则 | business_rules/archive_decision.py | ≥ 0.5 | 知识库 missing / no_match |
| 3. LLM 通用兜底 | litellm.completion() | n/a | 知识库 missing / no_match 且硬编码 < 0.5 |

**边界约束**(硬规则 #6):

- LLM 通用兜底 **仅在** `knowledge_base_missing` 或 `knowledge_base_no_match` 时触发
- `knowledge_base_low_confidence` **不触发** LLM 兜底(避免噪声→幻觉)

**数据驱动原则**(渐进式):

1. **当前**: 硬编码(business_rules/archive_decision.py)是 ground truth
2. **渐进过渡**: 历史归档结果反向抽取到 business_knowledge/*.md,知识库覆盖硬编码
3. **目标**: 知识库是 ground truth,硬编码是 fallback,LLM 仅在冷启动期使用

详见 [../../../business_rules/archive_files/three-layer-fallback.md](../../../business_rules/archive_files/three-layer-fallback.md)。
反向抽取流程见 [../../../business_rules/archive_files/knowledge-base-pattern.md](../../../business_rules/archive_files/knowledge-base-pattern.md)。

## Critical Requirements(硬规则)

1. **不写 project_ledger.json / 项目总览.md** — 属于 ledger skill
2. **不调 CloudCC/CRM** — 属于 crm skill
3. **不解析招标公告** — 属于 bid_files skill
4. **execute 必须等 execution_gate 通过** 才能移动文件(规划中,当前不移动)
5. **capability_request 不符合 schema → exit 3**(untrusted request)
6. **archive_phase 必须是 4 状态之一**: 项目投标 / 项目弃标 / 项目丢标 / 项目执行
7. **LLM 通用兜底仅在 knowledge_base_missing / no_match 时触发**(`knowledge_base_low_confidence` 不触发)
8. **PM 内部文档归档 phase 待 redesign** — 当前返回 `needs_redesign` 标记(参见 [Known Limitations](#known-limitations))

## Other Capabilities Consumption

archive_action.v1 / archive_manifest.v1 是稳定契约,以下 skill 可消费:

| 上游(调用 archive_files) | 下游(消费 archive_files 输出) |
|---|---|
| Intent Router(能力路由) | ledger skill(写项目账本) |
| data_cleaning_tools.py(单文件调用) | crm skill(创建/更新 CRM 记录) |
| batch 入口(规划中) | bid_files skill(归档后归档招标材料) |
| 外部调用方(其他 skill / 脚本) | execution layer(execute_archive.py 消费 manifest) |

**消费示例**(ledger skill):
```python
for action in manifest["actions"]:
    if action["reason"] == "success" and "source_missing" not in action["blockers"]:
        ledger.write_project_entry(
            project_name=action["project_name"],
            archive_phase=action["archive_decision"]["archive_phase"],
            document_type=action["document_type"],
        )
```

## Implementation Layer

SKILL.md 描述契约 + 工作流;具体实现在 `scripts/`:

- `scripts/build_archive_decision.py` — **核心**。导出 `build_archive_action()` 和 `business_phase_from_path()`
- 依赖 `business_rules/archive_decision.py`(硬编码规则层)
- 依赖 `business_knowledge/*.md`(知识库层)
- 测试在 `tests/test_build_archive_decision.py`(5 个用例覆盖三层 fallback 全分支)

## Resources(按需加载)

- **[schemas/archive_action.schema.json](schemas/archive_action.schema.json)** — 单文件决策契约
- **[schemas/archive_manifest.schema.json](schemas/archive_manifest.schema.json)** — 批量清单契约
- **[schemas/capability_request.schema.json](schemas/capability_request.schema.json)** — capability 入口契约
- **[schemas/capability_result.schema.json](schemas/capability_result.schema.json)** — capability 出口契约
- **[../../../business_rules/archive_files/workflow.md](../../../business_rules/archive_files/workflow.md)** — 5 阶段工作流详细步骤
- **[../../../business_rules/archive_files/three-layer-fallback.md](../../../business_rules/archive_files/three-layer-fallback.md)** — kb → hard → llm 数据驱动原理详解
- **[../../../business_rules/archive_files/knowledge-base-pattern.md](../../../business_rules/archive_files/knowledge-base-pattern.md)** — 从历史归档反向抽取到知识库的流程
- **[../../../business_rules/archive_files/examples/01-bid-contract.md](../../../business_rules/archive_files/examples/01-bid-contract.md)** — 真实案例:投标合同归档
- **[../../../business_rules/archive_files/examples/02-internal-pm-doc.md](../../../business_rules/archive_files/examples/02-internal-pm-doc.md)** — PM 内部文档(待 redesign)
- **[../../../business_rules/archive_files/examples/03-source-missing.md](../../../business_rules/archive_files/examples/03-source-missing.md)** — 文件缺失场景
- **[docs/ocr-baseline.md](../../docs/ocr-baseline.md)** — OCR 引擎优先级链 + Vision vs RapidOCR 对比基线 (v0.4.0, 2026-07-23)

## Known Limitations

- **PM 内部文档归档 phase 待 redesign**(硬规则 #8)
  - 当前识别:文件名/文本含 `project_manager` / `prd-project-manager` / `归档摘要报告` / `全量文件审计报告` / `项目总览` / `file organization` / `workspace config` / `ocr provider` 等标记
  - 当前行为:返回 `archive_action.v1` 带 `archive_phase=null` + `reasons=["pm_internal_archive_needs_redesign"]`,status=needs_review
  - 本轮框架阶段不动代码,后续启动 redesign 子任务
- **execute_archive.py 未实现**(workflow Phase 7)
  - 当前只能产出 manifest,不能真正移动文件
  - 调用方需自行实现执行逻辑(在 execution_gate 通过后)
- **业务知识库仅种子**(business_knowledge/归档规则_v1.md)
  - 当前知识库是从硬编码规则反向抽取的种子
  - 历史归档反向抽取 → 知识库扩种(规划中,渐进式数据驱动)
- **LLM 兜底依赖 GPUStack 服务可用**(环境变量:OPENAI_API_BASE 默认 http://172.18.125.202:9990/v1)

## Iteration Log

- **v0.4.0 (2026-07-23)**: OCR 优先级链调整 (Vision → RapidOCR → EasyOCR → Tesseract);PyObjC VNRecognizeTextRequest 中文识别乱码 bug 标记废弃;新增 `integrations/macos_vision_bridge/swift_ocr_bridge` Swift 子进程桥接;实测 Vision 4.62x 快于 RapidOCR (6 份样本 / 44 页)。详见 [docs/ocr-baseline.md](../../docs/ocr-baseline.md)
- **v0.3.0 (2026-07-17)**: 重写 SKILL.md 为 financial-services 风格;新增 archive_manifest.v1 / capability_request.v1 / capability_result.v1 schema;修复 _resolve_final_reason 决策层/文件系统层语义混淆 bug(4 个失败测试变绿)
- **v0.2.0 (2026-07-16)**: 三层 fallback 实现 + LLM 边界硬规则
- **v0.1.0 (2026-07-15)**: skill baseline(build_archive_decision.py + archive_action.schema.json)
