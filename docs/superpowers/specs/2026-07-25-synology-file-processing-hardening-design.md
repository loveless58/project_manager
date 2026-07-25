# 多业务文件源识别与归档前置加固设计

## 1. 背景与目标

2026-07-25 使用仓库中的 `data_cleaning_file_organization` Skill 对
“受控业务文件源中的五个真实样本”进行了一次隔离、只读试运行。运行包位于
“受控临时试运行目录”（不记录真实物理路径），源文件处理前后 SHA-256 一致，归档调用
停在 `confirmed=False`，没有发生文件移动。

试运行验证了 PDF 原生文本、扫描 PDF OCR、候选事实、复核队列、归档计划和
确认门能够运行，也暴露出以下问题：

1. Windows 节点因 RapidOCR、PaddleOCR、MinerU 和 Tesseract 均未安装而隐式
   回退到 EasyOCR；OCR 选择逻辑分别存在于 `DataCleaningTools` 与
   `ocr.provider_registry`，不是单一事实来源。
2. 发票表头“项目名称 / 规格型号”被通用招投标正则误识别为业务
   `project_name`，并产生了不属于发票的投标时间字段。
3. 文件类型、业务归属和归档阶段主要由纯函数判断，没有结合项目账本、合同
   证据、PageIndex 和 LLM 做可审计的业务裁决。
4. `DataCleaningTools(workspace_dir=...)` 同时改变运行目录和归档根，导致隔离
   试运行生成指向“受控临时试运行目录下项目文件子目录”的非生产目标。
5. 项目内部 PRD 在解析阶段和归档阶段得到不同分类。
6. `review_queue` 规范化后又追加原始验证异常，生成了空 ID、空问题的反馈项；
   `verification_verdict` 也没有兼容验证产物中的 `overall_verdict`。
7. 旧版 `.doc` 没有安全转换路径。

本设计在不引入 SQL 业务 Repository、不扩展 `LoopEngine`、不执行真实归档的
前提下，修复上述问题并为后续权威 SQL、RetrievalService 和多节点执行保留
稳定接口。

## 2. 本轮范围

### 2.1 纳入范围

- 统一 OCR Provider Registry 与版本化选择策略；
- 默认禁用 EasyOCR，不再把它作为隐式回退；
- 将 PaddleOCR、RapidOCR、macOS Vision 和可选 Tesseract 建模为可探测的
  `OcrProvider`；
- 将 MinerU 建模为 `DocumentParser` Provider，不把 MinerU 当作普通 OCR
  fallback；
- 发票专用字段 Schema 与字段策略；
- 规则候选、RetrievalService、PageIndex 和 LLM 结构化裁决组成的文件解释与
  业务归属链路；
- 运行工作区、多业务文件源和多个候选归档目标解耦；
- 项目治理文档统一分类；
- 反馈项统一规范化和验证 verdict 修复；
- LibreOffice Headless 旧版 `.doc` 安全转换；
- 使用相同真实样本进行只读回归，OFD 除外。

### 2.2 明确不做

- 不安装或实现 OFD 转换器；
- 不把 EasyOCR 卸载，只有显式配置允许时才可作为非默认 Provider；
- 不执行 `execute_archive_plan(..., confirmed=True)`；
- 不移动、重命名、覆盖或删除 SynologyDrive 原文件；
- 不递归扫描任何完整业务文件源；本轮只处理已批准的 SynologyDrive 样本；
- 不实现 PostgreSQL、SQLite 业务表或业务 Repository；
- 不实现多 Agent handoff；
- 不把新能力接入或扩展探索性的 `LoopEngine`；
- 不确定项目治理文档的最终物理归档目录；
- 不让 PageIndex、JSON、Markdown 或 LLM 输出成为权威业务状态源。

## 3. 核心原则

1. **解析与裁决分离**：OCR、原生解析、MinerU 负责可重复的内容提取；LLM
   负责结合检索证据进行文件类型和业务关系裁决。
2. **候选与权威分离**：本轮所有识别结果、LLM 关系和归档位置都是候选，必须
   经过复核与确认。
3. **物理位置解耦**：运行态只写节点本机非同步目录；系统允许一个节点配置多个
   业务文件源和多个候选归档目标。原文件和最终归档位置由 StorageBinding、
   DocumentStoreRouter 与 ArchiveTargetResolver 决定，不存在全局唯一业务根。
4. **Provider 显式选择**：每次处理记录尝试顺序、选中 Provider、版本、阻断
   原因和策略版本，不静默改变语义。
5. **证据优先**：LLM 判断必须返回 `evidence_refs`、冲突、置信度和模型/策略
   版本；缺少必要证据不得进入可执行归档状态。
6. **PageIndex 按需使用**：短发票本身不强制建立 PageIndex；PageIndex 用于在
   已筛选的长合同或长业务文档中定位甲乙方、金额、付款和开票条款。
7. **失败可见**：Provider 不可用、模型缺失、转换超时、检索证据不足均返回
   稳定的 `blocked` 或 `needs_review`，不能伪装成功。

## 4. 总体处理链路

```text
DocumentStoreRouter 按 StorageBinding 读取原文件
  -> ContentProfiler 判断文字层、扫描件、格式和复杂度
  -> ProcessingPlanner 选择原生解析、OCR 或 MinerU
  -> DocumentParser 生成标准内容与页级证据
  -> DeterministicSignalExtractor 生成字段和文件类型候选
  -> RetrievalService 检索候选项目、合同与业务上下文
       -> 当前阶段：项目账本与受控文件目录元数据
       -> 长文档内部：StructureIndex / PageIndex
       -> 后续阶段：SQL 元数据与 SQL FTS
  -> DocumentInterpretationService 调用 LLM 进行结构化裁决
  -> CandidateDocumentInterpretation
  -> ReviewQueue / Human Feedback
  -> ArchivePolicyResolver 生成候选归档动作
  -> confirmed=False 停在确认门
```

每一份准备进入归档计划的业务文件都必须经过一次结构化 LLM 裁决。PageIndex
只在裁决需要长合同或长文档证据时由 RetrievalService 调用。若 LLM 或裁决所需
的 PageIndex 能力不可用，允许保留解析产物，但不得产生 `ready` 的归档动作。

## 5. OCR Provider 架构

### 5.1 统一接口

新增或收口为单一协议：

```python
class OcrProvider(Protocol):
    name: str

    def probe(self) -> CapabilityReport: ...
    def extract(self, request: OcrRequest) -> OcrResult: ...
```

`OcrRequest` 至少包含：

- `source_path`；
- `media_type`；
- `document_version_id` 或本轮临时稳定 ID；
- `content_hash`；
- `requested_languages`；
- `page_selection`；
- `runtime_workspace`。

`OcrResult` 延续 `ocr.result.v1`，并补充：

- `provider_version`；
- `policy_version`；
- `attempted_providers`；
- 页级文本、置信度和来源引用；
- `blocked_reason`；
- `needs_human_review`。

### 5.2 Provider 类型

- `NativePdfTextProvider`：有可靠文字层时优先，不属于图像 OCR；
- `MacOsVisionOcrProvider`：仅在能力探测通过的 macOS 节点启用；
- `PaddleOcrProvider`：完整 PaddleOCR Provider，面向需要版面或表格能力的扫描
  文档；
- `RapidOcrProvider`：ONNX Runtime 轻量 Provider；
- `TesseractOcrProvider`：仅在显式允许并具备语言包时启用；
- `EasyOcrProvider`：保留适配器，但 `enabled_by_default=False`；
- `CustomOcrProvider`：受控注入的外部 OCR 服务。

### 5.3 默认选择策略

Windows：

```text
可靠 PDF 文字层
  -> PaddleOCR 或 RapidOCR（按显式节点配置）
  -> blocked
```

macOS：

```text
可靠 PDF 文字层
  -> macOS Vision
  -> PaddleOCR 或 RapidOCR（按显式节点配置）
  -> blocked
```

EasyOCR 不出现在默认序列中。Tesseract 只有在本地配置明确允许并完成语言包探测
后才进入序列。选择策略不按操作系统猜测能力，而依据 Provider `probe()` 的真实
结果和显式优先级。

### 5.4 依赖隔离

PaddleOCR、PaddlePaddle、MinerU 等重量级依赖优先使用独立运行时或受控子进程
适配器，避免把主应用的 Python 3.13 环境与模型框架强耦合。运行时路径、模型目录
和 Provider 版本通过本地配置或环境变量提供，不写死在仓库。

安装前必须依据对应 Provider 的受支持 Python/平台矩阵选择可复现环境；安装结果
必须通过 `probe()`、受控样本和版本记录验收，不能仅以 `pip install` 成功为准。

## 6. MinerU 边界

MinerU 实现 `DocumentParser`，不实现 `OcrProvider`。它适用于复杂 PDF 的版面、
阅读顺序、标题、表格、公式、图片和 Markdown/JSON 结构化输出。

```python
class DocumentParser(Protocol):
    name: str

    def probe(self) -> CapabilityReport: ...
    def parse(self, request: DocumentParseRequest) -> DocumentParseResult: ...
```

ProcessingPlanner 的首期选择规则：

- DOCX、XLSX、Markdown、XML：优先原生解析；
- 有可靠文字层的普通 PDF：优先 PyMuPDF，再按复杂度决定是否调用 MinerU；
- 扫描 PDF/图片：先选 OcrProvider，再把页级文本和图像信息交给 MinerU 或标准
  解析层；
- 复杂表格、多栏、公式或阅读顺序重要的 PDF：调用 MinerU；
- 短发票不因业务关联判断而强制进入 PageIndex。

MinerU 的临时文件、模型缓存和派生产物必须位于节点本机运行目录。原文件只读，
输出通过稳定 artifact ID 引用。

## 7. 文件类型与业务归属裁决

### 7.1 确定性信号层

规则不再输出最终文件类型和项目归属，只生成可审计候选：

```json
{
  "candidate_type": "发票",
  "confidence": 0.98,
  "signals": [
    "invoice_number_present",
    "buyer_seller_sections_present",
    "tax_amount_present"
  ]
}
```

强规则仍用于格式检查、字段规范化、税号和日期校验，因为这些操作可重复、低成本
且易审计。

### 7.2 RetrievalService

LLM 不直接依赖 `PageIndexClient`，而调用统一 RetrievalService：

```python
class RetrievalService(Protocol):
    def find_business_candidates(
        self,
        query: BusinessContextQuery,
    ) -> BusinessContextEvidence: ...
```

当前无 SQL 阶段：

1. 使用项目账本、候选项目元数据、文件位置和主体名称生成候选项目/合同；
2. 对候选长合同使用 `StructureIndex`；
3. `StructureIndex` 由配置选择 PageIndex 或 disabled Provider；
4. 返回合同主体、金额、付款节点、开票条件的章节和页码证据；
5. 禁止对任何业务绑定或全部业务绑定无筛选地执行全量 PageIndex。

未来 SQL 上线后，候选召回顺序变为 SQL 元数据/FTS，再由 PageIndex 做文档内定位，
业务接口保持不变。

### 7.3 LLM 结构化裁决

新增 `DocumentInterpretationService`，输入：

- 标准解析结果；
- OCR/MinerU 页级证据；
- 确定性文件类型候选；
- 候选项目和合同证据；
- 允许的文档类型与关系 Schema；
- prompt、模型和策略版本。

输出 `candidate_document_interpretation.v1`：

```json
{
  "schema_version": "candidate_document_interpretation.v1",
  "status": "success | needs_review | blocked",
  "document_type": "发票",
  "document_type_confidence": 0.99,
  "business_relation": {
    "candidate_project_id": "",
    "candidate_contract_id": "",
    "relation_type": "contract_invoice",
    "confidence": 0.83,
    "reasoning_summary": "购买方与合同甲方一致，销售方与合同乙方一致，金额符合付款节点",
    "evidence_refs": [],
    "conflicts": []
  },
  "model": "",
  "prompt_version": "",
  "policy_version": "document-interpretation.v1"
}
```

输出必须通过 JSON Schema 校验。LLM 原始响应不直接写账本，不直接生成可执行归档
动作。缺少必要证据、存在多个同等候选或 LLM/检索能力不可用时返回
`needs_review`/`blocked`。

## 8. 发票专用语义

发票永远不从明细表头产生业务 `project_name`。发票解析 Schema 包含：

- 发票号码、发票日期；
- 购买方/收票方名称与统一社会信用代码；
- 销售方名称与统一社会信用代码；
- 不含税金额、税额、价税合计；
- 明细行中的项目/商品名称、规格型号、单位、数量和金额。

发票中的“项目名称”映射为 `line_items[].item_name`，不映射为业务项目名称。

发票与项目/合同的关联证据按以下优先级考虑：

1. 购买方税号与合同甲方税号、销售方税号与合同乙方税号；
2. 规范化法定主体名称和甲乙方角色方向；
3. 合同编号、项目编号或既有开票台账；
4. 金额与合同付款/开票节点；
5. 开票日期与合同执行周期；
6. 商品/服务名称与合同标的；
7. 文件名和目录位置只能作为弱证据。

收票人与合同甲方一致是重要证据，但不是唯一依据。若同一甲方存在多个项目、主体
关系不明确、金额无法对应或角色方向冲突，必须进入人工复核。

## 9. 多业务文件源、路径与存储绑定

SynologyDrive 只是当前试运行使用的一个业务文件源，不是领域模型中的唯一业务根。
同一节点可以同时访问本机目录、多个 SynologyDrive 目录、SMB/NFS 挂载、外接盘、
临时导入目录或其他受控 DocumentStore；不同执行节点也可以对同一逻辑存储使用
不同物理路径。

### 9.1 StorageBinding

新增可版本化的 `StorageBinding`：

```json
{
  "binding_id": "synology-office-primary",
  "provider": "local_filesystem",
  "node_id": "windows-office-01",
  "logical_root": "business://office-primary/",
  "physical_root": "<由执行节点配置的受控业务文件源物理根目录>",
  "roles": ["source", "archive_target"],
  "readable": true,
  "writable": true,
  "trust_level": "trusted",
  "enabled": true
}
```

`physical_root` 只存在于节点本地配置，不写入可移植业务事实。业务产物引用：

- `binding_id`；
- `logical_uri`；
- 文档 ID/版本 ID；
- 内容哈希；
- 必要时记录执行时解析出的物理路径作为审计证据。

同一逻辑文档可以拥有多个位置绑定，不能把盘符、挂载点或文件名当作文档身份。

本轮实现节点本地、静态配置驱动的多 binding registry，用于先消除单根假设；
StorageBinding 的集中持久化、跨节点同步和 SQL 管理接口留待后续，不在本轮引入新的
权威状态源。

### 9.2 多绑定路由

新增 `StorageBindingRegistry` 和按绑定路由的 DocumentStore：

```python
class StorageBindingRegistry(Protocol):
    def get(self, binding_id: str) -> StorageBinding: ...
    def list_enabled(self, role: str = "") -> list[StorageBinding]: ...

class DocumentStoreRouter(Protocol):
    def stat(self, ref: DocumentRef) -> ObjectStat: ...
    def open_read(self, ref: DocumentRef): ...
```

文件整理请求必须显式携带 `DocumentRef` 或可解析为 DocumentRef 的输入，不依赖
唯一 `business_root`。首次兼容阶段允许把绝对路径映射到唯一匹配的 binding；
没有匹配或同时匹配多个 binding 时返回 `blocked`，不猜测来源。

### 9.3 归档目标独立解析

源绑定和目标绑定可以相同，也可以不同。归档首先生成逻辑 `ArchiveIntent`：

```json
{
  "source_ref": {
    "binding_id": "synology-office-primary",
    "logical_uri": "business://office-primary/incoming/invoice.pdf"
  },
  "destination_status": "unresolved",
  "candidate_target_binding_ids": [],
  "project_id": "",
  "archive_phase": "",
  "content_hash": ""
}
```

`ArchiveTargetResolver` 根据项目、文档类型、节点写权限、存储策略和人工选择解析
目标 binding 与逻辑路径。没有唯一合法目标时保持
`destination_status=unresolved` 并进入复核，不能默认使用 SynologyDrive，也不能
默认使用源文件所在根。

### 9.4 运行态保持节点本地

所有 StorageBinding 都与节点本地运行态分离：

```text
storage_bindings[]
  = 多个业务文件源/归档目标

runtime_workspace
  = 当前执行节点本机非同步目录

data_cleaning_workspace
  = <runtime_workspace>\数据清洗工作台
```

`DataCleaningTools` 的正式构造入口接收完整 Settings、Binding Registry、
DocumentStore Router 和 ArchiveTargetResolver。测试保留隔离 binding 与临时运行
目录，不访问真实业务存储。

现有单值 `business_root` 仅作为兼容配置：加载时转换为一个显式 legacy binding，
新领域对象和新接口不得继续把它当作唯一业务根。归档动作必须保存逻辑目标和执行时
解析的物理目标；本轮仍只生成计划。

## 10. 项目治理文档分类

解析和归档共享同一 `DocumentClassification`：

- `document_type`；
- `business_domain`；
- `project_phase`；
- `archive_phase`；
- `confidence`；
- `evidence`；
- `requires_review`。

项目 PRD、全量文件审计报告、归档摘要、workspace config、OCR provider 设计等统一
标记为：

```json
{
  "document_type": "项目治理文档",
  "business_domain": "project_manager_internal",
  "project_phase": null,
  "archive_phase": null,
  "requires_review": true
}
```

本轮继续使用 `pm_internal_archive_pending_redesign` 阻断，不为其猜测目标目录。

## 11. 旧版 DOC 转换

新增 `OfficeDocumentConverter` 协议和 `LibreOfficeHeadlessConverter`。Provider 路径
解析顺序：显式参数、`PROJECT_MANAGER_LIBREOFFICE_PATH`、PATH、平台标准安装
位置。子进程使用参数数组、超时和隐藏窗口，不使用 shell 拼接。

转换输出位于本机运行目录下的唯一临时目录；解析结束后删除。原文件只读，转换后
解析结果中的 source/evidence 仍指向原始 `.doc`。失败状态包括：

- `office_converter_unavailable`；
- `office_conversion_failed`；
- `office_conversion_timeout`；
- `office_conversion_output_missing`。

转换失败返回 `blocked`，不退化为无内容的成功结果。

## 12. 复核和反馈契约

先收集全部原始复核项，再统一调用 `normalize_review_queue()`。任何后续组件不得向
规范化列表追加未经规范化的项目。

`adversarial_verification_error` 获得明确默认契约：

- 非空 ID；
- 明确问题和错误证据；
- `retry_verification / defer / accept_risk` 决策；
- 默认建议 `retry_verification`。

`feedback_form.verification_verdict` 按顺序读取
`verification_verdict`、`overall_verdict` 和标准状态，不再为空。所有人工反馈只写
run artifact，不移动文件。

## 13. 错误、降级与安全状态

- 原生解析、OCR、MinerU、PageIndex、LLM、LibreOffice 均使用标准
  `success / needs_review / blocked / failed`；
- EasyOCR 被禁用时不能静默启用；
- PaddleOCR/RapidOCR 不可用时扫描件返回 `blocked`；
- MinerU 不可用时，普通有文字层 PDF 可退回标准解析并标记 `degraded=true`；
- 裁决所需 PageIndex 不可用时，业务关系不得变为 `ready`；
- LLM 不可用或响应不符合 Schema 时保留解析产物并返回 `blocked`；
- 任何 LLM 参与的业务关系在当前无 SQL 阶段至少为 `needs_review`；
- `execute_archive_plan(..., confirmed=False)` 必须保持无副作用；
- 本轮验收不得调用 `confirmed=True`。

## 14. 测试与验收

### 14.1 自动化测试

按 TDD 增加：

1. OCR Registry 只使用显式启用 Provider；
2. EasyOCR 默认禁用且不会隐式回退；
3. PaddleOCR/RapidOCR 探测、成功、缺依赖、模型错误和空文本状态；
4. MinerU 被注册为 DocumentParser 而不是 OcrProvider；
5. ProcessingPlanner 对原生 PDF、扫描 PDF、复杂 PDF、DOCX/XLSX 的选择矩阵；
6. 发票表头不能产生业务 `project_name` 或投标时间字段；
7. 发票行项目仍保留 item name 和 specification；
8. RetrievalService 使用候选项目后才查询 PageIndex；
9. LLM 裁决必须包含 Schema、证据、置信度和版本；
10. LLM/PageIndex 缺失时归档动作不能为 `ready`；
11. Settings 支持多个 source/archive StorageBinding，并保持运行目录节点本地；
12. legacy business_root 只生成兼容 binding，不成为唯一业务根；
13. 绝对路径只能映射到唯一 binding，零匹配或多匹配返回 blocked；
14. 源 binding 与目标 binding 独立，目标不唯一时保持 unresolved；
15. 临时测试构造不访问任何真实业务绑定；
16. 项目 PRD 在解析和归档阶段统一为项目治理文档；
17. 验证异常反馈项具有非空 ID、问题和决策；
18. `overall_verdict` 正确进入 feedback form；
19. LibreOffice 转换只写本机临时目录并在完成后清理；
20. `.doc` 解析证据仍指向原文件；
21. 转换器缺失、失败、超时和输出缺失返回稳定 blocked；
22. `confirmed=False` 不生成 `archive_result.json`；
23. 既有工具契约、归档门、治理和完整测试保持通过。

### 14.2 真实样本只读回归

使用 2026-07-25 首次试运行中的四个样本，排除 OFD：

- 项目 PRD Markdown；
- 电子发票 PDF；
- 扫描合同 PDF；
- 旧版 DOC。

验收条件：

- 四个源文件处理前后 SHA-256 一致；
- EasyOCR 未被调用；
- 扫描合同使用显式批准的 PaddleOCR/RapidOCR Provider，或在 Provider 未就绪时
  明确 blocked；
- 发票不产生业务项目名和投标时间；
- 发票业务关联引用合同甲方/乙方等检索证据；
- LLM 裁决产物通过 Schema；
- 旧版 DOC 经 LibreOffice 临时转换后进入标准解析；
- 项目 PRD 始终为项目治理文档；
- 反馈表没有空白项目；
- runtime artifact 位于本机；样本来源绑定为 SynologyDrive，但归档目标由独立
  ArchiveTargetResolver 解析，不假设 SynologyDrive 是唯一或默认目标；
- 所有候选动作保持 `needs_review` 或 `blocked`；
- 不生成实际归档结果，不移动文件。

## 15. 实施顺序

1. 多 StorageBinding、路径解耦、DocumentStore 路由与正式构造入口；
2. 发票 Schema、确定性信号和统一分类；
3. 受控业务上下文 Provider、RetrievalService 与按需 StructureIndex/PageIndex 查询；
4. LLM 结构化裁决、归档意图和归档就绪门；
5. 复核队列、反馈表和人工修正闭环；
6. 安全 CLI 与原生可读 PDF、DOCX、XLSX、Markdown、XML 的只读回归；
7. LibreOffice `.doc` 转换；
8. MinerU DocumentParser Provider 与 ProcessingPlanner；
9. 统一 OCR Port、Registry、选择策略与 EasyOCR 默认禁用；
10. PaddleOCR/RapidOCR Provider 探测和独立运行时边界；
11. 扫描件与全部样本的最终只读回归。

完成第 6 项即形成首个可用里程碑：非扫描文件可以完成解析、业务候选检索、LLM
结构化裁决、人工复核和安全归档计划；扫描件在此阶段必须明确返回 `blocked`，不得
隐式调用 EasyOCR。OCR 识别质量和 Provider 安装问题留到业务闭环可用后处理。

每个阶段先建立失败测试，再写最小实现，并保持现有契约兼容。外部 Provider 的真实
模型、网络和大型依赖测试必须显式启用，不成为默认快速测试的隐式依赖。

## 16. 完成定义

本设计完成不等于可以无人值守归档。完成定义是：

- OCR、MinerU、PageIndex 和 LLM 均通过显式 Provider/Service 边界接入；
- EasyOCR 不再隐式执行；
- 文件类型和业务归属不是纯函数最终决定，而是带检索证据的结构化裁决；
- 发票语义不会污染项目字段；
- SynologyDrive 仅作为可替换的 StorageBinding，不存在全局唯一业务根；
- 路径、转换和反馈问题被自动化测试覆盖；
- 相同真实样本只读回归满足第 14.2 节；
- 所有真实物理归档仍停在人工确认门前。
