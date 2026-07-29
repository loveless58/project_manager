# 文件整理 Agent 设计

日期：2026-07-29  
状态：待实现评审  
适用范围：第一个可用业务 Agent；不重构为通用多 Agent 平台。

## 1. 产品目标

用户在 Claude Code、Codex 或同类 Agent Host 中显式选择一小批业务文件后，文件整理 Agent 生成可读、可修改的整理建议；只有用户确认的条目才会执行归档。

首期验收闭环：

```text
选择 3–10 个文件
→ 原生解析或按需 OCR
→ 查询项目与文件历史
→ 输出整理建议表和证据
→ 用户确认部分或全部条目
→ 仅执行已确认归档动作
→ 回读并记录位置历史
```

## 2. 边界

### 2.1 Agent 与 Host

只有一个业务 Agent：`file-organizer`。Claude Code/Codex 是运行该 Agent 的主 Agent Host；仓库不再把 `LoopEngine`、Agent Gateway、响应交换目录或多个内部 Agent 作为本工作流前置条件。

`file-organizer` 负责：理解整理目标、调用 Skill、查询共享业务层、生成建议、向用户请求归档确认并汇总结果。

### 2.2 Skill

Agent 调用四个 Skill：

1. `document-parse`：DOCX、XLSX、文本 PDF、Markdown、XML 的原生解析。
2. `ocr`：仅在原生解析不可用或文件是扫描 PDF/图片时调用。
3. `business-query`：查询 SQLite 中的项目、文件、位置和已确认的关联；对需要章节证据的长文档查询 PageIndex。
4. `archive`：只执行用户确认的具体动作；执行前校验，执行后回读并写入位置历史。

Skill 不是独立 Agent。OCR、PageIndex、数据库、文件系统均不得要求用户理解其内部运行方式。

### 2.3 共享业务逻辑层

SQLite 是当前节点的权威业务存储。首期仅建立以下领域数据：

- `projects`：项目 ID、名称、可选项目编号、状态；
- `documents`：稳定文件 ID、内容哈希、类型、解析状态；
- `document_locations`：文件的逻辑 URI、当前/历史位置与归档状态；
- `document_project_links`：文件与项目的候选或人工确认关联、依据和置信度；
- `organization_runs` 与 `organization_items`：一次整理任务的建议、确认与执行结果。

PageIndex 是可重建的长文档结构索引，不是权威数据源。数据库先过滤候选项目和文件；PageIndex 只用于返回章节、页码和局部证据。

## 3. 文档处理策略

```text
DOCX / XLSX / Markdown / XML / 文本型 PDF
→ document-parse

扫描 PDF / 图片 / 原生解析无有效文本
→ OCR Provider Chain
```

统一输出 `structured_document.v1`，至少包含：稳定文件引用、内容哈希、媒体类型、解析状态、正文片段、表格/页面结果、基础字段、证据和 Provider 信息。

单个文件失败必须生成该文件的 `needs_review` 结果，不得阻塞同批其他文件。

## 4. OCR Provider Chain

OCR 是声明式可选能力。仓库提供 Provider 实现与依赖声明；实际节点通过安装对应 extras/requirements 并在本地配置中启用。业务文件、模型、节点私有路径和密钥不提交到仓库。

优先级固定如下：

```text
原生解析优先
→ RapidOCR
→ MinerU
→ EasyOCR
→ 待人工处理
```

- RapidOCR：快速中文/英文图片与扫描 PDF 文字提取，作为首选 OCR Provider；
- MinerU：在 RapidOCR 不可用、失败，或任务需要其版面/Markdown 结构化结果时调用；
- EasyOCR：本机兼容回退 Provider；
- 所有 Provider 都必须返回统一 `structured_document.v1` 所需信息或标准 blocked/failed 状态；
- Provider 缺失、模型缺失、网络下载失败或解析失败只能使当前文件进入待处理，不能触发隐式安装、隐式模型下载或隐式回退到未声明 Provider。

## 5. PageIndex 策略

PageIndex 作为 `business-query` 的可选依赖接入，不作为导入或归档的前置条件。

触发条件：文件为 PDF 或 Markdown、原生/OCR 文本可用、长度超过配置阈值，且 Agent 需要章节级证据或用户发起长文档查询。

PageIndex 不可用时：保留结构索引状态为 `unavailable`，Agent 继续依据已解析文本和 SQLite 返回建议，并明确缺少章节级证据。

运行时必须由节点本地配置显式提供 PageIndex 安装目录、解释器与上游模型配置；仓库只提交适配器、配置模板和验证命令。

## 6. Hook

Hook 是确定性事件处理，不是 Agent：

1. 解析成功后：登记或更新 `documents`，写当前 `document_locations`；
2. 符合 PageIndex 条件时：尝试建立/更新索引并记录索引引用或不可用状态；
3. 用户确认归档前：校验源哈希、目标目录、重名和权限；
4. 归档成功后：回读目标文件，更新 `document_locations` 的当前位置和历史记录。

Hook 不得自行选择项目、自动归档、覆盖或删除文件。

## 7. 用户可见输出与确认

Agent 输出一张建议表：

| 文件 | 类型 | 候选项目 | 建议目录 | 依据 | 状态 |
|---|---|---|---|---|---|
| `example.docx` | 合同 | `项目 A` | `项目 A/合同文件` | 合同号与项目库匹配 | 待确认 |

用户可对每条选择：`确认`、`修改目标`、`跳过`、`待人工处理`。

只有 `确认` 的条目进入 `archive` Skill；默认操作是移动，首期禁止覆盖与删除。任何不确定项目、缺少目标目录规则、解析失败或 OCR 低质量文件均进入待人工处理。

## 8. 非目标

首期不包含：

- 自动扫描整个 SynologyDrive；
- 强制 PostgreSQL、群晖远程执行、跨节点调度；
- 浏览器自动化、CRM/BPM 写入；
- 多 Agent 相互 handoff；
- 自动创建或自动更新项目；
- 自动覆盖、删除或未经确认的移动；
- 对所有文件自动 OCR 或自动 PageIndex。

## 9. 验收标准

在一个节点本地运行时，使用 3–10 个明确选择的真实业务文件验证：

1. DOCX、XLSX 与文本 PDF 至少能完成原生解析；
2. 扫描 PDF/图片在启用 Provider 后按 RapidOCR、MinerU、EasyOCR 顺序尝试；Provider 缺失时稳定标记待处理；
3. Agent 输出每个文件的整理建议或待人工原因，不因单文件失败终止整批；
4. 已确认动作仅移动对应文件；未确认文件内容与位置不变；
5. 归档成功后可从 SQLite 查询文件的新逻辑位置和历史位置；
6. PageIndex 可用时为长文档返回章节/页码证据；不可用时不阻塞上述 1–5 项。
