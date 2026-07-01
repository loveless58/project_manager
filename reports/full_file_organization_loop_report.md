# 文件整理闭环真实文件模拟报告

- 生成时间：2026-07-02T00:51:53
- 原始文件数：2
- 副本文件数：2
- run_id：run_20260702_005153_342424
- 准备阶段状态：success，成功 2，失败 0
- 未确认归档状态：needs_confirmation
- 人工复核状态：success，复核 1，失败 0
- 归档执行状态：success，移动 2，失败 0
- HTML 更新状态：success，项目数 1

## 输入文件

- 原始：`C:\Users\Administrator\Downloads\wKgVgGo4jjSESFeFAAAAAC2oYG492.docx`
  副本：`E:\Dev\Projects\project_manager\state\simulation_inputs\run_20260702_005153\01_wKgVgGo4jjSESFeFAAAAAC2oYG492.docx`
- 原始：`C:\Users\Administrator\Downloads\投标文件-华胜.docx`
  副本：`E:\Dev\Projects\project_manager\state\simulation_inputs\run_20260702_005153\02_投标文件-华胜.docx`

## 关键产物

- prepare.input_manifest: `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\runs\run_20260702_005153_342424\input_manifest.json`
- prepare.review_queue: `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\runs\run_20260702_005153_342424\review_queue.json`
- prepare.planned_archive_actions: `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\runs\run_20260702_005153_342424\planned_archive_actions.json`
- prepare.run_report: `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\runs\run_20260702_005153_342424\run_report.md`
- prepare.trace: `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\runs\run_20260702_005153_342424\trace.json`
- prepare.run_dir: `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\runs\run_20260702_005153_342424`
- archive.archive_result: `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\runs\run_20260702_005153_342424\archive_result.json`
- archive.run_report: `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\runs\run_20260702_005153_342424\run_report.md`
- html: `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\投标进度总览.html`

## 准备阶段归档计划

- needs_review: `E:\Dev\Projects\project_manager\state\simulation_inputs\run_20260702_005153\01_wKgVgGo4jjSESFeFAAAAAC2oYG492.docx` -> `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\project_ledgers\打印刻录系统采购项目\source_files\打印刻录系统采购项目_采购公告_20260702.docx`; blockers=['human_review_recommended']
- needs_review: `E:\Dev\Projects\project_manager\state\simulation_inputs\run_20260702_005153\02_投标文件-华胜.docx` -> `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\project_ledgers\打印刻录系统采购项目\source_files\打印刻录系统采购项目_投标文件_20260702.docx`; blockers=['human_review_recommended']

## 复核与策略修正

- `打印刻录系统采购项目` 生成规则候选：status=pending_rule_approval, requires_test=True

## 归档执行结果

- success: `E:\Dev\Projects\project_manager\state\simulation_inputs\run_20260702_005153\01_wKgVgGo4jjSESFeFAAAAAC2oYG492.docx` -> `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\project_ledgers\打印刻录系统采购项目\source_files\打印刻录系统采购项目_采购公告_20260702.docx`
- success: `E:\Dev\Projects\project_manager\state\simulation_inputs\run_20260702_005153\02_投标文件-华胜.docx` -> `E:\Dev\Projects\project_manager\state\real_file_simulation_workspace\run_20260702_005153\project_ledgers\打印刻录系统采购项目\source_files\打印刻录系统采购项目_投标文件_20260702.docx`

## 结论

- 结构化输出、业务判断、复核队列、归档计划和运行报告均已落盘。
- 未确认时归档工具返回 `needs_confirmation`，源文件副本未被移动。
- 人工复核可以以 `human_correction` 写回项目账本，并生成待审批规则候选。
- 确认后执行归档会移动副本文件、写入归档记录，并可继续生成投标进度总览 HTML。
- 本模拟只移动仓库内忽略目录中的副本，不移动用户 Downloads 原件。

## 子 agent 复核摘要

- 独立复核确认：注册表、治理 schema、文档、测试和真实文件副本模拟基本同步，可以提交。
- 已处理复核意见：`human_review_recommended` 归档计划现在必须先执行 `apply_human_review`，否则即使 `confirmed=True` 也会阻断移动。
- 已处理复核意见：模拟脚本默认使用带时间戳的 workspace 和 input-dir，降低复跑时 `target_exists` 的概率。
- 保留治理边界：人工复核当前会写回项目账本并生成 `rule_candidate.v1`，不会自动修改规则引擎；规则入库仍需后续审批和回归测试。
