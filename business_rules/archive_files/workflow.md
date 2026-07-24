# Workflow — archive_files 5 阶段执行流程

本文档描述 archive_files skill 端到端执行的详细步骤。每个文件 → 1 个 ArchiveAction;每批 → 1 个 ArchiveManifest。

## 输入输出契约

- **输入**: `capability_request.v1` (见 [schemas/capability_request.schema.json](../schemas/capability_request.schema.json))
- **输出**: `capability_result.v1` + `archive_manifest.v1` (见 [schemas/](../schemas/))

## Phase 1: 准备(规划中)

**目标**: 生成 `run_id`,收集 source files,组装 capability_request。

当前状态: **未实现**。直接调用 `build_archive_action()` 跳过此阶段。

规划接口(由 `prepare_run_package.py` 提供):
```python
request = prepare_run_package(
    source_dir="/abs/path/to/source",
    project_name="...",
    project_files_dir="...",
)
# → capability_request.v1 dict
```

## Phase 2: 知识检索

**目标**: 对每个 source_file,尝试从业务知识库获取归档决策。

实现位置: `_search_knowledge_base()` in `scripts/build_archive_decision.py`

读取 `business_knowledge/*.md`,合并后通过 LLM 检索:
- 业务知识库目录为空 → 返回 `knowledge_base_missing`
- LLM 调用成功且 confidence ≥ 0.7 → 返回 `success` (含 phase)
- LLM 调用成功但 confidence 0.3-0.7 → 返回 `knowledge_base_low_confidence`
- LLM 置信 < 0.3 或 phase=unknown → 返回 `knowledge_base_no_match`

## Phase 3: 三层决策

**目标**: 综合知识库 + 硬编码 + LLM 兜底,产生最终决策。

实现位置: `_three_layer_fallback()` in `scripts/build_archive_decision.py`

详见 [three-layer-fallback.md](three-layer-fallback.md)

## Phase 4: 文件系统校验

**目标**: 收集不影响决策但影响执行的因素。

实现位置: `build_archive_action()` 第 382-393 行

收集的 blockers:
- `source_missing` — `os.path.exists(source_file) == False`
- `target_exists` — `os.path.exists(target_path) and source != target`
- (无 `human_review_recommended` — 已在 evaluate_archive_decision 阶段处理)

## Phase 5: 决策组合

**目标**: 把 decision + blockers + fallback result 组合成 `archive_action.v1` dict。

实现位置: `build_archive_action()` 后半段(第 360-411 行)

关键步骤:
1. 应用 fallback final_phase 到 decision.archive_phase (覆盖硬编码)
2. 计算 proposed_name + target_path
3. 检测 already_archived (source_abs == target_abs)
4. 收集 blockers
5. 调用 `_resolve_final_reason()` 决定 reason

## Phase 6: 清单聚合(规划中)

**目标**: 把 N 个 ArchiveAction 聚合成 ArchiveManifest,生成 `archive_manifest.v1`。

当前状态: **未独立实现**。调用方需自行聚合。

规划接口:
```python
manifest = aggregate_manifest(
    run_id="...",
    source_root="/abs/path",
    actions=[archive_action, archive_action, ...]
)
# → archive_manifest.v1 dict,含 by_status 计数
```

## Phase 7: 执行(规划中)

**目标**: 在 execution_gate 通过后,根据 manifest 实际移动文件。

当前状态: **未实现**。这是 known limitation。

规划接口(由 `execute_archive.py` 提供):
```python
def execute_archive(manifest, *, execution_gate: bool) -> dict:
    assert execution_gate, "execution_gate must be True"
    # 遍历 status=ready 的 actions
    # os.rename 或 shutil.move
    # 更新 manifest.execution_summary
    return updated_manifest
```

## 端到端调用示例(当前可跑)

```python
from skills.archive_files.scripts.build_archive_decision import build_archive_action

# 单文件调用
action = build_archive_action(
    run_id="run-001",
    source_file="/abs/path/file.pdf",
    project_name="...",
    extracted={"document_type": "...", "fields": {"project_name": "合成项目字段008"}},
    ledger_result={"business_judgement": {}},
    project_files_dir="/abs/path/项目投标",  # 项目投标/项目弃标/项目丢标/项目执行 父目录
)

# 判断可执行
if action["reason"] == "success" and "source_missing" not in action["blockers"] and "target_exists" not in action["blockers"]:
    # 可执行归档
    import shutil
    shutil.move(action["source_file"], action["target_path"])
```

## 调用方注意事项

- **batch 入口缺失**: 当前只能循环单文件调用,无 batch 优化(每次 LLM 调用都是独立的,知识库 md 也每次重读)
- **LLM 兜底调用延迟**: 单文件约 1-3 秒(取决于 GPUStack 服务);批量时建议合并 prompt 或预热知识库
- **执行必须在 capability_result.status_code=0 后**: 治理规则拦截(status=2)或失败(status=3)时不执行
