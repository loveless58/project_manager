# File Organization L3 Loop

本文档记录 `data_cleaning_file_organization` loop package 当前 L2/L3 收口状态，用于代码审查、提交拆分和后续阶段开发。当前设计目标是：真实文件整理和归档必须经过可审计 run package、反驳验证、审计复核、人工反馈和执行 gate；除明确的 `execute_archive_plan(..., confirmed=True)` 外，其余步骤均不移动文件。

## 执行链路

标准文件整理链路：

```text
prepare_file_organization_run
  -> verify_file_organization_run
  -> audit_file_organization_run
  -> prepare_feedback_form
  -> apply_feedback_form
  -> apply_feedback_decisions
  -> generate_candidate_tests
  -> execute_archive_plan
```

当前 `skill_policies.data_cleaning_file_organization` 的自动 planner 默认只推进到 `audit_file_organization_run` 后停下，由人审查反馈表和 gate 结果。真实归档仍必须显式调用 `execute_archive_plan(run_id, confirmed=True)`。

## Artifact 流

`prepare_file_organization_run` 写入 run 包：

- `input_manifest.json`
- `review_queue.json`
- `planned_archive_actions.json`
- `trace.json`
- `run_report.md`

`verify_file_organization_run` 写入：

- `adversarial_verification.json`

`audit_file_organization_run` 写入：

- `audit_review.json`

`prepare_feedback_form` 写入：

- `feedback_form.json`
- `feedback_form.md`

`apply_feedback_form` 读取已填写的 `feedback_form.json`，转换为标准反馈，并委托 `apply_feedback_decisions`。

`apply_feedback_decisions` 写入：

- `human_feedback_decisions.json`
- `feedback_events.jsonl`
- `rule_candidates.json`
- `parser_test_candidates.json`
- 回写 `review_queue.json` 中匹配 item 的 `feedback_status`

`generate_candidate_tests` 写入：

- `generated_tests/parser_candidate_tests.py`
- `generated_tests/rule_candidate_tests.py`
- `generated_tests/test_manifest.json`

`execute_archive_plan` 在任何文件移动前写入：

- `archive_execution_gate.json`

只有 gate 通过后才会写：
- `archive_result.json`
- 更新 `run_report.md`

## 权限边界

只读或 artifact-only 步骤：

- `prepare_file_organization_run`：读取源文件并生成 run package；不移动文件。
- `verify_file_organization_run`：只读验证；不移动文件、不写业务账本。
- `audit_file_organization_run`：只读审计；不移动文件、不写业务账本。
- `prepare_feedback_form`：生成反馈表；不移动文件、不写业务账本。
- `apply_feedback_form`：应用反馈表到反馈 artifact；不移动文件、不写业务账本。
- `apply_feedback_decisions`：写反馈 artifact 和候选规则/测试；不移动文件、不写业务账本。
- `generate_candidate_tests`：写 run 包内测试草案；不修改仓库正式 `tests/`。

会产生真实外部副作用的步骤：

- `execute_archive_plan(run_id, confirmed=True)`：在 `archive_execution_gate.json` 通过后才移动文件，并写项目总览.md。

阻断条件：

- `confirmed` 不是 `True`。
- `audit_review.json` 要求的 `required_feedback_items` 仍未在 `review_queue.json` 中标记为 `feedback_received`。
- `planned_archive_actions.json` 中存在硬 blocker，例如 `source_missing`、`target_exists`、`unknown_project`。
- 源文件不可读或目标目录不可写。

## 反馈表工作流

准备反馈表：

```powershell
python -X utf8 -B scripts\review_file_organization_run.py --workspace <workspace> --run-id <run_id> --prepare-only
```

人工编辑：

- 打开 `runs/<run_id>/feedback_form.md` 查看问题。
- 在 `runs/<run_id>/feedback_form.json` 中填写每个 item 的 `response.decision`。
- 留空 `response.decision` 表示该项继续 pending。

应用反馈表：

```powershell
python -X utf8 -B scripts\review_file_organization_run.py --workspace <workspace> --run-id <run_id> --apply
```

默认应用后会调用 `generate_candidate_tests` 生成候选测试草案。若只想应用反馈，不生成测试草案，可加 `--no-generate-tests`。

## 提交分组

建议把当前变更拆成以下审查组：

1. Agent role 基础设施：`agents/`、`business_rules/adversarial_verification.py`、agent role tests。
2. 标准合同：`contracts/feedback_schema.py`、`contracts/review_queue_schema.py`、`contracts/archive_gate_schema.py`、`contracts/test_candidate_schema.py`、`contracts/feedback_form_schema.py`。
3. 工具接入：`tools/data_cleaning_tools.py` 中 verify/audit/feedback/gate/candidate-test/form workflow。
4. Runtime 声明：`main.py`、`loop_packages/data_cleaning_file_organization/manifest.json`、validators。
5. Policy 边界：`skill_policies/data_cleaning_file_organization.py` 中 prepare -> verify -> audit 后停在人审。
6. 回归测试：`tests/test_agent_roles.py`、`tests/test_review_queue.py`、`tests/test_feedback_loop.py`、`tests/test_archive_execution_gate.py`、`tests/test_candidate_test_generation.py`、`tests/test_feedback_form.py`。
7. 文档：本文件和后续 operator runbook。

不要把无关未跟踪脚本混进 L2/L3 提交，除非它被明确纳入本轮审查范围。

## 当前缺口

- 候选测试草案尚未自动晋升到仓库正式测试。
- 规则包仍有硬编码字段和关键词，需要后续迁到 rule pack。
- LLM 还没有进入核心业务判断，只能在后续作为只读 semantic reviewer 接入。
- 反馈表当前是 JSON/Markdown 工作流，还不是完整 UI。
