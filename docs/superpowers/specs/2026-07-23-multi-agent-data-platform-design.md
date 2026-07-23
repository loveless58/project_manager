# Project Manager 多智能体数据平台设计

日期：2026-07-23
状态：已确认设计，待实施计划
适用仓库：`loveless58/project_manager`

## 1. 背景与目标

当前项目以一个 ReAct 风格 `LoopEngine` 为控制平面，通过 active skill 和受限 `ToolRegistry` 执行项目管理、文件整理、商机管理和 CloudCC/CRM 任务。现有实现已经具备结构化 observation、确认门、文件账本、OCR Provider、`document_parse.v1`、PageIndex 客户端和部分审计能力，但仍存在以下结构性问题：

- 运行时本质上是单 Agent、单 LoopEngine，多 Agent 角色尚未形成独立权限和结构化 handoff；
- `project_ledger.json`、Markdown 和 JSONL 文件承担了权威业务状态，缺乏事务、并发控制和统一查询；
- 文件路径、SynologyDrive、PageIndex、本机模型端点、业务目录和置信度策略存在不同程度硬编码；
- 文档身份与物理路径耦合，文件移动、改名、重复副本和云盘占位会破坏引用稳定性；
- 文档处理容易以文件格式或 OCR 为中心，没有按业务意图和内容能力选择最小处理路径；
- PageIndex 已有客户端封装，但当前是外部依赖且默认绑定 macOS 路径，不能作为当前 Windows 环境中的稳定运行能力；
- 仓库仍跟踪历史运行日志，并在测试和文档中保留真实或类真实路径，业务数据与源码边界不够清晰。

本设计建立一个可渐进迁移的多智能体项目与办公助手架构，第一阶段以单台 Windows、单用户为主、本地 SQLite 为权威数据库，同时保留向 PostgreSQL、多设备和后台多 Worker 演进的接口边界。

核心目标：

1. 按端到端业务职责和写权限划分 Agent，而不是按 OCR、SQL、PageIndex 等技术组件划分 Agent。
2. 以 SQL 数据库作为项目事实、审核、审批、任务和 handoff 的权威源。
3. 将文件业务身份与物理位置分离，支持移动、改名、重复副本和未知归档位置。
4. 对大量文件采用元数据先行、分层处理、按需深度解析的策略。
5. 使用 SQL 元数据过滤、SQL 全文检索和 PageIndex 长文档结构检索构成首期 RAG，不引入独立向量数据库。
6. 通过稳定接口和显式适配器注册替换 SynologyDrive、PageIndex、OCR、数据库和投影格式，避免基础设施写死。
7. 对权威事实写入使用事务，对文件移动和外部写操作使用审批、回读和对账，禁止盲目重试。
8. 让所有派生索引和可读视图均可从数据库、原始文件和版本化策略重建。

## 2. 非目标

第一阶段不包含：

- 多用户权限管理界面；
- 远程 PostgreSQL 正式部署；
- 独立向量数据库或通用 Embedding 服务；
- 对整个 SynologyDrive 进行全量 OCR、LLM 提取或 PageIndex；
- 自动删除、自动覆盖或未经批准的文件移动；
- 未经批准的 CRM/BPM/浏览器提交；
- 将所有确定性组件改造成通用动态插件平台；
- 让模型自由写入权威事实或长期记忆；
- 让 JSON、Markdown、PageIndex 或 FTS 成为不可替代的权威状态源。

## 3. 已选方案与替代方案

### 3.1 已选方案：稳定接口 + 显式适配器 + 业务 Agent

核心业务只依赖稳定接口，部署配置选择具体适配器。Agent 按独立业务目标、上下文、权限和生命周期划分；确定性能力作为领域服务或工具。

| 逻辑职责 | 稳定接口 | 第一阶段适配器 |
|---|---|---|
| 权威数据存储 | Repository / Unit of Work | SQLite |
| 原始文档存储 | `DocumentStore` | Synology 文件系统 |
| 长文档结构索引 | `StructureIndex` | PageIndex |
| 跨文档全文检索 | `FullTextRetriever` | SQLite FTS5 |
| 文档解析 | `DocumentParser` | `document_parse` |
| OCR | `OcrProvider` | 现有 Provider Chain |
| 派生视图 | `ProjectionWriter` | JSON、Markdown、HTML |
| 语义检索 | `SemanticRetriever` | 第一阶段关闭 |

未来可以替换为 PostgreSQL、S3/MinIO/SharePoint/Box、其他结构索引器、云 OCR、其他投影格式和 pgvector，而不修改 Agent 的业务职责和核心领域规则。

### 3.2 未选方案：直接绑定当前组件

直接在核心逻辑中调用固定 PageIndex 路径、固定 SynologyDrive 盘符、固定 OCR 引擎和固定 Markdown 输出，虽然开发快，但会复制当前的跨平台失败、配置漂移和迁移成本，因此不采用。

### 3.3 未选方案：完全动态插件平台

允许通过字符串动态加载任意 Provider 会增加安全、调试和配置复杂度。第一阶段只定义有限接口和显式注册表，不建设任意代码加载平台。

## 4. 总体架构

```text
用户 / 后台触发器
  -> 办公/项目管理主助手
       -> 文件与文档分析 Agent（只读业务分析）
       -> 项目账本 Agent（权威事实唯一业务写入口）
       -> 归档执行 Agent（受批准约束的文件写入）
       -> 浏览器自动化 Agent（后续接入）

领域服务与适配器
  -> AssetCatalog / DocumentIdentityResolver
  -> ContentProfiler / ProcessingPlanner
  -> DocumentParser / OcrProvider
  -> StructureIndex / FullTextRetriever
  -> ArchivePolicyResolver
  -> Repository / Unit of Work
  -> ProjectionWriter

持久化
  -> SQLite：权威身份、关系、状态、决策、审批和审计
  -> DocumentStore：原始文件和大型产物
  -> FTS / PageIndex：可重建索引
  -> JSON / Markdown / HTML：可重建投影
```

核心系统固化领域契约和安全不变量；基础设施、租户规则和部署环境通过显式配置、适配器和版本化策略提供。

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

负责验证具体归档动作的审批、哈希、权限、目标范围和重名风险，执行已批准移动或改名，回读结果并更新位置历史。

它只能执行批准过的 `action_id`，不能自行选择项目、文档类别或归档目标，不能扩大审批范围；第一阶段默认禁止删除和覆盖。

### 5.5 浏览器自动化 Agent

后续阶段加入，负责外部系统读取、下载、草稿填写、提交和回读。读取/草稿与提交能力具有独立权限边界，提交需要明确审批、幂等和回读保护。

### 5.6 非 Agent 组件

AssetCatalog、DocumentIdentityResolver、ContentProfiler、ProcessingPlanner、DocumentParser、OcrProviderRegistry、StructureIndex、FullTextRetriever、DocumentStore、Repository、ProjectionWriter、ArchivePolicyResolver 以及 Schema/Approval/Hash/Permission Validator 是确定性服务或适配器，不创建独立 Agent。

当前审计组件中可以确定性实现的检查迁入治理服务；只有语义证据复核在后续有明确价值时才升级为独立只读审核 Agent。

## 6. 文档身份与位置模型

物理路径不是文档身份。系统区分：

- `content_object`：二进制内容，以哈希标识；
- `document`：业务文档身份；
- `document_version`：业务文档的内容版本；
- `document_location`：某一版本的物理位置及位置历史；
- `asset_observation`：某次扫描中观察到的文件状态。

### 6.1 `content_objects`

关键字段：`content_object_id`、`sha256`、`size_bytes`、`media_type`、`first_seen_at`。

相同哈希可以复用只依赖二进制内容的原生文本、OCR 和结构索引结果，但不能自动合并不同项目上下文中的业务文档，也不能直接复用项目匹配、候选事实、风险判断或归档意图。后面这些业务产物必须结合当前 document、project、策略版本和处理意图重新评估。系统区分二进制重复和业务重复。

### 6.2 `documents`

关键字段：`document_id`、`project_id`、`document_type`、`logical_title`、`business_status`、`created_at`。路径、文件名或存储提供者变化不改变 `document_id`。

### 6.3 `document_versions`

关键字段：`document_version_id`、`document_id`、`content_object_id`、`version_number`、`version_status`、`supersedes_version_id`、`created_at`。新内容创建新版本，不原地覆盖旧版本。

### 6.4 `document_locations`

关键字段：`location_id`、`document_version_id`、`storage_provider`、`object_key`、`logical_uri`、可选的 `physical_path` 缓存、`location_role`、`availability`、`is_current`、`first_seen_at`、`last_seen_at`。

位置角色包括 `source`、`working_copy`、`archived`、`duplicate`、`cache`、`temporary`、`unknown`；可用性包括 `available`、`placeholder`、`unreadable`、`missing`、`moved`、`deleted`、`unknown`。

数据库和 handoff 主要传递稳定 ID、逻辑 URI 和内容哈希；只有 DocumentStore 适配器在实际操作时解析物理路径。`physical_path` 只用于当前机器的诊断和执行缓存，不能成为跨设备身份、唯一键或审批主体。

## 7. 大量文件的分层处理

默认策略是首次发现只做轻量登记和能力探测。只有被具体任务命中、被业务规则标记为高优先级或被后台计划明确选中时，才进行完整解析、OCR、LLM 提取和 PageIndex。

### 7.1 L0：轻量资产登记

登记文件名、扩展名、大小、修改时间、存储提供者、逻辑位置、云盘就绪状态、快速指纹、首次和最近观察时间。文件量大时不立即计算完整 SHA-256。

### 7.2 L1：内容能力探测

检测 MIME、容器完整性、PDF 文本层、扫描属性、加密状态、工作表、图片尺寸、sidecar 和云盘就绪情况，输出 `ContentProfile`。

### 7.3 L2：标准解析

按内容类型选择 DOCX、XLSX、CSV、JSON/XML、原生 PDF、图片/扫描 PDF、邮件和压缩包清单等处理器。

### 7.4 L3：业务深度提取

只有任务需要时才执行文档分类、项目匹配、字段抽取、风险识别、候选事实、证据定位和 LLM 结构化提取。

### 7.5 L4：结构索引

只有较长、有可用文本、具有章节结构价值且确有查询需求的文档进入 PageIndex。Excel、CSV、短通知、图片票据、压缩包和重复文件不默认进入 PageIndex。

### 7.6 ProcessingPlanner

Agent 声明业务意图，ProcessingPlanner 根据 ContentProfile、可用 Provider 和版本化策略生成确定性处理计划。计划必须说明选择理由、跳过理由、策略版本、预期产物和复核要求。

OCR 只是 `extract_text` 的一种实现。文本型 PDF、DOCX、XLSX、CSV、JSON/XML 和网页 DOM 不默认使用 OCR。处理器失败也不能随意切换到语义不同的策略。

## 8. SQL、PageIndex 与 RAG

RAG 是检索与生成流程，不是某一种数据库。第一阶段使用：

```text
SQL 元数据和权限过滤
  -> SQLite FTS5 跨文档候选召回
  -> PageIndex 在选定长文档内定位章节和页码
  -> LLM 基于证据生成回答
```

SQL 保存权威业务状态、文档身份、版本、项目关系、审核、审批、任务和索引元数据；FTS5 负责跨文档候选召回；PageIndex 负责长文档章节树、页码定位和局部检索；DocumentStore 保存原始文件。

PageIndex 通过 `StructureIndex` 接口接入。数据库只保存 Provider、索引类型、外部引用、输入哈希、Provider 版本和状态，不把本机结果路径当成核心业务字段。

PageIndex 不可用时，普通查询降级到 SQL FTS 和标准解析文本并标记 `degraded=true`。必须精确引用章节和页码而当前没有替代能力时返回 `blocked`，不得伪造页码。

第一阶段不引入独立向量数据库。未来语义相似检索有真实评测需求时，通过统一 `SemanticRetriever` 增加 PostgreSQL pgvector，Agent 和 RetrievalService 接口不变。

## 9. 数据库领域模型

### 9.1 文件发现与处理

- `asset_observations`：扫描看到的路径、大小、时间、就绪状态和快速指纹；
- `processing_jobs`：处理意图、优先级、状态、幂等键和请求方；
- `processing_runs`：处理器、版本、策略版本、输入哈希、状态和错误码；
- `processing_artifacts`：ContentProfile、规范文本、表格、OCR、document_parse 和结构索引等产物引用；
- `index_records`：FTS、PageIndex 和未来索引的状态及外部引用。

处理任务状态包括 `queued`、`running`、`succeeded`、`partial`、`blocked`、`failed`、`cancelled`。

### 9.2 候选事实与权威事实

- `candidate_facts`：文件分析 Agent 生成的不可变候选；
- `fact_evidence`：文档版本、页码、章节、原文和内容哈希；
- `fact_decisions`：批准、拒绝、延期、补证据和替换决策；
- `approved_facts`：版本化权威事实；
- `project_current_facts`：当前权威事实的派生查询视图。

高风险字段的最终决策主体不能是 LLM。批准新事实时旧事实标记 `superseded` 并保留有效时间范围，禁止静默覆盖。

### 9.3 审核与审批

- `review_items`：项目匹配、候选事实、文档分类、重复关系、归档目标、低质量提取和外部写操作的统一复核队列；
- `approvals`：绑定主体、主体版本、审批范围、内容哈希、批准人、时间和有效期。

`review_queue.json` 由数据库中未解决的 `review_items` 投影生成，不再是权威队列。

### 9.4 归档

- `archive_intents`：逻辑项目、阶段、类别、保留类型和建议名称；
- `archive_actions`：具体源位置、目标位置、动作类型、策略版本、哈希和幂等键；
- `archive_executions`：执行前后状态、实际目标、错误码和回读结果。

归档意图允许 `destination_status=unresolved`。只有 ArchivePolicyResolver 生成具体物理动作并获得审批后，归档执行 Agent 才能工作。

### 9.5 Agent 与审计

- `agent_runs`：Agent 角色、目标、父运行、关联 ID、状态和错误码；
- `handoffs`：Schema 版本、源/目标 Agent、payload 引用、哈希和状态；
- `tool_events`：工具、权限范围、输入哈希、输出状态和错误码。

大 payload 可以作为 artifact 保存，数据库保存引用、哈希和 Schema 版本。日志对敏感字段脱敏，不记录 API Key。

## 10. 长期记忆

第一阶段不提供模型自由写入的通用记忆桶，而是使用明确类型：

- 权威语义记忆：项目、approved facts、fact decisions、文档关系和外部系统链接；
- 候选记忆：candidate facts、review items 和未确认关系，必须标注 `unverified`；
- 情节记忆：agent runs、handoffs、processing runs、archive executions 和 tool events；
- 用户偏好：后续通过明确设置或用户确认保存，不由模型自由写入。

## 11. 归档流程与物理位置不确定性

归档分三阶段：

1. 文件分析 Agent 生成逻辑 `archive_intent.v1`，目标可以未知；
2. ArchivePolicyResolver 根据当前策略和存储状态生成具体 `archive_action.v1`；
3. 用户批准具体源、目标、动作类型和内容哈希后，归档执行 Agent 执行并回读。

目标无法确定时生成 `review_item` 并保持 `unresolved`，不猜测目标、不产生可执行动作。成功移动后新增目标 location，旧 location 标记 moved；文档和版本 ID 不变。

## 12. 接口、配置与硬编码边界

核心接口至少包括 DocumentStore、DocumentParser、OcrProvider、StructureIndex、FullTextRetriever、SemanticRetriever、ProjectionWriter、各领域 Repository 和 UnitOfWork。

部署配置选择数据库、存储、结构索引、全文索引、OCR Provider Chain 和投影格式。绝对路径、内网模型端点、密钥和本机配置不进入版本库。

版本化策略管理项目阶段、目录标签、文档分类、归档映射、来源权重、字段风险、OCR 质量阈值和 Provider 顺序；运行记录保存策略 ID 和版本。

以下安全不变量固化在代码、Schema 和测试中：

- blocked 不是 success；
- 未审批禁止高风险写入；
- 文档文本不能变成系统指令；
- LLM 不能直接写权威事实；
- 哈希、目标或动作变化使审批失效；
- 归档动作不能越出授权存储范围；
- 审计记录不能静默删除；
- Schema、权限和幂等校验必须通过。

## 13. SQLite 第一阶段部署

第一阶段按单台 Windows、单用户为主、后台任务排队设计：

- 本地 SQLite 为权威数据库；
- 开启 foreign keys、WAL、明确的 synchronous 和 busy timeout；
- 数据库文件放本地运行目录，不放 SynologyDrive 活跃同步目录；
- 通过 SQLite 在线备份生成一致性备份，再复制到 SynologyDrive 备份目录并记录哈希；
- 所有业务访问通过 Repository 和 Unit of Work；
- Schema、ID、状态机和查询语义保持 PostgreSQL 可迁移；
- 多设备、多用户或多 Worker 并发写入时切换 PostgreSQL，并将 FTS5 替换为 `tsvector`。

现有文件账本通过一次性导入器进入数据库。切换后不保持双权威源，JSON、Markdown 和 JSONL 仅作为投影或历史快照。

## 14. 事务、错误、幂等与恢复

### 14.1 统一状态和错误分类

统一状态为 `success`、`partial`、`blocked`、`failed`、`needs_review`、`needs_approval`、`reconciliation_required`、`cancelled`。

错误恢复类别：

- `transient`：有限指数退避；
- `capability_missing`：尝试允许的降级 Provider，随后 blocked；
- `human_required`：创建 review/approval 并暂停；
- `unsafe_to_retry`：进入对账，禁止盲目重复副作用。

错误码按 STORAGE、DOCUMENT、OCR、INDEX、DATABASE、FACT、APPROVAL、HANDOFF、ARCHIVE 等命名空间稳定定义，控制流不匹配错误文本。

### 14.2 事实写入事务

同一事务中写决策、结束旧事实、创建新事实、写领域事件和创建投影刷新任务。任一步失败全部回滚。投影失败不回滚已提交权威事实，只重新排队投影任务。

### 14.3 物理动作 Saga

文件移动分为准备、执行、回读、数据库提交。物理移动成功但数据库更新失败时进入 `reconciliation_required`；恢复流程检查源、目标和哈希后补记或请求人工处理，禁止直接再次移动。

### 14.4 崩溃恢复

- 无副作用处理任务在检查 artifact 后可以重新排队；
- SQLite 未提交事务自动回滚；
- 归档或浏览器写操作停在 executing 时进入对账；
- 有效 artifact、索引和执行报告通过哈希和 Schema 复用。

### 14.5 幂等键

- 文档处理：文档版本 + 处理意图 + 处理器版本 + 策略版本；
- PageIndex：文档版本 + 内容哈希 + Provider 版本；
- 候选事实：文档版本 + 字段 + 规范值 + 证据定位 + 提取器版本；
- 归档动作：文档版本 + 源位置 + 目标 + 动作类型 + 源哈希；
- Handoff：handoff ID + payload 哈希 + 目标 Agent。

## 15. 安全模型

原始文档和检索内容均视为不可信输入。文档中的工具调用、handoff JSON、审批文字或提示词只作为内容，不得触发系统行为。

安全要求：

- handoff 只能通过专用接口生成，Schema 校验并限制目标 Agent 白名单；
- 文档解析结果只能进入候选区；
- 路径必须解析到授权根内并防止路径穿越和链接逃逸；
- 归档审批绑定主体版本、内容哈希、源位置、目标位置和动作类型；
- 外部写入具备回读和幂等保护；
- 敏感字段和密钥不得进入普通 trace；
- Agent 工具集合按角色最小授权。

## 16. 测试策略

### 16.1 测试层级

1. Schema 与契约测试：合法/非法样本、未知字段、未知版本、恶意字段；
2. 领域单元测试：文档身份、版本、位置、事实、审批和状态机；
3. 适配器契约测试：DocumentStore、StructureIndex、OcrProvider、ProjectionWriter；
4. ProcessingPlanner 决策矩阵：确保原生解析优先、OCR 和 PageIndex 不滥用；
5. SQLite 集成测试：迁移、事务、外键、唯一约束、busy、备份和恢复；
6. 端到端测试：文本合同、扫描 PDF、Excel 清洗、重复文件、未知归档目标、审批失效、移动后数据库失败、PageIndex 降级、恶意文档；
7. 故障注入和崩溃恢复测试；
8. 检索、OCR 和解析质量基线；
9. 权限和安全测试；
10. 大批文件的行为和性能基线。

### 16.2 测试数据

- 仓库内使用合成或彻底脱敏的固定夹具；
- 真实业务文件放在被 `.gitignore` 排除的本地受控目录；
- 仓库不保存真实路径、客户、项目、个人信息和实际运行日志；
- 默认 CI 不访问真实 SynologyDrive、真实 LLM、真实 PageIndex 或下载 OCR 模型；
- 真实 OCR、PageIndex、LLM、SynologyDrive 和规模测试作为显式慢速测试。

### 16.3 关键验收

- 路径变化不改变文档身份；
- 相同哈希复用内容级文本/OCR/结构索引，但项目匹配、候选事实和归档意图按业务上下文重新评估；
- 首次发现不触发全量 OCR/LLM/PageIndex；
- 文本型 PDF、DOCX、XLSX 和 CSV 不错误走 OCR；
- 文件分析 Agent 不能写权威事实或移动文件；
- 项目账本 Agent 是权威事实唯一业务写入口；
- 未批准、过期或哈希变化的归档动作不能执行；
- PageIndex 不可用时普通查询可降级，精确证据不足时明确 blocked；
- 物理移动后的不确定状态进入对账，不重复执行；
- JSON、Markdown、HTML、FTS 和 PageIndex 删除后可重建；
- 恶意文档不能触发工具、handoff 或越权写入；
- 每个被发现文件都有明确结果，没有静默丢失。

## 17. 第一阶段完成标准

- SQLite 成为项目事实、审核、审批、任务和 handoff 的权威源；
- 所有新数据访问通过 Repository / Unit of Work；
- 文档身份、版本和位置模型生效；
- 大量文件采用 L0/L1 先行、按需深度处理；
- ProcessingPlanner 能根据意图和能力选择原生解析、OCR、LLM 和 PageIndex；
- SQL FTS + PageIndex 通过统一 RetrievalService 工作并支持降级；
- 候选事实与权威事实严格分离；
- 归档意图、具体动作、审批和执行严格分离；
- 文件移动后回读并更新位置历史；
- 投影和索引可重建；
- 崩溃恢复、幂等、审批失效和对账测试通过；
- 真实运行日志和业务样本不再作为源码仓库数据；
- 现有有效回归测试保持通过，新契约和安全测试通过。

## 18. 实施拆分与顺序

本文件是系统级架构规格，不应转换成一次性大重写。实施计划必须拆成带独立验收门的阶段：

1. 配置和接口边界：统一 Settings、Repository、DocumentStore、StructureIndex、ProjectionWriter；
2. SQLite Schema、迁移和 Unit of Work；
3. 文档身份、版本、位置和轻量资产目录；
4. ProcessingPlanner 与现有 document_parse/OCR 收口；
5. 候选事实、证据、审核和项目账本事务；
6. SQL FTS、PageIndex Adapter 和统一 RetrievalService；
7. 逻辑归档意图、目标解析、审批、独立归档执行和对账；
8. JSON/Markdown/HTML 投影及现有文件账本一次性迁移；
9. Agent handoff、角色权限和主助手编排；
10. 故障注入、规模基线、真实环境慢速验收和仓库业务数据清理。

每个阶段先建立契约和测试，再迁移调用方，避免文件账本和数据库长期成为两个权威源。第一份实施计划从阶段 1 开始，并把后续阶段保持为明确依赖，不在同一次代码变更中完成全部平台迁移。
