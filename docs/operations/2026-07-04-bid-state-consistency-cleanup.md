# 合成案例：投标状态一致性冲突复盘

> 数据声明：本文只使用虚构项目、逻辑 URI 和合成状态，不对应任何客户、人员、项目或本机目录。它保留历史缺陷的工程语义，作为规则设计与回归测试说明。

## 问题摘要

合成项目 `SyntheticProjectAlpha` 位于逻辑位置
`business://projects/lost/SyntheticProjectAlpha`。输入事实同时包含：

- `bid_status=已弃标`；
- `registration_status=待报名`；
- `business_judgement=closed / low risk`。

这组事实不可能作为同一项目的当前状态同时成立。系统虽然能根据业务阶段判定项目已经关闭，却遗漏了跨字段一致性检查。

## 业务不变量

投标状态按下列顺序推进：

```text
报名或参与决策
-> 投标与开标
-> 中标、丢标、弃标或执行
```

因此：

- `待报名` 表示尚未完成报名或参与决策；
- `已弃标` 表示已经决定不再参与；
- `已丢标/未中标` 表示参与后未成功。

当关闭状态与 `待报名` 同时出现时，系统必须输出高风险冲突并要求人工复核，不能默认为低风险关闭。

## 根因

### 1. 规则只判断单字段，没有验证组合状态

旧规则根据 `bid_status` 或 `lifecycle_stage` 推导 `closed`，却没有验证
`closed + registration_status=待报名` 的矛盾组合。

### 2. 业务位置上下文只覆盖了部分字段

逻辑位置 `business://projects/lost/...` 能确定关闭原因时，旧实现只更新
`bid_status`、`lifecycle_stage` 和 `closed_reason_type`，没有同步收敛
`registration_status`，导致历史输入再次污染当前事实。

### 3. 权威事实与派生投影可能不同步

事实更新后若没有重新生成 Markdown/HTML 投影，用户仍可能看到旧值。投影必须可由权威状态重建，不能独立成为事实源。

## 修复边界

规则层增加以下确定性检查：

```text
closed 或 bid_status 属于 已弃标/已丢标/未中标
+ registration_status=待报名
=> risk_level=high
=> human_review_required=true
=> data_quality_flags 增加状态冲突
```

位置上下文只在证据充分时收敛互相依赖的状态字段。无法确定客户、销售负责人或项目身份时保持未知，不从目录名猜测。

数据修复遵循以下原则：

1. 先备份并只读检查权威事实、冲突和业务判断；
2. 先写失败测试，再修改业务规则；
3. 只自动修复可由确定性证据证明的字段；
4. 重算业务判断并重建派生投影；
5. 复核全局统计与单项目状态一致；
6. 原始业务文件和运行产物留在仓库外的受控位置。

## 回归测试语义

- `test_business_rules_flag_closed_project_with_pending_registration_as_conflict`
  验证关闭项目与待报名状态会产生高风险复核项。
- `test_project_lost_directory_context_overrides_record_status_and_keeps_outputs_unique`
  验证丢标位置上下文会一致更新弃标状态、生命周期和关闭原因。
- 投影测试验证相同权威状态只生成一组一致、可重建的输出。

## 名称相似与状态冲突

另一个合成案例包含：

```text
business://projects/lost/SyntheticProjectAlpha
business://projects/executing/SyntheticProjectAlpha-Variant
```

名称相似只是信号，不等于身份已经确认。业务层应输出结构化复核案例，而不是自动合并目录、迁移账本或改写项目名：

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
    "reason": "名称相似但状态冲突；不自动合并或覆盖"
  }
}
```

对应回归覆盖：

- 保留项目原名且不自动合并别名；
- 账本不因名称相似自动合并项目；
- 执行阶段与关闭投标状态冲突时生成复核项。

任何文件移动、目录合并或账本迁移都属于独立归档执行边界，必须经过结构化动作、审批和回读。
