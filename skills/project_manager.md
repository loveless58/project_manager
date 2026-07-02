---
name: project_manager
version: 3.3.0
description: |
  Project Manager Agent skill index. This file summarizes active skills and
  points to the runtime sources of truth.
---

# Project Manager Skill Index

## Disclosure Rule

The agent uses three levels of disclosure:

```text
Level 0: skill summaries
Level 1: active skill boundary document
Level 2: active ToolRegistry schemas
```

The runtime must not expose all tools by default. All-tools registration is a debug and governance validation path only.

## Active Skills

| Skill | Boundary document |
|---|---|
| `data_cleaning_file_organization` | `skills/data_cleaning_file_organization.md` |
| `project_management` | `skills/project_management.md` |
| `opportunity_management` | `skills/opportunity_management.md` |
| `cloudcc_crm` | `skills/cloudcc_crm.md` |

## Hard Rules

- One request routes to one active skill before tool schemas are exposed.
- Tool ownership comes from `main.SKILL_TOOL_MAP` and executable `ToolRegistry` registrations.
- Skill markdown files describe purpose and boundaries; they do not maintain complete parameter contracts.
- Runtime artifacts belong under `state/`, `logs/`, or caller-provided isolated directories, not under `skills/`.

## Source Of Truth

Use `ToolRegistry` for executable schema, `skill_policies/` for rule-mode fallback sequencing, and `governance/validate.py` for drift checks.
