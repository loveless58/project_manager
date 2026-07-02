# Architecture: Progressive Project Manager Agent

## Core Shape

The agent is intentionally small at the control-plane level:

```text
User goal
  -> SkillRouter
  -> active Skill
  -> active ToolRegistry
  -> one LoopEngine
  -> structured artifacts
```

There is one `LoopEngine`. New skills do not create new loop engines.

## Three Disclosure Levels

### Level 0: Skill Index

The agent first sees skill summaries only. This keeps routing simple and avoids
overloading the prompt with unrelated tools.

Current skill index:

```text
data_cleaning_file_organization
project_management
opportunity_management
cloudcc_crm
```

### Level 1: Skill Contract

After routing, the agent loads the selected skill contract:

```text
skills/<skill>.md
```

The first active contract is:

```text
skills/data_cleaning_file_organization.md
```

### Level 2: Tool Schemas

Only tools owned by the active skill are registered into the runtime loop.

Example for `data_cleaning_file_organization`:

```text
scan_raw_files
extract_pdf
extract_document
run_ocr
classify_document
batch_process
save_structured
process_documents_to_ledger
prepare_file_organization_run
apply_human_review
execute_archive_plan
import_project_detail_workbook
generate_bid_progress_html
update_project_ledger
```

The full 35-tool registry still exists for governance validation:

```text
main._build_registry()
```

Normal runtime should use:

```text
main._build_registry_for_skill(skill_name)
```

## First Loop Contract

The first loop to keep stable is data cleaning and file organization:

```text
candidate facts + evidence
  -> update_project_ledger
  -> ProjectLedger
  -> state/project_ledgers/<project>/项目总览.md
  -> state/project_ledgers/<project>/project_ledger.json
  -> state/project_ledgers/<project>/decision_log.jsonl
```

This is the minimum usable product for the project ledger architecture.

## Observation Evaluation Contract

Every tool round produces both raw evidence and a planner-facing evaluation:

```text
Observation: <raw tool output>
Observation Evaluation: {
  "schema_version": "loop.observation_evaluation.v1",
  "status": "success | partial | blocked | failed | needs_confirmation",
  "error_code": "...",
  "retryable": true | false,
  "needs_confirmation": true | false,
  "next_actions": [...],
  "summary": "..."
}
```

`LoopEngine` stores the same evaluation on `LoopRound.observation_evaluation`
and derives `round.status` from it. The next planner round must consume the
evaluation before making another action decision. `needs_confirmation` is a hard
human gate; it is not a retryable tool failure. `blocked` means evidence is
insufficient or a capability is unavailable; it must not be reinterpreted as a
successful negative result.

## Boundaries

- Skill files define business capability contracts.
- Tools execute bounded actions.
- `ProjectLedger` manages project facts, evidence, conflicts, and decision logs.
- `LoopEngine` owns retry, budget, trace, dedup, stop conditions, and observation evaluation.
- LLM output can recommend or classify, but cannot directly overwrite ledger facts.
- External-system writes, including CloudCC/CRM, must remain gated.

## Source Of Truth

| Concern | File |
|---|---|
| Disclosure and runtime contract | `agent.md` |
| Skill contract | `skills/<skill>.md` |
| Tool parameter schema | `governance/project_schema.json` |
| Loop behavior | `common/loop_engine.py` |
| Project ledger behavior | `ledger/project_ledger.py` |
