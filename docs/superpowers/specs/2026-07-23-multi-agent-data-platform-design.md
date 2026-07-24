# Project Manager 多智能体数据平台设计

日期：2026-07-23
状态：已确认设计，待书面规格复核
适用仓库：`loveless58/project_manager`

## 1. 背景与目标

当前项目以一个 ReAct 风格 `LoopEngine` 为控制平面，通过 active skill 和受限 `ToolRegistry` 执行项目管理、文件整理、商机管理和 CloudCC/CRM 任务。现有实现已经具备结构化 observation、确认门、文件账本、OCR Provider、`document_parse.v1`、PageIndex 客户端和部分审计能力，但仍存在以下结构性问题：

- 运行时本质上是单 Agent、单 LoopEngine，多 Agent 角色尚未形成独立权限和结构化 handoff；
- `project_ledger.json`、Markdown 和 JSONL 文件承担权威业务状态，缺乏事务、并发控制和统一查询；
- 文件路径、SynologyDrive、PageIndex、本机模型端点、业务目录和置信度策略存在不同程度硬编码；
- 文档身份与物理路径耦合，文件移动、改名、重复副本和云盘占位会破坏引用稳定性；
- 文档处理容易以文件格式或 OCR 为中心，没有按业务意图和内容能力选择最小处理路径；
- PageIndex 已有客户端封装，但当前外部依赖和运行路径带有平台假设，不能视为所有执行环境都具备的固定能力；
- 仓库仍跟踪历史运行日志，并在测试和文档中保留真实或类真实路径，业务数据与源码边界不够清晰；
- 原设计把首期部署限制为单机 Windows 和 SQLite，无法覆盖单机 macOS、群晖中心数据库以及异地执行节点。

本设计建立一个可渐进迁移的多智能体项目与办公助手架构。系统核心不依赖 Windows、macOS、盘符、固定 NAS 路径或单一数据库部署方式，并正式支持两类运行配置：

1. Windows 或 macOS 单机配置：本地 SQLite、本地调度和本机可用工具；
2. 中心化配置：群晖 PostgreSQL 17、中心控制平面以及一个或多个 Windows/macOS 异地执行节点。

群晖侧目标安装包标识按用户提供的 `PostgreSQL 17.19-4` 记录。该标识不是 PostgreSQL 官方容器镜像标签，也不直接作为 SQL 特性判断依据。部署验收以数据库报告的实际主版本为 PostgreSQL 17 为准。

核心目标：

1. 按端到端业务职责和写权限划分 Agent，而不是按 OCR、SQL、PageIndex 等技术组件划分 Agent。
2. 以 SQL 数据库作为项目事实、审核、审批、任务和 handoff 的权威源。
3. 将文件业务身份与物理位置分离，支持不同操作系统路径、移动、改名、重复副本和未知归档位置。
4. 对大量文件采用元数据先行、分层处理、按需深度解析的策略。
5. 使用 SQL 元数据过滤、SQL 全文检索和 PageIndex 长文档结构检索构成首期 RAG，不引入独立向量数据库。
6. 通过稳定接口和显式适配器注册替换 DocumentStore、PageIndex、OCR、数据库和投影格式，避免基础设施写死。
7. 将智能体业务职责与执行机器的技术能力分离，支持按能力、数据位置和信任等级调度任务。
8. 对权威事实写入使用事务，对文件移动和外部写操作使用审批、回读和对账，禁止盲目重试。
9. 让所有派生索引和可读视图均可从数据库、原始文件和版本化策略重建。

## 2. 非目标

首个系统级实施周期不包含：

- 完整的多租户权限管理和面向终端用户的管理界面；
- 把 PostgreSQL 端口直接暴露到公网；
- 让异地执行节点持有数据库表级写权限；
- 独立向量数据库或通用 Embedding 服务；
- 对整个文件存储进行全量 OCR、LLM 提取或 PageIndex；
- 自动删除、自动覆盖或未经批准的文件移动；
- 未经批准的 CRM/BPM/浏览器提交；
- 将所有确定性组件改造成可加载任意代码的通用插件平台；
- 让模型自由写入权威事实或长期记忆；
- 让 JSON、Markdown、PageIndex 或全文索引成为不可替代的权威状态源；
- 首期同时维护 PostgreSQL、MariaDB、MySQL 等多个服务型数据库实现；
- 把 SQLite 数据库文件放到 SynologyDrive 或网络共享中供多台机器共同打开。

浏览器自动化 Agent 属于后续接入范围，但本设计提前定义其审批、节点绑定、凭据和副作用边界。

## 3. 已选方案与替代方案

### 3.1 已选方案：部署中立核心 + 两种运行配置

核心业务只依赖稳定接口，部署配置选择具体适配器。Agent 按独立业务目标、上下文、权限和生命周期划分；确定性能力作为领域服务或工具。

| 逻辑职责 | 稳定接口 | 单机配置 | 中心化配置 |
|---|---|---|---|
| 权威数据存储 | Repository / Unit of Work | SQLite | PostgreSQL 17 |
| 数据库全文检索 | `FullTextRetriever` | SQLite FTS5 | PostgreSQL `tsvector` / GIN |
| 原始文档存储 | `DocumentStore` | 本地、挂载盘或同步目录 | 群晖文件服务或其他已注册存储 |
| 长文档结构索引 | `StructureIndex` | 本机可用 Provider | 由具备能力的执行节点提供 PageIndex |
| 文档解析 | `DocumentParser` | 本机 Provider | 节点能力注册表选择 Provider |
| OCR | `OcrProvider` | 本机 Provider Chain | 节点能力注册表选择 Provider Chain |
| 派生视图 | `ProjectionWriter` | JSON、Markdown、HTML | JSON、Markdown、HTML 或其他适配器 |
| 任务调度 | `JobScheduler` | 本地持久化队列 | 中心租约调度和节点心跳 |
| 语义检索 | `SemanticRetriever` | 首期关闭 | 首期关闭 |

中心化生产目标收敛到 PostgreSQL 17，不在首期实现 MariaDB/MySQL。Repository 边界仍然保留，用于隔离业务领域与数据库方言，而不是承诺无成本切换数据库。

### 3.2 未选方案：所有执行节点直连 PostgreSQL

让 Windows/macOS 执行节点直接读写群晖 PostgreSQL 看似简单，但会扩散数据库凭据、表结构和事务规则，增加跨版本兼容、网络暴露和并发写入风险，因此不采用。

中心化配置中，执行节点只通过控制平面 API 领取任务、发送心跳和提交产物。只有控制平面及受控运维连接可以访问 PostgreSQL。

### 3.3 未选方案：强制所有部署使用 PostgreSQL

单机离线工作若也强制启动 PostgreSQL，会增加安装、资源和维护成本。SQLite 继续作为单机嵌入式实现，但其数据库文件只能由单机进程访问，不能通过文件同步形成伪分布式部署。

### 3.4 未选方案：完全动态插件平台

允许通过字符串动态加载任意 Provider 会增加安全、调试和配置复杂度。首期只定义有限接口、能力声明和显式注册表，不建设任意代码加载平台。

## 4. 总体架构

### 4.1 逻辑架构

```text
用户 / Claude Code / 后台触发器
  -> 办公/项目管理主助手
       -> 文件与文档分析 Agent（只读业务分析）
       -> 项目账本 Agent（权威事实唯一业务写入口）
       -> 归档执行 Agent（受批准约束的文件写入）
       -> 浏览器自动化 Agent（后续接入）

控制平面
  -> Agent Orchestrator / Handoff Router
  -> Repository / Unit of Work
  -> JobScheduler / NodeRegistry
  -> Approval / Review / Reconciliation
  -> RetrievalService / ProjectionService

执行平面
  -> Windows 执行节点
  -> macOS 执行节点
  -> 其他受信任执行节点
  -> 每个节点只声明实际可用的解析、OCR、PageIndex、浏览器和存储能力

持久化与外部系统
  -> SQLite：单机配置的权威数据库
  -> 群晖 PostgreSQL 17：中心化配置的权威数据库
  -> DocumentStore：原始文件和大型产物
  -> SQL FTS / PageIndex：可重建索引
  -> JSON / Markdown / HTML：可重建投影
```

### 4.2 中心化数据流

```text
主助手创建业务目标
  -> 控制平面在 PostgreSQL 创建 agent_run / processing_job
  -> JobScheduler 根据 required_capabilities、data_affinity 和节点状态分配租约
  -> 异地执行节点通过 API 领取任务
  -> 节点通过自己的 storage_binding 定位输入
  -> 节点运行解析、OCR、PageIndex、清洗或浏览器工具
  -> 节点上传产物引用、内容哈希、状态和错误码
  -> 控制平面校验幂等键与输入版本
  -> 控制平面事务写入 PostgreSQL
  -> 项目账本 Agent 审核候选事实并触发投影刷新
```

智能体是业务职责边界，执行节点是技术能力和运行位置。一个文档分析 Agent 可以把不同子任务调度给不同节点；一个节点也可以服务多个 Agent，但不能因此获得这些 Agent 的全部业务权限。

## 5. Agent 职责与权限

### 5.1 办公/项目管理主助手

负责接收自然语言目标、路由任务、创建运行记录、调度专业 Agent、汇总结果、展示审核项，并在复核和审批边界暂停。它不直接移动文件、写权威事实、修改 CRM/BPM 或直接访问数据库表。

### 5.2 文件与文档分析 Agent

负责文件发现请求、轻量登记、能力探测、处理计划、按需解析、文档分类、项目匹配、重复识别、候选事实、证据、复核项和逻辑归档意图。

它只读原始业务文件，可以写运行记录、候选事实和派生索引，但不能修改权威事实、移动或删除文件、猜测未知归档目标或写 CRM/BPM。

主要交付：

- `document_analysis_handoff.v1`
- `candidate_fact_batch.v1`
- `archive_intent.v1`
- `review_queue.v1`

### 5.3 项目账本 Agent

负责审核候选事实和证据、检测冲突、执行字段风险规则、在事务中写权威事实、保留历史、管理项目权威语义记忆并触发投影刷新。

它是权威项目事实唯一业务写入口，不能移动文件、修改解析产物、自行批准高风险事实、把模型推理直接写成权威事实或执行外部写入。

### 5.4 归档执行 Agent

负责验证具体归档动作的审批、哈希、权限、目标范围和重名风险，选择具备目标存储写权限的执行节点，执行已批准移动或改名，回读结果并更新位置历史。

它只能执行批准过的 `action_id`，不能自行选择项目、文档类别或归档目标，不能扩大审批范围；首期默认禁止删除和覆盖。

### 5.5 浏览器自动化 Agent

后续阶段加入，负责外部系统读取、下载、草稿填写、提交和回读。读取/草稿与提交能力具有独立权限边界，提交需要明确审批、幂等和回读保护。

浏览器登录态属于节点本地受保护能力，不上传 Cookie、令牌或浏览器配置到 PostgreSQL。任务通过 `preferred_node_id` 或能力标签路由到保存对应登录态的执行节点。

### 5.6 非 Agent 组件

AssetCatalog、DocumentIdentityResolver、ContentProfiler、ProcessingPlanner、DocumentParser、OcrProviderRegistry、StructureIndex、FullTextRetriever、DocumentStore、Repository、ProjectionWriter、ArchivePolicyResolver、JobScheduler、NodeRegistry 以及 Schema/Approval/Hash/Permission Validator 是确定性服务或适配器，不创建独立 Agent。

当前审计组件中可以确定性实现的检查迁入治理服务；只有语义证据复核在后续有明确价值时才升级为独立只读审核 Agent。

## 6. 文档身份、逻辑位置与节点路径映射

物理路径不是文档身份。系统区分：

- `content_object`：二进制内容，以哈希标识；
- `document`：业务文档身份；
- `document_version`：业务文档的内容版本；
- `document_location`：某一版本的逻辑位置及位置历史；
- `asset_observation`：某个执行节点在某次扫描中观察到的文件状态；
- `storage_binding`：逻辑存储前缀到节点本地路径的映射。

### 6.1 `content_objects`

关键字段：`content_object_id`、`sha256`、`size_bytes`、`media_type`、`first_seen_at`。

相同哈希可以复用只依赖二进制内容的原生文本、OCR 和结构索引结果，但不能自动合并不同项目上下文中的业务文档，也不能直接复用项目匹配、候选事实、风险判断或归档意图。后面这些业务产物必须结合当前 document、project、策略版本和处理意图重新评估。系统区分二进制重复和业务重复。

### 6.2 `documents`

关键字段：`document_id`、`project_id`、`document_type`、`logical_title`、`business_status`、`created_at`。路径、文件名、操作系统或存储提供者变化不改变 `document_id`。

### 6.3 `document_versions`

关键字段：`document_version_id`、`document_id`、`content_object_id`、`version_number`、`version_status`、`supersedes_version_id`、`created_at`。新内容创建新版本，不原地覆盖旧版本。

### 6.4 `document_locations`

关键字段：`location_id`、`document_version_id`、`storage_provider`、`object_key`、`logical_uri`、`location_role`、`availability`、`is_current`、`first_seen_at`、`last_seen_at`。

位置角色包括 `source`、`working_copy`、`archived`、`duplicate`、`cache`、`temporary`、`unknown`；可用性包括 `available`、`placeholder`、`unreadable`、`missing`、`moved`、`deleted`、`unknown`。

### 6.5 `storage_bindings`

关键字段：`storage_binding_id`、`storage_provider`、`logical_prefix`、`node_id`、`local_mount_path`、`access_mode`、`availability`、`last_verified_at`。

同一逻辑位置可以在不同节点映射为不同物理路径，例如：

```text
business://project-files
  -> Windows-01: ${WINDOWS_BUSINESS_MOUNT}
  -> Mac-01: ${MACOS_BUSINESS_MOUNT}
  -> NAS-Worker: ${NAS_BUSINESS_MOUNT}
```

`local_mount_path` 是节点本地配置或受控登记数据，不是文档身份、数据库唯一键或审批主体。数据库和 handoff 传递稳定 ID、逻辑 URI 和内容哈希；只有对应节点上的 DocumentStore 适配器在执行时解析物理路径。

## 7. 大量文件的分层处理

默认策略是首次发现只做轻量登记和能力探测。只有被具体任务命中、被业务规则标记为高优先级或被后台计划明确选中时，才进行完整解析、OCR、LLM 提取和 PageIndex。

### 7.1 L0：轻量资产登记

登记文件名、扩展名、大小、修改时间、存储提供者、逻辑位置、观察节点、云盘就绪状态、快速指纹、首次和最近观察时间。文件量大时不立即计算完整 SHA-256。

### 7.2 L1：内容能力探测

检测 MIME、容器完整性、PDF 文本层、扫描属性、加密状态、工作表、图片尺寸、sidecar 和文件就绪情况，输出 `ContentProfile`。

### 7.3 L2：标准解析

按内容类型选择 DOCX、XLSX、CSV、JSON/XML、原生 PDF、图片/扫描 PDF、邮件和压缩包清单等处理器。

### 7.4 L3：业务深度提取

只有任务需要时才执行文档分类、项目匹配、字段抽取、风险识别、候选事实、证据定位和 LLM 结构化提取。

### 7.5 L4：结构索引

只有较长、有可用文本、具有章节结构价值且确有查询需求的文档进入 PageIndex。Excel、CSV、短通知、图片票据、压缩包和重复文件不默认进入 PageIndex。

### 7.6 ProcessingPlanner

Agent 声明业务意图，ProcessingPlanner 根据 ContentProfile、可用执行节点、Provider 能力和版本化策略生成确定性处理计划。计划必须说明选择理由、跳过理由、所需能力、数据位置偏好、策略版本、预期产物和复核要求。

OCR 只是 `extract_text` 的一种实现。文本型 PDF、DOCX、XLSX、CSV、JSON/XML 和网页 DOM 不默认使用 OCR。处理器失败也不能随意切换到语义不同的策略。

## 8. 执行节点与能力调度

### 8.1 `execution_nodes`

关键字段：`node_id`、`node_name`、`platform`、`architecture`、`agent_version`、`trust_level`、`network_zone`、`status`、`max_concurrency`、`last_heartbeat_at`。

节点状态包括 `registering`、`online`、`draining`、`offline`、`disabled`。节点离线不改变业务任务语义，只影响任务是否可以继续执行或重新调度。

### 8.2 `node_capabilities`

能力记录至少包含 `capability_name`、`provider`、`provider_version`、`configuration_fingerprint`、`status`、`last_verified_at`。

典型能力包括：

- `native_pdf_parse`
- `docx_parse`
- `xlsx_parse`
- `ocr`
- `pageindex`
- `browser_read`
- `browser_write`
- `office_automation`
- `large_file_processing`
- 某个 DocumentStore 的只读或读写访问

不得依据 `platform == windows` 推断节点一定具备 Office，也不得依据 `platform == macos` 推断节点一定具备 PageIndex。操作系统只是能力探测的输入之一，实际调度依赖经过验证的能力声明。

### 8.3 调度规则

JobScheduler 依次考虑：

1. `required_capabilities` 是否全部满足；
2. 节点是否能访问任务输入的逻辑存储；
3. 数据本地性和大文件传输成本；
4. 节点信任等级是否满足任务敏感度；
5. 是否需要指定浏览器登录态或本机应用；
6. 节点负载、并发上限和近期失败率；
7. `preferred_node_id`，但偏好不能绕过硬性能力和权限要求。

### 8.4 租约、心跳和幂等

`processing_jobs` 增加 `required_capabilities`、`data_affinity`、`preferred_node_id`、`lease_owner`、`lease_token_hash`、`lease_expires_at`、`attempt_no`、`idempotency_key` 和 `input_content_hash`。

节点只能通过控制平面领取有限期租约。提交结果时必须同时匹配 job、lease token、attempt、输入哈希和幂等键。过期租约提交不能直接改变权威状态，只能作为待核验的迟到结果保存或拒绝。

无副作用任务在租约过期后可以重新排队；文件移动、浏览器提交等有副作用任务如果执行状态未知，必须进入 `reconciliation_required`，不能自动转交另一节点重复执行。

## 9. SQL、PageIndex 与 RAG

RAG 是检索与生成流程，不是某一种数据库。两种配置使用同一 RetrievalService：

```text
SQL 元数据和权限过滤
  -> SQL 全文检索进行跨文档候选召回
  -> PageIndex 在选定长文档内定位章节和页码
  -> LLM 基于证据生成回答
```

数据库保存权威业务状态、文档身份、版本、项目关系、审核、审批、任务和索引元数据；SQL 全文索引负责跨文档候选召回；PageIndex 负责长文档章节树、页码定位和局部检索；DocumentStore 保存原始文件。

单机 SQLite 使用 FTS5。中心 PostgreSQL 17 使用规范文本列、`tsvector` 和 GIN 索引。语言分词和规范化策略必须版本化，并通过质量基线验证；业务层不直接拼接 FTS5 或 PostgreSQL 查询语法。

PageIndex 通过 `StructureIndex` 接口接入，并作为执行节点能力注册。数据库只保存 Provider、索引类型、外部引用、输入哈希、Provider 版本、生成节点和状态，不把本机结果路径当成核心业务字段。

PageIndex 不可用时，普通查询降级到 SQL 全文检索和标准解析文本并标记 `degraded=true`。必须精确引用章节和页码而当前没有替代能力时返回 `blocked`，不得伪造页码。

首期不引入独立向量数据库。未来语义相似检索有真实评测需求时，通过统一 `SemanticRetriever` 增加 pgvector 或其他实现，Agent 和 RetrievalService 接口不变。

## 10. 数据库领域模型

### 10.1 文件发现与处理

- `asset_observations`：观察节点、逻辑位置、节点路径缓存、大小、时间、就绪状态和快速指纹；
- `processing_jobs`：处理意图、所需能力、优先级、状态、租约、幂等键和请求方；
- `processing_runs`：处理器、版本、策略版本、输入哈希、执行节点、状态和错误码；
- `processing_artifacts`：ContentProfile、规范文本、表格、OCR、document_parse 和结构索引等产物引用；
- `index_records`：数据库全文索引、PageIndex 和未来索引的状态及外部引用。

处理任务状态包括 `queued`、`leased`、`running`、`succeeded`、`partial`、`blocked`、`failed`、`reconciliation_required`、`cancelled`。

### 10.2 候选事实与权威事实

- `candidate_facts`：文件分析 Agent 生成的不可变候选；
- `fact_evidence`：文档版本、页码、章节、原文和内容哈希；
- `fact_decisions`：批准、拒绝、延期、补证据和替换决策；
- `approved_facts`：版本化权威事实；
- `project_current_facts`：当前权威事实的派生查询视图。

高风险字段的最终决策主体不能是 LLM。批准新事实时旧事实标记 `superseded` 并保留有效时间范围，禁止静默覆盖。

### 10.3 审核与审批

- `review_items`：项目匹配、候选事实、文档分类、重复关系、归档目标、低质量提取和外部写操作的统一复核队列；
- `approvals`：绑定主体、主体版本、审批范围、内容哈希、批准人、时间和有效期。

`review_queue.json` 由数据库中未解决的 `review_items` 投影生成，不再是权威队列。

### 10.4 归档

- `archive_intents`：逻辑项目、阶段、类别、保留类型和建议名称；
- `archive_actions`：具体源位置、目标位置、动作类型、策略版本、哈希和幂等键；
- `archive_executions`：执行节点、租约、执行前后状态、实际目标、错误码和回读结果。

归档意图允许 `destination_status=unresolved`。只有 ArchivePolicyResolver 生成具体物理动作并获得审批后，归档执行 Agent 才能工作。

### 10.5 Agent 与审计

- `agent_runs`：Agent 角色、目标、父运行、关联 ID、状态和错误码；
- `handoffs`：Schema 版本、源/目标 Agent、payload 引用、哈希和状态；
- `tool_events`：工具、执行节点、权限范围、输入哈希、输出状态和错误码。

大 payload 可以作为 artifact 保存，数据库保存引用、哈希和 Schema 版本。日志对敏感字段脱敏，不记录 API Key、数据库口令、浏览器 Cookie 或长期访问令牌。

### 10.6 节点与存储

- `execution_nodes`：节点身份、平台、信任等级、状态和心跳；
- `node_capabilities`：节点实际可用 Provider 和能力版本；
- `storage_bindings`：逻辑存储前缀与节点路径映射；
- `node_events`：注册、能力变化、离线、禁用和版本升级审计。

## 11. 长期记忆

首期不提供模型自由写入的通用记忆桶，而是使用明确类型：

- 权威语义记忆：项目、approved facts、fact decisions、文档关系和外部系统链接；
- 候选记忆：candidate facts、review items 和未确认关系，必须标注 `unverified`；
- 情节记忆：agent runs、handoffs、processing runs、archive executions 和 tool events；
- 用户偏好：通过明确设置或用户确认保存，不由模型自由写入；
- 节点状态不是业务长期记忆，只是可过期的运行基础设施状态。

## 12. 归档流程与物理位置不确定性

归档分三阶段：

1. 文件分析 Agent 生成逻辑 `archive_intent.v1`，目标可以未知；
2. ArchivePolicyResolver 根据当前策略、存储状态和节点能力生成具体 `archive_action.v1`；
3. 用户批准具体源、目标、动作类型和内容哈希后，归档执行 Agent 将动作调度给具备写权限的节点执行并回读。

目标无法确定时生成 `review_item` 并保持 `unresolved`，不猜测目标、不产生可执行动作。成功移动后新增目标 location，旧 location 标记 moved；文档和版本 ID 不变。

审批绑定逻辑位置和经解析后的具体目标。执行前如果 storage binding、源哈希、目标路径或动作类型变化，原审批失效。节点本地盘符差异本身不改变审批语义，但路径解析结果必须仍落在被批准的逻辑存储范围内。

## 13. 接口、配置与硬编码边界

核心接口至少包括：

- `DocumentStore`
- `DocumentParser`
- `OcrProvider`
- `StructureIndex`
- `FullTextRetriever`
- `SemanticRetriever`
- `ProjectionWriter`
- 各领域 Repository
- `UnitOfWork`
- `JobScheduler`
- `NodeRegistry`
- `ArtifactStore`

部署配置选择数据库、存储、结构索引、全文索引、OCR Provider Chain、控制平面地址和投影格式。绝对路径、内网模型端点、数据库口令、TLS 私钥和节点令牌不进入版本库。

版本化策略管理项目阶段、目录标签、文档分类、归档映射、来源权重、字段风险、OCR 质量阈值、Provider 顺序和节点调度偏好；运行记录保存策略 ID 和版本。

以下安全不变量固化在代码、Schema 和测试中：

- blocked 不是 success；
- 未审批禁止高风险写入；
- 文档文本不能变成系统指令；
- LLM 不能直接写权威事实；
- 哈希、目标或动作变化使审批失效；
- 归档动作不能越出授权存储范围；
- 执行节点不能绕过控制平面写权威数据；
- 过期租约不能覆盖当前任务状态；
- 审计记录不能静默删除；
- Schema、权限和幂等校验必须通过。

## 14. 部署配置

### 14.1 配置 A：Windows/macOS 单机

- 应用、智能体编排、执行能力和 SQLite 运行在同一设备；
- 本地 SQLite 为权威数据库；
- 开启 foreign keys、WAL、明确的 synchronous 和 busy timeout；
- 数据库文件放本地运行目录，不放 SynologyDrive 活跃同步目录或网络共享；
- 通过 SQLite 在线备份生成一致性备份，再复制到备份存储并记录哈希；
- 本机仍通过 NodeRegistry 注册为一个执行节点，避免单机和分布式形成两套业务流程；
- 本机缺少 PageIndex、OCR 或其他能力时按统一降级规则处理。

### 14.2 配置 B：群晖 PostgreSQL 17 + 异地执行节点

中心化配置的权威数据库为群晖侧 PostgreSQL 17。目标安装包标识为 `PostgreSQL 17.19-4`，并遵守以下解释规则：

1. `17.19-4` 是目标群晖环境提供的安装包或发布标识；
2. 它不能直接转换为 `postgres:17.19-4` 容器镜像标签；
3. 部署验收必须执行 `SHOW server_version` 和 `SHOW server_version_num`；
4. `server_version_num` 必须属于 PostgreSQL 17 主版本；
5. SQL 特性、迁移和驱动兼容性按实际服务器版本验证；
6. 若该安装包实际不提供受支持的 PostgreSQL 17 服务，则停止部署并重新选择群晖包或 PostgreSQL 官方 17 容器，不通过伪造版本配置继续。

中心化拓扑要求：

- PostgreSQL 使用独立数据库、独立应用用户和最小权限；
- 控制平面是正常运行时唯一的数据库访问服务；
- 执行节点通过控制平面 API 领取任务和提交结果；
- PostgreSQL 不直接暴露公网，异地访问经过 VPN、私有网络或安全网关；
- 若控制平面和 PostgreSQL 都在群晖，可通过受控内部网络通信；
- 若控制平面运行在另一台常在线设备，只允许该服务来源访问数据库；
- 运维连接与应用连接使用不同账号和审计策略。

### 14.3 PostgreSQL 数据目录和升级

PostgreSQL 数据目录必须是群晖本地专用持久化目录，不与业务原始文件、SynologyDrive 同步目录或临时产物混用。数据库进程是数据目录唯一写入者。

部署不得使用无法复现的 `latest` 浮动标签。若最终使用容器，应固定 PostgreSQL 17 的明确镜像系列和镜像摘要；若使用群晖包，应记录包标识、实际 server version、安装来源和升级方式。

小版本升级前执行备份和恢复演练检查。主版本升级必须采用 PostgreSQL 支持的 dump/restore 或 `pg_upgrade` 流程，不能让新主版本直接打开旧主版本数据目录。

### 14.4 备份、恢复和可用性

最低备份要求：

- 定期 `pg_dump` 逻辑备份；
- 数据库角色和必要全局对象的受控备份；
- 备份文件写入独立备份位置并记录哈希；
- 至少保留一个不在同一存储故障域的副本；
- 定期在临时数据库执行恢复演练；
- 记录最近成功备份、恢复测试和恢复点信息。

普通文件复制、SynologyDrive 同步和 RAID 不能替代 PostgreSQL 一致性备份。需要物理热备份时使用 PostgreSQL 支持的方法或经过验证的一致性快照流程。

### 14.5 数据库迁移与连接管理

- Schema 迁移由单一 migration owner 执行；
- 应用启动时检查 Schema 版本，不允许多个节点自行跑迁移；
- PostgreSQL 方言实现可以使用其事务、行锁、`SKIP LOCKED`、JSON 和全文索引能力，但必须封装在基础设施层；
- 连接池由控制平面管理，执行节点不各自创建数据库连接池；
- 数据库不可用时停止权威写入，读取可根据已验证缓存提供明确标记的降级结果；
- 恢复连接后按幂等键和状态机继续处理，不回放不确定的副作用。

## 15. 事务、错误、幂等与恢复

### 15.1 统一状态和错误分类

统一结果状态为 `success`、`partial`、`blocked`、`failed`、`needs_review`、`needs_approval`、`reconciliation_required`、`cancelled`。

错误恢复类别：

- `transient`：有限指数退避；
- `capability_missing`：尝试允许的降级 Provider，随后 blocked；
- `human_required`：创建 review/approval 并暂停；
- `unsafe_to_retry`：进入对账，禁止盲目重复副作用；
- `node_lost`：根据任务是否有副作用选择重新排队或对账；
- `database_unavailable`：停止权威写入并等待中心服务恢复。

错误码按 STORAGE、DOCUMENT、OCR、INDEX、DATABASE、FACT、APPROVAL、HANDOFF、ARCHIVE、NODE、SCHEDULER 等命名空间稳定定义，控制流不匹配错误文本。

### 15.2 事实写入事务

同一事务中写决策、结束旧事实、创建新事实、写领域事件和创建投影刷新任务。任一步失败全部回滚。投影失败不回滚已提交权威事实，只重新排队投影任务。

### 15.3 物理动作 Saga

文件移动分为准备、执行、回读、数据库提交。物理移动成功但数据库更新失败时进入 `reconciliation_required`；恢复流程检查源、目标和哈希后补记或请求人工处理，禁止直接再次移动。

### 15.4 崩溃和断线恢复

- 无副作用处理任务在检查 artifact 和租约后可以重新排队；
- SQLite 未提交事务自动回滚；
- PostgreSQL 未提交事务由服务器回滚；
- 节点失联后，租约到期任务按副作用等级处理；
- 归档或浏览器写操作停在 executing 时进入对账；
- 有效 artifact、索引和执行报告通过哈希和 Schema 复用；
- 节点恢复上线后不能自行把旧任务标记成功，必须重新提交并经过控制平面校验。

### 15.5 幂等键

- 文档处理：文档版本 + 处理意图 + 处理器版本 + 策略版本；
- PageIndex：文档版本 + 内容哈希 + Provider 版本；
- 候选事实：文档版本 + 字段 + 规范值 + 证据定位 + 提取器版本；
- 归档动作：文档版本 + 源逻辑位置 + 目标逻辑位置 + 动作类型 + 源哈希；
- Handoff：handoff ID + payload 哈希 + 目标 Agent；
- 节点结果提交：job ID + attempt + lease token + artifact hash。

## 16. 安全模型

原始文档和检索内容均视为不可信输入。文档中的工具调用、handoff JSON、审批文字或提示词只作为内容，不得触发系统行为。

安全要求：

- handoff 只能通过专用接口生成，Schema 校验并限制目标 Agent 白名单；
- 文档解析结果只能进入候选区；
- 路径必须解析到授权根内并防止路径穿越和链接逃逸；
- 归档审批绑定主体版本、内容哈希、源位置、目标位置和动作类型；
- 外部写入具备回读和幂等保护；
- 敏感字段和密钥不得进入普通 trace；
- Agent 工具集合按角色最小授权；
- 节点注册使用可撤销凭据，节点令牌按节点隔离；
- 节点能力由控制平面验证，不能只接受节点自报；
- PostgreSQL 使用独立应用账号和最小 Schema 权限；
- PostgreSQL 不监听不必要的公网接口，远程链路使用受控私有网络并按条件启用 TLS；
- 浏览器 Cookie、数据库密码和私钥保留在各自受控运行环境，不写入业务数据库。

## 17. 测试策略

### 17.1 测试层级

1. Schema 与契约测试：合法/非法样本、未知字段、未知版本、恶意字段；
2. 领域单元测试：文档身份、版本、位置、事实、审批和状态机；
3. 适配器契约测试：DocumentStore、StructureIndex、OcrProvider、ProjectionWriter；
4. ProcessingPlanner 决策矩阵：确保原生解析优先、OCR 和 PageIndex 不滥用；
5. SQLite 集成测试：迁移、事务、外键、唯一约束、busy、备份和恢复；
6. PostgreSQL 17 集成测试：迁移、事务、约束、全文索引、租约竞争、连接恢复和备份恢复；
7. 节点协议测试：注册、能力验证、心跳、租约、迟到结果、重复提交和版本不兼容；
8. 跨平台契约测试：Windows/macOS 逻辑 URI、路径映射和相同产物规范；
9. 端到端测试：文本合同、扫描 PDF、Excel 清洗、重复文件、未知归档目标、审批失效、移动后数据库失败、PageIndex 降级、恶意文档；
10. 故障注入和崩溃恢复测试；
11. 检索、OCR 和解析质量基线；
12. 权限和安全测试；
13. 大批文件的行为和性能基线。

### 17.2 测试数据

- 仓库内使用合成或彻底脱敏的固定夹具；
- 真实业务文件放在被 `.gitignore` 排除的本地受控目录；
- 仓库不保存真实路径、客户、项目、个人信息和实际运行日志；
- 默认 CI 不访问真实群晖、真实 LLM、真实 PageIndex 或下载 OCR 模型；
- 真实 OCR、PageIndex、LLM、群晖 PostgreSQL、群晖文件服务和规模测试作为显式慢速测试。

### 17.3 关键验收

- Windows/macOS 路径变化不改变文档身份；
- 同一逻辑 URI 能通过各节点 storage binding 正确解析；
- 相同哈希复用内容级文本/OCR/结构索引，但项目匹配、候选事实和归档意图按业务上下文重新评估；
- 首次发现不触发全量 OCR/LLM/PageIndex；
- 文本型 PDF、DOCX、XLSX 和 CSV 不错误走 OCR；
- 文件分析 Agent 不能写权威事实或移动文件；
- 项目账本 Agent 是权威事实唯一业务写入口；
- 执行节点不能直接写 PostgreSQL 权威表；
- 过期租约和迟到结果不能覆盖当前结果；
- 未批准、过期或哈希变化的归档动作不能执行；
- 节点失联后的无副作用任务可恢复，有副作用任务进入对账；
- PageIndex 不可用时普通查询可降级，精确证据不足时明确 blocked；
- 物理移动后的不确定状态进入对账，不重复执行；
- JSON、Markdown、HTML、SQL FTS 和 PageIndex 删除后可重建；
- 恶意文档不能触发工具、handoff 或越权写入；
- PostgreSQL 实际 server major 验证为 17；
- PostgreSQL 备份能够在临时环境恢复；
- 每个被发现文件都有明确结果，没有静默丢失。

## 18. 首期完成标准

### 18.1 平台基础完成标准

- 所有新数据访问通过 Repository / Unit of Work；
- SQLite 和 PostgreSQL 17 使用同一领域契约，不共享数据库文件；
- 文档身份、版本、逻辑位置和节点路径映射模型生效；
- 大量文件采用 L0/L1 先行、按需深度处理；
- ProcessingPlanner 能根据意图、内容能力和执行节点能力选择原生解析、OCR、LLM 和 PageIndex；
- SQL 全文检索 + PageIndex 通过统一 RetrievalService 工作并支持降级；
- 候选事实与权威事实严格分离；
- 归档意图、具体动作、审批和执行严格分离；
- 投影和索引可重建；
- 真实运行日志和业务样本不再作为源码仓库数据。

### 18.2 单机配置完成标准

- Windows 和 macOS 均能使用本地 SQLite 启动；
- 同一台机器作为控制平面和执行节点运行；
- 本地数据库具备一致性备份和恢复流程；
- 缺失的 OCR/PageIndex 能力按统一状态降级。

### 18.3 中心化配置完成标准

- 群晖 `PostgreSQL 17.19-4` 安装包对应的实际服务器主版本验证为 17；
- PostgreSQL 成为中心化配置下项目事实、审核、审批、任务和 handoff 的唯一权威源；
- 至少一个 Windows 或 macOS 节点能完成注册、心跳、领取任务和提交结果；
- 控制平面是执行节点访问权威数据的唯一正常运行时入口；
- 节点断线、租约过期、重复提交和迟到结果测试通过；
- PostgreSQL 逻辑备份和恢复演练通过；
- 数据库端口没有直接暴露公网；
- 文件移动后回读并更新位置历史；
- 崩溃恢复、幂等、审批失效和对账测试通过。

## 19. 实施拆分与顺序

本文件是系统级架构规格，不应转换成一次性大重写。实施计划必须拆成带独立验收门的阶段：

1. 配置和接口边界：统一 Settings、Repository、DocumentStore、StructureIndex、ProjectionWriter；
2. 领域 Schema、迁移框架、Unit of Work 和 SQLite 单机实现；
3. PostgreSQL 17 Repository、全文检索、迁移锁、连接和备份验收；
4. 文档身份、版本、逻辑位置、storage binding 和轻量资产目录；
5. NodeRegistry、能力探测、任务租约、心跳和执行节点协议；
6. ProcessingPlanner 与现有 document_parse/OCR 收口；
7. 候选事实、证据、审核和项目账本事务；
8. SQL 全文检索、PageIndex Adapter 和统一 RetrievalService；
9. 逻辑归档意图、目标解析、审批、独立归档执行和对账；
10. JSON/Markdown/HTML 投影及现有文件账本一次性迁移；
11. Agent handoff、角色权限和主助手编排；
12. Windows/macOS 单机验收、群晖 PostgreSQL 中心化验收、故障注入、规模基线和仓库业务数据清理。

每个阶段先建立契约和测试，再迁移调用方，避免文件账本和数据库长期成为两个权威源。第一份实施计划从阶段 1 开始，并把 PostgreSQL 17 的真实部署探针和集成测试纳入阶段 3；不在同一次代码变更中完成全部平台迁移。

## 20. 明确决策记录

截至本规格版本，以下事项已经确定：

- 业务 Agent 采用主助手、文件分析、项目账本、归档执行和后续浏览器自动化的职责划分；
- OCR、document_parse、PageIndex、SQL 和投影器属于服务或适配器，不单独包装成业务 Agent；
- SQL 数据库是权威源，JSON/Markdown 是投影和审计快照；
- 单机配置使用 SQLite；
- 中心化配置使用群晖 PostgreSQL 17；
- 群晖目标包标识为 `PostgreSQL 17.19-4`，实际 server major 必须验收为 17；
- 异地执行节点不直接写 PostgreSQL，通过控制平面 API 工作；
- Windows/macOS、物理路径和 Provider 能力不写死在业务规则中；
- PageIndex 不替代 SQL，只承担长文档结构定位；
- 首期不引入独立向量数据库；
- 原始文件位置可以移动或未知，OCR 不是默认解析策略；
- 归档由独立执行职责根据结构化、已批准动作实施。
