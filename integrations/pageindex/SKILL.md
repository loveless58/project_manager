---
name: pageindex
description: |
  使用显式配置的 PageIndex 外部运行时，把 PDF 或 Markdown 转换为包含章节标题、
  摘要和物理页码范围的结构树；用于长文档章节定位与局部读取，不承担 OCR、归档决策
  或权威业务状态存储。
---

# PageIndex Integration Skill

## 使用条件

适合以下请求：

- 获取长 PDF/Markdown 的章节结构或章节摘要；
- 定位关键章节所在物理页；
- 读取一个结构节点对应的局部页面；
- 按标题查询已经生成的结构树。

不适合以下请求：

- 扫描件或图片 OCR；
- 普通短文档的整篇文本提取；
- 项目归档决策；
- SQL 权威状态、记忆或文件物理存储。

## 配置边界

PageIndex 是节点侧可选能力。安装目录必须来自 `PROJECT_MANAGER_PAGEINDEX_DIR` 或 `AppSettings.providers.pageindex_dir`，不能由 Skill 猜测，也不能写死为某台机器的路径。

```python
import os

from integrations.pageindex.pageindex_client import PageIndexClient

configured_dir = os.environ["PROJECT_MANAGER_PAGEINDEX_DIR"]
client = PageIndexClient(configured_dir)
client.check_environment()
```

使用 `AppSettings`：

```python
from integrations.pageindex.pageindex_client import PageIndexClient
from platform_core.settings import load_app_settings

settings = load_app_settings()
configured_dir = settings.providers.pageindex_dir
if configured_dir is None:
    raise RuntimeError("PageIndex is not enabled for this node")
client = PageIndexClient(configured_dir)
```

LLM endpoint 由节点环境显式注入。文档示例只能使用占位符：

```bash
export PROJECT_MANAGER_LLM_BASE_URL="https://llm.example.invalid/v1"
```

该占位符不可路由；实际 endpoint 和 key 不得提交到仓库。

## 稳定输出契约

```json
{
  "status": "success | failed",
  "engine": "pageindex",
  "doc_name": "document.pdf",
  "doc_id": "generated-id",
  "structure": [
    {
      "title": "Chapter",
      "node_id": "0001",
      "start_index": 1,
      "end_index": 4,
      "summary": "...",
      "nodes": []
    }
  ],
  "structure_json_path": "/runtime/pageindex/results/document_structure.json",
  "elapsed_seconds": 12.3,
  "error": "only present on failure"
}
```

不变量：

- `start_index` / `end_index` 是 1 起始的 PDF 物理页码；
- `node_id` 是字符串；
- `structure` 是树；
- 外部执行失败返回稳定失败结果，不能泄露节点工具路径；
- `structure.json` 是派生索引，不是第二权威源。

## 工作流

```text
确认 AppSettings 已启用 pageindex
  -> probe/check_environment
  -> index_pdf 或 index_md
  -> find_nodes_by_title
  -> 按需 get_page_content
  -> 下游 Skill 消费结构化结果
```

查询优先级：

1. 先在 `structure` 中按标题定位；
2. 需要正文证据时只读取节点页码范围；
3. 检测到无文字层的扫描件时交回 OCR/document_parse 路由。

## 实现资源

- [README.md](README.md)：跨平台安装、显式配置和验证步骤。
- [pageindex_client.py](pageindex_client.py)：客户端及稳定失败边界。
- [structure_index.py](structure_index.py)：平台 `StructureIndex` 适配器。
- 外部 PageIndex 仓库位置：由 `PROJECT_MANAGER_PAGEINDEX_DIR` 指定。

## 限制

- 外部 PageIndex 的安装、模型和网络由每个执行节点独立维护；
- 扫描件必须先由 OCR 能力处理；
- 外部执行时间随文档长度和 provider 性能变化；
- 真实集成测试必须显式启用，普通快速测试不会访问外部服务。
