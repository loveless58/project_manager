---
name: project_manager
version: 3.2.0
description: |
  Project Manager Agent skill index. This file lists available skills and the
  progressive disclosure rule. Detailed tool contracts live in each skill file
  and in governance/project_schema.json.
---

# Project Manager Skill Index

## Disclosure Rule

The agent uses three levels of disclosure:

```text
Level 0: skill index
Level 1: active skill contract
Level 2: active skill tool schemas
```

The runtime must not expose all tools by default. All-tools registration is a
debug and governance validation mode only.

## Skills

| Skill | File | Current status |
|---|---|---|
| `data_cleaning_file_organization` | `skills/data_cleaning_file_organization.md` | active first loop |
| `project_management` | planned | existing tools, contract not split yet |
| `opportunity_management` | planned | existing tools, contract not split yet |
| `cloudcc_crm` | planned | existing tools, contract not split yet |

## First Loop

The first skill to keep working is:

```text
data_cleaning_file_organization
```

Expected runtime path:

```text
goal mentioning 项目总览 / 项目账本 / 数据清洗及文件整理
  -> route skill data_cleaning_file_organization
  -> expose only data-cleaning/file-organization tools
  -> call update_project_ledger
  -> produce state/project_ledgers/<project>/项目总览.md
```

## Tool Contract Source

Do not maintain a second complete tool list here. The authoritative schema is:

```text
governance/project_schema.json
```

## Runtime State

Skill files are contracts. Runtime artifacts go under:

```text
state/project_ledgers/
logs/
```
