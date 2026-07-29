# 文档管理 Agent 三层架构

日期：2026-07-30  
状态：已确认架构，待实施计划与代码改造  
适用仓库：`loveless58/project_manager`

关联文档：

- [文档管理 Agent（第一阶段）PRD](../../prd/2026-07-30-document-management-agent.md)
- [历史 PRD：多智能体项目数据平台](../../prd/2026-07-30-historical-multi-agent-data-platform.md)
- [Claude for Financial Services](https://github.com/anthropics/financial-services)

## 1. 架构决策

项目采用 **命名的工作流 Agent + 按需披露的 Skill + 独立 Connector** 三层架构。

第一阶段只实现一个 `DocumentManagementAgent`。它不是若干 Python 函数的顺序调用包装，而是拥有明确目标、运行状态、Skill 选择、证据交付和人工门控的 Agent 工作流。

`document_parse`、`business_query`、`archive` 是该 Agent 的 Skill，不是独立 Agent。后续只有出现独立目标、上下文、权限和生命周期时，才新增命名 Agent。项目不复制金融业务或现在就实现多 Agent，而是借鉴 `financial-services` 的组织原则：命名 Agent 拥有端到端工作流，Skill 承载可复用领域方法，Connector 连接数据和确定性能力。

## 2. 三层边界

```text
用户 / Claude Code / 后续 UI
  -> DocumentManagementAgent
       -> Skill Catalog 中按需选择的 Skill
            -> Connector / Adapter / Storage / Database
```

### 2.1 Agent 工作流层

`DocumentManagementAgent` 负责：

- 接收用户目标与明确文件输入，创建并维护 `run_id`；
- 根据文件元数据、解析结果、风险和阶段选择下一个 Skill；
- 决定继续、返回 `needs_review` 或完成；
- 汇总事实、失败原因、证据和可读产物；
- 强制权限边界：第一阶段只读源文件，只写外部结果与 SQLite 审计；
- 使用 LLM 理解目标、选择 Skill、解释证据和表达不确定性。

Agent 不负责 PDF 渲染、OCR、SQL、路径映射、文件移动或 PageIndex 实现。这些均属于下层能力。LLM 不能绕过 Connector 直接写文件、数据库、长期记忆或执行归档。

### 2.2 Skill 领域能力层

Skill 是 Agent 按需加载的领域方法包。每个 Skill 必须声明触发条件、禁止触发条件、输入输出契约、Connector 依赖、副作用、失败语义、证据要求、审核要求和版本。

第一阶段 Skill Catalog：

| Skill | 职责 | 第一阶段状态 |
|---|---|---|
| `document_parse` | 能力探测、原生解析、OCR 兜底、标准化文档 | 启用 |
| `document_facts` | 类型识别、字段候选、电子发票字段与文本/页码证据 | 新增并启用 |
| `document_review` | `document.json`、`review.md`、运行摘要 | 新增并启用 |
| `business_query` | 基于业务事实形成候选与证据 | 接口保留；不做项目归属判断 |
| `archive` | 确认后的哈希、权限、目标与回读校验 | 接口保留；不执行真实归档 |

`document_facts` 与 `document_review` 不得藏在 Agent 私有函数中，否则项目会再次退化为扁平函数项目。

### 2.3 Connector / 数据能力层

Connector 是可替换的确定性适配器，只做 I/O、能力探测、格式转换或数据访问，不判断业务目标。

| Connector | 职责 | 第一阶段状态 |
|---|---|---|
| StorageBinding / DocumentStore | 逻辑 URI 与当前节点的只读源文件、外部结果根目录映射 | 启用 |
| Native Parser | DOCX、XLSX、文本和可取字 PDF 的读取 | 启用 |
| OCR Provider Chain | RapidOCR、MinerU、EasyOCR 的能力探测与有序调用 | 启用，以实际可用 Provider 为准 |
| Artifact Store | 创建不可覆盖的 run/document 目录并写 JSON/Markdown | 新增 |
| SQLite Repository | 保存文档、位置、运行、任务项和产物引用 | 启用并扩展 |
| PageIndex StructureIndex | 长文档章节、页码和局部上下文证据 | 接口保留，不参与第一阶段项目判断 |
| PostgreSQL Adapter | 中心化权威数据库 | 后续能力 |

## 3. 渐进式披露

渐进式披露同时约束上下文、数据读取和副作用权限。

### 3.1 上下文披露

Agent 启动时只加载核心工作流、安全策略、Skill Catalog、节点能力摘要、用户目标和输入文件的轻量元数据。不得一次性加载全部 Skill、完整原文、PageIndex、归档策略或业务知识。

```text
输入为 PDF
  -> 加载 document_parse 的 PDF 路径
  -> 原生文本不足时，加载 OCR Provider 策略
  -> 识别为电子发票时，加载 document_facts 的 invoice 子规则
  -> 生成交付物时，加载 document_review
  -> 后续显式启用项目判断时，才加载 business_query 与 PageIndex 证据策略
  -> 收到确认时，才加载 archive
```

### 3.2 数据披露

```text
L0：文件名、大小、MIME、逻辑 URI、哈希
L1：解析状态、文本长度、页数、解析器、类型候选
L2：字段候选、页码块、表格、局部文本证据
L3：完整文本与原始页，仅在审核或深度提取时读取
L4：项目数据库与 PageIndex，仅在后续项目判断阶段读取
```

`document.json` 是一次处理的事实快照和上下文压缩产物。后续 Agent 默认读取其摘要、字段和证据引用；仅在必要时回到全文或原始文件。

### 3.3 权限披露

```text
默认：读取明确源文件；写外部结果产物；写节点本地 SQLite 审计。
后续：人工确认后写已确认业务事实。
更高：显式确认后移动或改名文件。
```

第一阶段不授予项目权威事实写入、真实归档、外部系统提交、浏览器自动化或跨节点数据库写入权限。

## 4. 工作流状态机

```text
received
  -> scoped
  -> profiled
  -> parsed
  -> facts_extracted
  -> artifacts_published
  -> awaiting_review
  -> completed
```

失败不跳过产物：任何阶段失败都必须进入 `artifacts_published`，记录失败原因、已尝试能力和建议动作，随后进入 `awaiting_review` 或 `completed`。

归档只能在未来独立确认后扩展：

```text
awaiting_review -> confirmation_received -> archive_validated -> archived / archive_failed
```

第一阶段不得从 `awaiting_review` 自动进入归档状态。

## 5. 契约与产物

`contracts/` 是三层共享的版本化协议边界。跨层对象至少包含 `schema_version`、`run_id` 或 `document_id`、状态和可审计来源。

第一阶段核心契约：

| 契约 | 用途 |
|---|---|
| `structured_document.v1` | 解析结果：来源、哈希、文本、页、表、解析器、基础字段 |
| `document_result.v1` | `document.json`：事实、字段候选、证据、处理建议和产物元数据 |
| `agent_run.v1` | `run.json`：目标、输入、状态、产物清单与安全声明 |
| `artifact_reference.v1` | SQLite 中的逻辑 URI、哈希与产物版本引用 |

产物必须位于 Git 和源业务目录外：

```text
<results-root>/runs/<run-id>/
  run.json
  run-summary.md
  documents/<document-id>/document.json
  documents/<document-id>/review.md
```

`document.json` 是唯一事实来源；`review.md`、`run-summary.md` 与 SQLite 索引均从同次事实派生，不能维护相互冲突的业务版本。

## 6. 目录与迁移原则

目标目录边界如下；现有代码只按测试覆盖逐步迁移，禁止无关大搬家：

```text
agents/document_management/     # Agent 目标、状态机、Skill Catalog
skills/document_management/     # 领域 Skill 声明、子规则、测试夹具
connectors/storage/             # 逻辑 URI 与节点路径
connectors/parsing/             # 原生解析与 OCR 适配器
connectors/database/            # SQLite / 后续 PostgreSQL
connectors/artifacts/           # JSON、Markdown 与结果目录
connectors/pageindex/           # 结构索引适配器
contracts/                      # 跨层版本化对象
```

现有 `agents/file_organizer`、`skills/file_organizer`、`ocr/providers`、`platform_core/storage_bindings`、`infrastructure/file_organizer` 与 `integrations/pageindex` 是迁移来源。第一阶段允许兼容导入；目录和命名仅在有测试覆盖时调整。

## 7. 非目标与架构验收

第一阶段不实现旧版 `.doc`、项目归属判断效果、PageIndex 项目证据、PostgreSQL/群晖、多 Agent handoff、浏览器自动化、真实归档、向量 RAG 或自动长期记忆。

在进入第二阶段前必须证明：

1. Agent 不加载无关 Skill 也能处理明确文件；
2. 每个启用 Skill 的输入、输出、失败语义和 Connector 依赖可独立测试；
3. 扫描 PDF 与电子发票 PDF 均生成正式 JSON/Markdown 审核产物；
4. 用户不看代码、SQLite 或终端日志也能理解运行过程与结果；
5. SQLite 可追溯运行、文档、逻辑来源和产物引用；
6. 源文件不变，Agent 无法越过确认门归档；
7. 后续接入 PageIndex、PostgreSQL 或新 Agent 时无需改写基本工作流契约。
