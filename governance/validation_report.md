# Governance 校验报告

## 目录契约

校验脚本：`governance/validate.py dirs`

校验内容：

- `项目文件/` 三阶段目录是否存在：`项目投标/`、`项目执行/`、`项目归档/`
- `项目文件/index.json` 是否存在
- `数据清洗工作台/00-原始文件（待处理）` 是否存在
- `新机会与线索/招标公告/` 是否存在

## 工具 Schema 契约

校验脚本：`governance/validate.py tools`

校验内容：

- 实际注册的 34 个工具与 `governance/project_schema.json` 中声明的 34 个工具是否一致
- 是否存在多余工具：已注册但未写入 schema
- 是否存在缺失工具：schema 已声明但未注册
- 每个工具的 required 参数是否与注册表一致

## 当前关键边界

- 运行时采用渐进式披露：先由 skill router 选择 active skill，再只注册该 skill 的工具子集。
- 数据清洗及文件整理 skill 暴露 13 个工具，其中 `prepare_file_organization_run`、`apply_human_review`、`execute_archive_plan` 共同构成文件整理闭环。
- `execute_archive_plan` 在 `confirmed=false` 时必须返回 `needs_confirmation`，不得移动源文件。
- CloudCC/CRM 工具域默认使用受控边界，真实浏览器写入必须先经过登录态探针、草稿准备和提交前确认。

## 使用方式

### 手动校验

```bash
cd /path/to/project_manager
python governance/validate.py all
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 全部通过 |
| 1 | 存在 warning |
| 2 | 存在 error |

### 接入 CI

在 `.github/workflows/` 或其他 CI 中添加：

```yaml
- name: Governance check
  run: python governance/validate.py all
```

### 接入 git pre-commit hook

```bash
# .git/hooks/pre-commit
#!/bin/bash
cd "$(git rev-parse --show-toplevel)"
python governance/validate.py all
```

## 契约版本

| 契约文件 | 版本 | 字段 |
|---------|------|------|
| `directory_contract.json` | 1.0.0 | required_dirs, validation_rules |
| `project_schema.json` | 1.0.0 | tools (34), validation_rules |
| `validate.py` | 1.0.0 | dirs / tools / all |

变更契约时同步更新 `version` 字段、本报告和对应测试。
