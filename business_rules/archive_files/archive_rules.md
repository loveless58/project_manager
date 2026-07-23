# 归档规则 v1（业务知识库种子）

> 来源：从 `business_rules/archive_decision.py` 反向抽取（2026-07-16）。
> 这是业务知识库的**种子**——后续真实归档数据反向抽取会追加历史案例。

## 1. 业务阶段定义

归档目标必须落在 4 个业务阶段之一：

| 阶段 | 含义 |
|---|---|
| 项目投标 | 投标进行中、待开标 |
| 项目弃标 | 主动放弃（我方弃标） |
| 项目丢标 | 投标失败（未中标/已丢标） |
| 项目执行 | 项目已中标、正在执行 |

## 2. PM 内部文档识别（硬约束）

文件名或文本含以下任一标记 → 判定为 PM 内部文档：

- 包含字符串 `project_manager`
- 关键词 markers：
  - `prd-project-manager`
  - `归档摘要报告`
  - `全量文件审计报告`
  - `项目总览`
  - `file organization`
  - `workspace config`
  - `ocr provider`

**PM 内部文档**：当前返回 `needs_redesign` 标记，不尝试归档到业务项目目录。

## 3. 业务阶段判断（路径 + 字段值映射）

按以下优先级判断 `archive_phase`：

### 3.1 路径前缀匹配（最高优先级）

| 路径含 | 阶段 |
|---|---|
| `项目丢标` | 项目丢标 |
| `项目执行` | 项目执行 |

### 3.2 字段值映射（次优先级）

| 来源字段 | 取值 | 阶段 |
|---|---|---|
| `fields.lifecycle_stage` | `closed`, `closed_lost` | 项目丢标 |
| `fields.lifecycle_stage` | `execution`, `executing`, `delivery` | 项目执行 |
| `fields.bid_status` | `弃标`, `已弃标`, `未中标`, `已丢标`, `丢标` | 项目丢标 |
| `business_judgement.business_stage` | `closed`, `closed_lost` | 项目丢标 |
| `business_judgement.business_stage` | `execution`, `executing`, `delivery` | 项目执行 |

### 3.3 默认

无匹配 → `项目投标`

## 4. 目标 Bucket（路径前缀）

| 路径含 | Bucket |
|---|---|
| `数据资产` 或 `数字资产` | `[数字资产, 导入资产]` |
| 否则 | `[原始文件]` |

最终归档路径：
```
<project_files_dir>/<archive_phase>/<项目名>/<bucket...>/<文件名>
```

## 5. Blockers 标记

| Blocker | 含义 |
|---|---|
| `unknown_project` | 项目名缺失 |
| `human_review_recommended` | business_judgement 提示需人核（非项目丢标、非 metadata_passthrough） |
| `pm_internal_archive_pending_redesign` | PM 内部文档归档 phase 待重新设计 |
| `target_exists` | 目标路径已存在 |
| `source_missing` | 源文件不存在 |

## 6. Confidence 评分

| 场景 | Confidence |
|---|---|
| PM 内部文档 | 0.0 |
| 有项目名（非丢标） | 0.45 |
| 项目丢标 + 有项目名 | 0.72 |
| 无项目名 | 0.0 |

## 7. Subject Type

| 场景 | subject_type |
|---|---|
| PM 内部文档 | `internal_project` |
| 业务项目（有 project_name） | `bid_project` |
| 无 project_name | `unknown` |

## 8. 默认值

| 字段 | 默认值 |
|---|---|
| `subject_name`（无 project_name） | `未命名项目` |
| `document_type`（业务项目无） | `未分类` |
| `document_type`（PM 内部） | `项目治理文档` |
| `_safe_name` 清理后为空 | `unknown` |

## 9. 文件名安全化（`_safe_name`）

正则替换 `<>:"/\|?*` 和空白字符 → `_`，首尾 `_` 去掉，结果为空时返回 `unknown`。

## 10. 待办（反向抽取后追加）

- [ ] 历史 archive_manifest.json 反向抽取 → 历史案例库
- [ ] PM 内部文档归档 phase 重新设计（当前返回 needs_redesign）
- [ ] LLM 通用能力兜底的具体 prompt 设计
