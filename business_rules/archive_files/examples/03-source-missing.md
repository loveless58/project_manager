# Example 03: 源文件不存在场景

## 场景

调用方传了一个不存在的文件路径(可能是 typo、上游传入错误、文件已被移动)。系统应该明确报错并提示,但不掩盖决策层结果(如果决策层成功)。

## 输入

**文件**: `/nonexistent/path/file.pdf`(不存在)

**extracted**:
```python
extracted = {
    "document_type": "...",
    "fields": {"project_name": "合成项目字段006"},
}
```

## 调用

```python
action = build_archive_action(
    run_id="run-2026-07-17-003",
    source_file="/nonexistent/path/file.pdf",
    project_name="合成项目字段006",
    extracted=extracted,
    ledger_result={"business_judgement": {}},
    project_files_dir="/tmp/archive_test",
)
```

## 决策路径

1. **Layer 1 知识库**: 无匹配 → `knowledge_base_no_match`
2. **Layer 2 硬编码**: 有 project_name → archive_phase = "项目投标", confidence = 0.45
3. **Layer 3 LLM 兜底**: 假设 LLM 返回 phase = "unknown"(LLM 也无法判断)
4. **三层 fallback 返回**: `final.reason = "knowledge_base_low_confidence"` (LLM 兜底失败后)
5. **Phase 4 文件系统校验**: `os.path.exists("/nonexistent/path/file.pdf") == False`
   - `blockers = ["source_missing"]`
6. **`_resolve_final_reason` 判定**:
   - final_reason = "knowledge_base_low_confidence"
   - final.llm_fallback_used = True
   - 走到"决策层失败 + LLM 兜底未救回"分支
   - "source_missing" in blockers → return "source_missing"

## 输出 archive_action.v1

```json
{
  "schema_version": "archive_action.v1",
  "run_id": "run-2026-07-17-003",
  "status": "needs_review",
  "source_file": "synthetic/SYN-SOURCE-007.docx",
  "project_name": "合成项目字段006",
  "document_type": "...",
  "proposed_name": "file.pdf",
  "target_dir": "/tmp/archive_test/项目投标/合成项目字段006/原始文件",
  "target_path": "/tmp/archive_test/项目投标/合成项目字段006/原始文件/file.pdf",
  "blockers": ["source_missing"],
  "business_judgement": {},
  "archive_decision": {...},
  "reason": "source_missing",
  "knowledge_base_used": false,
  "pageindex_query_id": null
}
```

## 关键观察

- `status = "needs_review"`(因 blockers 非空)
- `reason = "source_missing"`(决策层失败 + 文件系统错,提升为文件系统 reason)
- `target_path` 仍计算(给调用方参考"如果文件存在,会归到这里")
- `archive_decision.archive_phase = "项目投标"`(决策层给了 phase,但 LLM 兜底失败)

## 调用方处理

```python
# 三种处理路径

# 选项 A: 直接报错(适用于"上游传入错误,要求上游修复")
if action["reason"] == "source_missing":
    raise SourceFileNotFound(action["source_file"])

# 选项 B: 入队等待(适用于"文件可能被外部进程暂时删除")
if action["reason"] == "source_missing":
    retry_queue.push(action, delay=60)  # 1分钟后重试

# 选项 C: 标记 needs_human_review(适用于"不确定,人工核实")
if action["status"] == "needs_review":
    human_review_queue.push(action)
```

## 与 v0.2.0 的区别

**v0.2.0 行为**(修复前):
- 任何文件不存在都让 reason = "source_missing",掩盖了"决策层 KB 高置信度成功"的情况
- 例: KB 高置信度给 success,文件不存在 → reason 仍变 source_missing,丢失决策信息

**v0.3.0 行为**(修复后):
- 决策层真成功 → reason = "success",blocker 单独反映 source_missing
- 决策层失败 + 文件系统错 → reason 提升为 source_missing(测试 5 场景)
- 决策层失败但 LLM 兜底成功 → reason = "success",blocker 反映 source_missing
- 决策层低置信度(无 LLM 触发)→ reason = "knowledge_base_low_confidence"

详见 [SKILL.md §Three-Layer Fallback](../SKILL.md) 和 `scripts/build_archive_decision.py::_resolve_final_reason`。
