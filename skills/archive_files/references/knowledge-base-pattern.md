# Knowledge Base Pattern — 从历史归档反向抽取到业务知识库

本文档描述如何从历史 archive_manifest.json 反向抽取归档规则,扩种到 `business_knowledge/*.md`,实现数据驱动的策略优化。

## 核心循环

```
历史 archive_manifest.v1
  ↓
抽取高频 pattern (按文件名/字段/phase 分组)
  ↓
提炼规则(候选 markdown 条目)
  ↓
人工 review
  ↓
追加到 business_knowledge/归档规则_v*.md
  ↓
下次归档自动用上
```

**关键**: 这一循环是**渐进式**的 — 不需要一次性把硬编码全部替换,可以从高频 pattern 开始,慢慢覆盖。

## 步骤 1: 收集历史数据

**数据源**: 已经执行过的 `archive_manifest.v1` 文件(或执行后的 `_execution_summary` 字段)。

```python
# 伪代码
manifests = collect_historical_manifests(workspace_root)
# 返回 List[archive_manifest.v1]
```

**典型规模**: 每天 5-20 个归档动作,积累 1 个月 = 150-600 个 ArchiveAction。

## 步骤 2: 抽取高频 pattern

按"决策路径 + 文件特征"分组,统计频率。

```python
# 伪代码: 按 filename pattern 分组
from collections import Counter
patterns = Counter()
for manifest in manifests:
    for action in manifest["actions"]:
        if action["reason"] == "success":
            # 按文件名规则匹配
            if re.match(r"^[A-Z]+软件外包.*\.pdf$", action["proposed_name"]):
                patterns["[A-Z]+软件外包.*.pdf"] += 1
            # 按决策路径分组
            decision_path = action["archive_decision"].get("decision_path", "unknown")
            patterns[decision_path] += 1
```

**目标**: 找到出现 ≥ 5 次的模式(避免噪声)。

## 步骤 3: 提炼规则

每条高频 pattern → 1 条候选规则(markdown 形式):

```markdown
## 规则 N: [规则名]

**触发条件**: [文件名 pattern / 字段值 / 路径前缀]
**归档决策**: [archive_phase + project_name 提取规则]
**置信度**: [基于历史数据的命中率]
**历史样本数**: [N]

### 例子
- [真实文件 1] → [真实归档结果]
- [真实文件 2] → [真实归档结果]
```

## 步骤 4: 人工 review

**为什么需要人工**:
- LLM 抽取可能误判上下文(例:把"项目弃标"路径误归到"项目执行")
- 高频但错误的模式不能入知识库
- 边界 case 需要业务专家判断

**review checklist**:
- [ ] 触发条件是否过于宽泛(容易误命中)?
- [ ] 决策结果是否符合业务直觉?
- [ ] 例子是否真实(没有编造)?
- [ ] 与现有规则是否冲突?

## 步骤 5: 追加到知识库

**格式**: 知识库 md 文件按版本号管理:

```
business_knowledge/
├── 归档规则_v1.md   (初始种子)
├── 归档规则_v2.md   (扩种 1)
├── CHANGELOG.md     (版本日志)
```

每个 v2 在 CHANGELOG 标注:
```markdown
## v2 (2026-MM-DD)
- 新增规则 N: [名称](+X 个历史样本支持)
- 调整规则 M: [变更原因]
```

**避免破坏 v1**: 知识库读取按文件名 glob,新文件自动包含。

## 当前知识库状态(v1)

`business_knowledge/归档规则_v1.md` 是种子,内容从 `business_rules/archive_decision.py` 反向抽取。

种子包含:
- 业务阶段定义(4 状态)
- PM 内部文档识别硬约束
- 业务阶段判断优先级(路径 > 字段值 > 默认)
- 目标 Bucket(数据资产 vs 原始文件)
- Blockers 标记
- Confidence 评分规则

**未覆盖**(留给 v2+):
- 具体文件名 pattern(如"软件外包"标题 → 项目投标)
- 项目名提取规则(从文件名/合同字段)
- 文档类型细分(投标响应 / 中标通知 / 验收报告)

## 调优指标

监控以下指标判断策略优化效果:

| 指标 | 当前(v0.3.0) | 目标(v0.5.x) |
|---|---|---|
| 知识库命中率(`kb_result.reason == "success"` 比例) | < 10% | > 50% |
| 硬编码 fallback 率 | ~80% | < 30% |
| LLM 兜底触发率 | ~10% | < 5% |
| needs_review 比例 | 待统计 | < 20% |

**注意**: 这些指标需要真实归档数据。当前是规划,待执行。

## 不该做的事

- ❌ **不要**自动把高频 pattern 直接写入知识库 — 必须人工 review
- ❌ **不要**一次性迁移所有硬编码到知识库 — 渐进式,先覆盖高频
- ❌ **不要**让知识库覆盖硬编码的边界硬约束(如 4 阶段限制、LLM 触发条件) — 这些是产品决策,不是数据可决定的
- ❌ **不要**把失败案例(`needs_review`、`source_missing`)反向抽取 — 它们不代表正确决策
