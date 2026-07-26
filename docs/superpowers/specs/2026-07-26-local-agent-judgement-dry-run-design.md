# 本机 Agent Judgment Dry-run 设计

## 状态与目标

本设计覆盖业务文件判断平台的下一阶段：在当前 Windows 或 macOS 节点完成可重复的本机 dry-run，不连接群晖、不部署 PostgreSQL、不读取真实业务目录，也不执行物理归档。

本阶段新增的判断方式是 `agent`：执行/编排 agent 可以直接完成一次受控的业务语义判断，不要求本机另行配置模型 API。该方式不能绕过既有的解释、审阅、归档与反馈安全边界。

## 范围

### 包含

- 在隔离本地目录中生成和运行脱敏或合成样本。
- 使用只读 source binding、本地 SQLite、本地 runtime workspace 和本地 projection。
- 让编排 agent 将受控 evidence pack 解释为严格的 `candidate_document_interpretation.v1`。
- 对 PDF、DOCX、XLSX、Markdown、XML 验证 native 解析、检索、解释、审阅工件和反馈表单。
- 对扫描 PDF、PNG、JPG 验证 `OCR.CAPABILITY_DISABLED`、零 OCR provider 探测和零归档结果。
- 保留 configured LLM adapter，供后续无人值守、批量或异地节点执行。

### 不包含

- 群晖、PostgreSQL 17、PageIndex 远程连接、VPN、Lucky 或公网网络配置。
- PostgreSQL schema 迁移、数据库备份或生产账号创建。
- 真实业务文件、真实客户/合同/项目名称或凭据。
- `confirmed=True`、自动 feedback apply、移动、改名、覆盖或删除文件。

## 隔离拓扑

```text
<dry-run-root>/
  source/       只读合成或脱敏输入
  runtime/      SQLite、runs、缓存、日志和投影
  config/       节点配置与业务上下文 catalog
```

- `source/` 映射为 enabled、readable、non-writable 的 source binding。
- 可选 archive binding 必须是独立空目录；即使被配置，执行门也始终接收 `confirmed=False`。
- `runtime/` 必须是执行节点的本地非同步磁盘，不能位于 SynologyDrive、OneDrive、SMB/NFS 或任意 storage binding 内。
- catalog 仅保存合成候选与逻辑引用；物理路径不能进入 catalog、stdout、evidence、审阅工件或 LLM 输入。

## 判断模式

`DocumentInterpretationService` 保持唯一的业务判断契约入口。新旧调用方式都必须走其验证链路。

| 模式 | 调用方 | 用途 | 无法使用时的行为 |
|---|---|---|---|
| `agent` | 当前交互式执行/编排 agent | 本机人工参与的 dry-run | `LLM.CAPABILITY_DISABLED`，停止在 blocked/needs_review |
| `configured_llm` | 可注入的 API 或本地模型 adapter | 无人值守、批处理、异地节点 | `LLM.CAPABILITY_DISABLED`，停止在 blocked/needs_review |
| `disabled` | 不调用语义判断 | 只验证解析、检索和工件边界 | 生成明确阻断，不猜测业务关系 |

`agent` 不是自由文本捷径。它接收的输入仅包括经验证、脱敏的 evidence pack、业务上下文和逻辑 `DocumentRef`；输出必须是 `candidate_document_interpretation.v1`。输出继续经过 schema、敏感文本、证据、DocumentRef、候选关系、固定 review policy 与 archive intent 校验。

运行工件必须记录 `interpreter_mode`、agent 或模型标识、输入 hash、输出 hash、时间和验证状态。该记录用于审计与回放，不能包含凭据或物理路径。

## Agent Host Gateway

裸 CLI 不能隐式调用当前 Codex 或 Claude 会话。agent 模式只能由显式的执行/编排 host 提供 AgentJudgementGateway；CLI、本地配置和已配置的 LLM adapter 都不能把当前交互会话当作隐式后备能力。

Gateway 的受控流程固定为：

1. 调用方创建并验证 agent_judgement_request.v1，其中包含 run ID、受控 evidence、逻辑 DocumentRef、business context、允许的输出 schema，以及 request hash。
2. Host 使用当前 agent 生成严格 JSON agent_judgement_response.v1，并附带 agent/model 标识。
3. resume 或 host tool 将响应交回 DocumentInterpretationService；服务必须重新校验 request hash、run ID、candidate IDs、evidence refs、schema 与 sensitive bounds，随后才可生成 review artifacts。
4. 缺少 Gateway 或 response、request hash 不匹配、schema 错误时，必须返回 LLM.CAPABILITY_DISABLED 或 validation block。禁止静默由 configured LLM 代答，也禁止猜测业务关系。

AgentJudgementGateway 不是 LoopEngine：它不存储业务文件、没有 archive 权限，也不直接写 SQLite 或 review queue。持久化继续由既有 run/artifact 事务完成。

## 数据流与权限

```text
source binding (read-only)
  -> native parser / disabled OCR gate
  -> validated extraction artifact
  -> business-context retrieval
  -> optional PageIndex structure boundary
  -> DocumentInterpretationService
       -> agent adapter 或 configured LLM adapter
  -> normalized review queue / feedback form / archive intent
  -> execute_archive_plan(confirmed=False)
```

- Agent 和 configured LLM 的权限相同：只能形成 review-only 判断；均不能设置 `confirmed=True`，也不能调用 feedback apply、文件移动或归档执行。
- PageIndex 结果只表示索引边界完成；其 opaque reference 不得进入 business evidence 或直接成为关系结论。
- SQL/SQLite 只是结构化状态、审计与查询层，不是业务文件或归档执行的唯一权威来源。

## 本机首轮验收

使用合成样本：native-text invoice PDF、contract DOCX、governance Markdown、project XLSX、XML、image-only scanned PDF。

成功条件：

1. 五个 native 文件均形成经过严格验证的 `needs_review` 工件。
2. 发票关系可匹配合成合同 `C-001`；candidate fields 不得包含 `project_name`。
3. 扫描件返回 `OCR.CAPABILITY_DISABLED`，不探测或调用 OCR provider，CLI exit code 为 `2`。
4. source 文件 SHA-256 前后一致；runtime 中不存在 `archive_result.json`。
5. review queue 的每个 item 都有非空 `id` 和 `question`。
6. 任何解释 schema、adapter、检索或安全边界错误都 fail closed，不自动降级为规则猜测。
7. CLI stdout 只输出脱敏状态、逻辑 binding、failure code、run ID 与 artifact 名称。

## 故障与恢复

- 不可用的 agent host 或 configured LLM 不重试为无依据的业务判断，而是写入 `LLM.CAPABILITY_DISABLED` 或明确 validation block。
- 扫描件不触发 OCR fallback；后续如需 OCR，必须另立已批准的 OCR workflow。
- 本机 dry-run 失败只留下审计工件与日志；不会修改 source 或 target binding。
- 清理 runtime 只能在人工确认后针对明确的本地 dry-run root 进行，不由 CLI 自动递归删除。

## 测试与交付

- 以临时目录执行 integration test，fake 仅限 agent host、OpenAI-compatible transport 或 PageIndex network client；native parser、storage router、artifact writer、review queue 与 archive gate 使用真实实现。
- 增加 agent adapter 的严格 schema 正反例、无 agent host 的 fail-closed 例、configured LLM 后备例和无 `confirmed=True` 回归。
- 更新 quickstart：区分 interactive `agent` dry-run 与 configured LLM unattended 模式；明确 PostgreSQL/群晖不属于本阶段。
- 交付本地配置模板、合成 catalog、dry-run runner、验证报告与操作说明；所有模板使用占位符，不含真实路径或密钥。
