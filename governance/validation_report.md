# Governance 校验报告

## 目录契约

校验脚本：`governance/validate.py dirs`

校验内容：
- `项目文件/` 三阶段目录是否存在（项目投标/项目执行/项目归档）
- `项目文件/index.json` 是否存在
- `数据清洗工作台/00-原始文件（待处理）` 是否存在
- `新机会与线索/招标公告/` 是否存在

## 工具 Schema 契约

校验脚本：`governance/validate.py tools`

校验内容：
- 实际注册的 20 个工具 vs schema 中的 20 个工具是否一致
- 是否有多余的工具（注册了但不在 schema 里）
- 是否有缺失的工具（schema 里有但没注册）

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
| 1 | 有 warning |
| 2 | 有 error |

### 接入 CI

在 `.github/workflows/` 或其他 CI 里添加：

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
| `project_schema.json` | 1.0.0 | tools (20), validation_rules |
| `validate.py` | 1.0.0 | dirs / tools / all |

变更契约时同步更新 `version` 字段 + 本文档。
