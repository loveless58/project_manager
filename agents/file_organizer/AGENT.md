# File Organizer Agent

在 Claude Code 或 Codex 中，先让宿主调用 `FileOrganizerAgent.prepare(explicit_files, goal=...)`，展示每个条目的候选项目、建议归档位置、解析/证据原因和 `needs_confirmation` 状态。此步骤绝不移动文件。

只有用户给出明确的条目确认后，才调用 `execute_confirmed(run_id, confirmations)`。确认可以是 `confirmed`、`modified_target` 或 `skipped`；不得根据文件名、物理路径或模型推断隐式确认。

不要调用旧的 LoopEngine、Agent Gateway 或 `prepare_business_file_run.py` 来替代此工作流。OCR、PageIndex 和数据库是本 Agent 的可调用 Skill/业务层，不是独立 Agent；PageIndex 不可用时保留建议并要求复核。
