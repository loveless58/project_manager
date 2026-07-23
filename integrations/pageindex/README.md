# PageIndex Integration

该目录提供 `PageIndexClient` 与 `PageIndexStructureIndex`，把外部 [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex) 能力接入项目。PageIndex 是可选外部依赖，不随本仓库安装，也没有机器级默认路径。

## 安装与节点配置

在每个需要执行 PageIndex 的 Windows 或 macOS 节点上，把 PageIndex 克隆到该节点自己的工具目录并安装其依赖：

```bash
git clone https://github.com/VectifyAI/PageIndex.git /path/to/pageindex
cd /path/to/pageindex
python -m venv .venv
```

Windows：

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PROJECT_MANAGER_PAGEINDEX_DIR = (Get-Location).Path
$env:PROJECT_MANAGER_LLM_BASE_URL = "https://llm.example.invalid/v1"
```

macOS：

```bash
.venv/bin/python -m pip install -r requirements.txt
export PROJECT_MANAGER_PAGEINDEX_DIR="$PWD"
export PROJECT_MANAGER_LLM_BASE_URL="https://llm.example.invalid/v1"
```

PageIndex 自身使用的模型名、API key 和兼容端点应按其上游文档配置。示例域名只是不会被路由的占位符，不能直接用于生产。不要把真实 endpoint、密钥或节点路径写进仓库。

## AppSettings 配置

启用结构索引时需要同时选择 provider 并显式提供安装目录：

```powershell
$env:PROJECT_MANAGER_STRUCTURE_INDEX = "pageindex"
$env:PROJECT_MANAGER_PAGEINDEX_DIR = "C:\Tools\PageIndex"
```

也可以在被忽略的 `config/project-manager.local.json` 中设置：

```json
{
  "providers": {
    "structure_index": "pageindex",
    "pageindex_dir": "/path/to/pageindex"
  }
}
```

## Python 调用

直接使用客户端时，构造函数必须接收显式目录：

```python
import os

from integrations.pageindex.pageindex_client import PageIndexClient

configured_dir = os.environ["PROJECT_MANAGER_PAGEINDEX_DIR"]
client = PageIndexClient(configured_dir)

result = client.index_pdf("/path/to/contract.pdf")
if result["status"] == "success":
    for node in result["structure"]:
        print(node["title"], node["start_index"], node["end_index"])
```

通过应用配置调用：

```python
from integrations.pageindex.pageindex_client import PageIndexClient
from platform_core.settings import load_app_settings

settings = load_app_settings()
configured_dir = settings.providers.pageindex_dir
if configured_dir is None:
    raise RuntimeError("PageIndex is not enabled for this node")
client = PageIndexClient(configured_dir)
```

环境检查是显式能力探测：

```python
client.check_environment()
```

构造客户端不会启动外部进程；`check_environment()`、`index_pdf()` 和 `index_md()` 在真正使用 PageIndex 时才检查解释器和 CLI。页内容读取及结构树查询不依赖 PageIndex CLI。

## 稳定输出

索引结果使用 `pageindex_result.v1` 形状：

```json
{
  "status": "success",
  "engine": "pageindex",
  "doc_name": "contract.pdf",
  "doc_id": "generated-id",
  "structure": [],
  "structure_json_path": "/runtime/pageindex/results/contract_structure.json",
  "elapsed_seconds": 12.3
}
```

失败结果保留相同边界并提供稳定的 `error`，不泄露本机绝对工具路径。

## API 与处理边界

- `index_pdf(pdf_path)`：调用外部 PageIndex，生成 PDF 章节树。
- `index_md(md_path)`：调用外部 PageIndex，生成 Markdown 章节树。
- `get_page_content(pdf_path, pages)`：纯 Python 读取指定物理页。
- `find_nodes_by_title(index_result, keyword)`：在既有结构树中查询标题。
- 扫描件没有文字层时应先走 OCR/document_parse 策略，不由 PageIndex 隐式切换 OCR。
- `structure.json` 是可重建索引产物，不是 SQL 权威状态或原始文件存储。

## 验证

快速单元测试不会要求本机已安装 PageIndex。真实集成测试必须由操作者显式提供受控样本和下列变量：

```text
PAGEINDEX_RUN_SLOW_TESTS=1
PAGEINDEX_TEST_DIR=/path/to/pageindex
PAGEINDEX_TEST_PDF=/path/to/sample.pdf
PAGEINDEX_TEST_CACHE=/path/to/sample_structure.json
PAGEINDEX_TEST_SCAN_PDF=/path/to/scan.pdf
```

随后运行：

```bash
python -X utf8 -B -m pytest -q tests/test_pageindex_client.py
```
