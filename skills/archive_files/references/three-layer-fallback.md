# Three-Layer Fallback — 数据驱动决策原理

archive_files skill 用三层 fallback 决定归档 phase:**业务知识库 → 硬编码规则 → LLM 通用兜底**。本文档解释为什么这样设计、每层做什么、边界约束是什么。

## 为什么是三层(不是一层)

业务上,归档 phase 判定有 3 个信号源:

1. **历史经验** — 已经处理过的同类文件,模式可复用(知识库)
2. **业务规则** — 项目类型 + 字段值映射的硬规则(硬编码)
3. **通用推理** — 看文件路径/名字直接推断(LLM)

**单层不够**:
- 只有硬编码 → 规则僵化,新场景无法适应
- 只有 LLM → 慢、贵、不稳定、容易幻觉
- 只有知识库 → 冷启动时为空,无法工作

**三层组合**:
- **冷启动期**: 知识库空 → 走硬编码 → LLM 兜底
- **运行期**: 知识库扩种 → 高置信直接用 → 硬编码 / LLM 退化为 fallback
- **理想态**: 知识库是 ground truth,硬编码 / LLM 几乎不用

## 每层职责

### Layer 1: 业务知识库

**来源**: `business_knowledge/*.md`

**读取**: `_read_knowledge_base()` 把所有 md 合并,作为 LLM prompt 上下文。

**检索**: `_search_knowledge_base()` 用 LLM 在 md 中检索最相关的归档决策。

**置信度分级**:
| LLM 输出 | 判定 |
|---|---|
| phase ≠ unknown 且 confidence ≥ 0.7 | `success` (用知识库结果) |
| confidence 0.3-0.7 或 phase=unknown | `knowledge_base_low_confidence` |
| confidence < 0.3 | `knowledge_base_no_match` |

**关键**:
- **失败/缺失不算错误** → 知识库就是空,fall through 到下一层
- **不阻塞**,永远尝试一次
- **冷启动友好**: 空知识库返回 `knowledge_base_missing`,不影响主流程

### Layer 2: 硬编码规则

**来源**: `business_rules/archive_decision.py`

**调用**: `evaluate_archive_decision(source_file, extracted, business_judgement, project_files_dir)`

**输出**: `archive_decision.v1` dict,包含 archive_phase + confidence + blockers

**置信度模型**:
| 场景 | confidence | reason(回填到 archive_action) |
|---|---|---|
| PM 内部文档 | 0.0 | needs_redesign |
| 有 project_name + 项目丢标 | 0.72 | success |
| 有 project_name + 其他 phase | 0.45 | knowledge_base_low_confidence(或 success 取决于知识库状态) |
| 无 project_name | 0.0 | knowledge_base_no_match |

**触发条件**: 仅当知识库 `missing` 或 `no_match` 时

### Layer 3: LLM 通用兜底

**来源**: litellm.completion(独立 prompt,不基于知识库)

**调用**: `_llm_general_fallback(source_file, hard_phase)`

**触发条件**(硬规则 #6): **仅在** `kb_result["reason"] ∈ {knowledge_base_missing, knowledge_base_no_match}` 时

**不触发**(`knowledge_base_low_confidence`): 避免知识库低置信度被 LLM 放大成幻觉

**输出**: `archive_action.v1` reason=success(若 LLM 给的 phase ≠ unknown)或 knowledge_base_low_confidence

## 数据驱动原则(渐进式)

**当前阶段**(v0.3.0):
```
知识库: business_knowledge/归档规则_v1.md (种子,从硬编码反向抽取)
硬编码: 5 个 if-elif 分支(evaluate_archive_decision)
LLM: GPUStack + MiniMax,默认 10s 超时
```

**演进方向**:

```
阶段 A (v0.4.x): 知识库扩种
  - 从历史 archive_manifest.json 反向抽取高频模式
  - 把硬编码规则"翻译"成知识库规则(markdown 形式)
  - 知识库覆盖率 ↑ → 硬编码 fallback 频率 ↓

阶段 B (v0.5.x): 知识库是 source of truth
  - 硬编码仅在知识库 confidence < 0.5 时兜底
  - 知识库版本号管理(business_knowledge/CHANGELOG.md)

阶段 C (v0.6.x): 自动反向抽取
  - execute_archive.py 完成后,自动把 archive_action.v1 写入历史库
  - 定时任务: 从历史库抽取高频 pattern → 知识库候选
  - 人工 review 后入知识库

阶段 D (持续): 策略优化
  - 收集"决策错误"案例(user feedback / 后置发现归档错)
  - 分析瓶颈规则
  - 修订知识库规则
```

## 边界硬约束(再次强调)

1. **LLM 通用兜底仅在 `kb_missing` / `kb_no_match` 时触发** — `kb_low_confidence` 不触发
2. **archive_phase 必须是 4 状态之一**: 项目投标 / 项目弃标 / 项目丢标 / 项目执行
3. **PM 内部文档返回 needs_redesign** — 不尝试归档到业务项目目录
4. **决策层 reason 与文件系统层 blocker 分离**(v0.3.0 起明确) — `_resolve_final_reason` 保证 reason 反映决策结果,blocker 反映文件系统状态

## 调优方向(给维护者)

如果发现 fallback 走了不该走的分支:

1. **kb_low_confidence 频繁触发 LLM** → 检查是否 `_three_layer_fallback` 的 LLM 触发条件错了(应该是 kb_missing/no_match 才触发)
2. **硬编码频繁 fallback 到 LLM** → 说明硬编码规则覆盖不全,扩种知识库
3. **LLM 频繁返回 unknown phase** → 检查 LLM prompt 是否清晰,知识库是否需要补规则
4. **reason/source_missing 混淆** → v0.3.0 已修,验证 `_resolve_final_reason` 逻辑
