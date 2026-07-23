# Example 02: PM 内部文档(needs_redesign placeholder)

## 场景

尝试归档一个 PM 内部治理文档(如 PRD、审计报告)。这类文档**不应该归档到业务项目目录**,需要 redesign。

## 输入

**文件**: `PRD-project-manager-ocr.md`(项目根目录的 PRD)

**extracted**:
```python
extracted = {
    "document_type": "项目治理文档",
    "extracted_text": "...Project Manager OCR Provider 设计方案...",
    "fields": {"project_name": "project_manager"},
}
```

## 调用

```python
action = build_archive_action(
    run_id="run-2026-07-17-002",
    source_file="${BUSINESS_ROOT}/PRD-project-manager-ocr.md",
    project_name="project_manager",
    extracted=extracted,
    ledger_result={"business_judgement": {}},
    project_files_dir="${BUSINESS_ROOT}/项目文件/项目投标",
)
```

## 决策路径

1. **Layer 2 硬编码 `_is_project_manager_internal_document()`**: 
   - filename = "PRD-project-manager-ocr.md"
   - text 含 "project_manager" / "ocr provider" 等 markers
   - **判定: PM 内部文档** → 直接 return,不走知识库/LLM
2. 三层 fallback 不触发

## 输出 archive_action.v1

```json
{
  "schema_version": "archive_action.v1",
  "run_id": "run-2026-07-17-002",
  "status": "needs_review",
  "source_file": "${BUSINESS_ROOT}/PRD-project-manager-ocr.md",
  "project_name": "project_manager",
  "document_type": "项目治理文档",
  "proposed_name": "",
  "target_dir": null,
  "target_path": null,
  "blockers": ["pm_internal_archive_pending_redesign"],
  "business_judgement": {},
  "archive_decision": {
    "subject_type": "internal_project",
    "subject_name": "project_manager",
    "archive_phase": null,
    "document_type": "项目治理文档",
    "confidence": 0.0,
    "target_dir": null,
    "target_path": null,
    "blockers": ["pm_internal_archive_pending_redesign"],
    "human_review_required": true,
    "reasons": ["pm_internal_archive_needs_redesign"]
  },
  "reason": "success",
  "knowledge_base_used": false,
  "pageindex_query_id": null
}
```

## 关键观察

- `archive_phase = null`(硬编码层提前 return,fallback 没覆盖)
- `blockers = ["pm_internal_archive_pending_redesign"]`(注意这里 blocker 不是文件系统错误,而是产品决策标记)
- `status = "needs_review"`(因 blockers 非空)
- `reason = "success"`(决策层是 "PM 内部文档",硬编码 return 不算失败;但 target_path=None 所以无法执行)

## 调用方处理

```python
# 不应该执行归档(target_path 为 None)
assert action["target_path"] is None
assert action["status"] == "needs_review"

# 但 reason=success,这是 v0.3.0 决策层语义独立后的行为
# 调用方应该读 status 而非仅 reason 来判断可执行性
if action["status"] == "ready":
    execute_archive(action)
elif action["status"] == "needs_review":
    # 走人工复核 / 标记 needs_redesign
    human_review_queue.push(action)
```

## Known Limitation(本轮框架阶段)

- `_is_project_manager_internal_document()` 的 marker 列表是硬编码
- PM 内部文档归档 phase 应该如何定义(归到 `项目治理/` 目录?归到 `_internal/`?独立目录?)— 待 redesign
- 本轮框架阶段不动代码,仅占位 + 标 needs_redesign
- 后续启动 redesign 子任务,可能改动:
  - 新增 `_internal_phase` 字段
  - 单独的 `项目治理/` phase(扩 4 状态到 5 状态)或保留为独立目录
  - 知识库增加 PM 文档归档规则
