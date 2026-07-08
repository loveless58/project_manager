# 已弃标/待报名状态冲突复盘与修复

## 背景

在清理 `E:\SynologyDrive\项目文件\项目丢标\居家药学服务系统_V1_0` 时发现：

- 项目位于 `项目丢标` 业务目录。
- 账本当前事实中同时出现 `bid_status=已弃标` 和 `registration_status=待报名`。
- `business_judgement` 仍被判为 `closed / 已弃标 / low risk`。

这暴露出一个业务规则漏洞：系统能根据目录上下文把项目判为 closed，但没有检查报名状态与中标/弃标状态之间的组合一致性。

## 业务原则

项目业务流转顺序是：

```text
报名/决定是否参与
-> 投标/开标
-> 中标、丢标、弃标或执行
```

因此：

- `待报名` 表示项目仍处于报名前或报名决策前。
- `已弃标` 表示已经决定不参与或放弃参与。
- `已丢标/未中标` 表示已经参与后失败。

`已弃标` 与 `待报名` 不能作为同一项目的当前事实同时成立。出现这种组合时，必须标记为状态冲突，而不是默认为低风险 closed。

## 根因

### 1. 业务判断层缺少状态组合一致性检查

`business_rules/bid_project_rules.py` 之前只用 `bid_status` 和 `lifecycle_stage` 判断业务阶段：

```text
bid_status 包含 弃标/丢标/未中标 -> closed
```

但没有检查：

```text
closed + registration_status=待报名
```

这种组合是否矛盾。

### 2. 路径上下文覆盖不完整

`tools/data_cleaning_tools.py::_apply_source_path_context()` 能根据 `项目丢标` 目录把：

```text
bid_status -> 已弃标
lifecycle_stage -> closed
closed_reason_type -> abandoned_by_us
```

但之前没有同步把 `registration_status` 从旧记录里的 `待报名` 收敛为 `已弃标`。

### 3. 派生 Markdown 与 JSON 账本可能不同步

如果账本当前事实被更新后没有同步重写 `项目总览.md`，Markdown 仍可能展示旧字段或旧候选事实。

## 修复策略

### 代码防线

1. 在 `BidProjectRuleEngine` 中增加状态一致性规则：

```text
closed 或 bid_status=已弃标/已丢标/未中标
+ registration_status=待报名
=> risk_level=high
=> human_review_required=true
=> data_quality_flags 包含 报名状态与中标状态冲突
```

2. 在 `DataCleaningTools._apply_source_path_context()` 中补齐目录上下文：

```text
项目丢标 + abandoned_by_us
=> registration_status=已弃标
```

这样旧 `项目记录.md` 中的 `待报名/待开标` 不会再次污染当前事实。

### 数据修复原则

只自动修复确定性问题：

- 删除 `待确认`、表格残片、合同句子等明显污染字段。
- 对 `项目丢标` 目录下已确定为弃标的项目，将源记录状态收敛为弃标语义。
- 重算 `business_judgement`。
- 同步重写 `项目总览.md` 和全局 `投标进度总览.html`。

不自动推断缺失客户或销售：

- 如果没有可信来源，不补写 `customer_name`。
- 如果没有可信来源，不补写 `sales_owner`。

## 回归测试

新增/更新测试覆盖：

- `test_business_rules_flag_closed_project_with_pending_registration_as_conflict`
  - 验证 `已弃标 + 待报名` 会被判为高风险状态冲突。

- `test_project_lost_directory_context_overrides_record_status_and_keeps_outputs_unique`
  - 验证 `项目丢标` 路径上下文会同时覆盖：
    - `bid_status=已弃标`
    - `registration_status=已弃标`
    - `lifecycle_stage=closed`
    - `closed_reason_type=abandoned_by_us`

## 操作检查清单

处理类似问题时按以下顺序执行：

1. 只读检查 `project_ledger.json` 的 `current_facts`、`conflicts`、`business_judgement`。
2. 对照源 `项目记录.md`，确认是源记录旧状态、抽取错误，还是目录上下文覆盖缺失。
3. 如果是规则漏洞，先补回归测试，再改业务规则。
4. 如果是历史账本污染，先备份同目录 `project_ledger.json`，再做最小清理。
5. 清理后重算 `business_judgement`。
6. 重写 `项目总览.md`。
7. 重生成 `E:\SynologyDrive\投标进度总览.html`。
8. 复核全局统计和单项目状态是否一致。

## 本次涉及的真实项目

```text
E:\SynologyDrive\项目文件\项目丢标\居家药学服务系统_V1_0
```

已确认的问题：

- `project_ledger.json` 当前事实已清掉 `待确认` 客户/销售污染。
- `project_ledger.json` 已把 `registration_status=待报名` 修正为 `已弃标`。
- `项目总览.md` 已重新生成，避免继续展示旧事实。
- `项目记录.md` 源状态已从 `待报名/待开标` 收敛为 `已弃标`，后续重复抽取不再依赖路径上下文兜底。

## 业务层结构化识别不足

后续复核发现：

```text
E:\SynologyDrive\项目文件\项目丢标\居家药学服务系统_V1_0
E:\SynologyDrive\项目文件\项目执行\居家药学服务系统项目
```

这两个目录存在名称相似和状态冲突。问题不应定义为“需要项目别名归一/自动合并”，而应定义为业务层结构化识别能力不足：

- 业务层没有把“名称相似”作为结构化信号输出。
- 业务层没有把“项目丢标 vs 项目执行”的状态冲突清晰输出为待复核样本。
- 业务层不应直接决定目录合并、账本迁移或项目名改写。

### 根因

当前抽取和账本更新容易把目录上下文、文件名、项目记录状态混在一起，导致结构化输出没有清楚表达：

- 两个名称是否只是相似，而不是已经确认同一项目。
- 证据来自哪个文件、哪个目录、哪个抽取方法。
- 状态冲突是确定事实，还是需要人工复核。

### 修复策略

修复方向是输出结构化判断样本，而不是增加运维脚本、自动合并工具或自动项目名归一：

```json
{
  "schema_version": "business_case.v1",
  "case_type": "project_identity_or_status_conflict",
  "signals": {
    "name_similarity": "high",
    "status_conflict": true,
    "evidence_conflict": true
  },
  "output": {
    "decision": "needs_review",
    "reason": "名称相似但状态冲突，业务层不自动合并、不自动覆盖"
  }
}
```

业务层只输出结构化信号、证据、冲突和不确定性；文件移动、目录合并、账本迁移必须保持在归档执行链路之外，且不得由业务层自动触发。

回归测试：

- `tests/test_archive_decision.py::test_archive_decision_preserves_project_name_and_does_not_auto_merge_aliases`
- `tests/test_loop.py::test_project_ledger_does_not_auto_merge_similar_project_names`
- `tests/test_loop.py::test_business_rules_flag_execution_project_with_closed_bid_status_as_conflict`

### 操作原则

发现疑似同项目别名时，不自动归一、不自动合并、不自动改目录。业务层只输出结构化复核样本，由后续人工确认或单独归档执行链路处理。后续优化应基于这些结构化样本提升业务层识别能力，而不是增加 skill 和 tools 之间的耦合。
