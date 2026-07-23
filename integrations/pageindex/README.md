# PageIndex Integration

通用 PageIndex 客户端,封装 [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex)(本地 fork 复制,含 prompt injection 安全加固)。

## INSTALL — 克隆 project_manager 后必做

> ⚠️ PageIndex 是**外部依赖**,不在 project_manager git 里。
> 克隆 project_manager 后必须额外操作,否则 `pageindex_client.index_pdf()` / `index_md()` 会失败。

### 步骤 1:clone PageIndex repo 到约定路径

```bash
cd /Users/zhang/Desktop/工作文件/
git clone https://github.com/VectifyAI/PageIndex.git
# 或者用我们已有的本地 fork(有 prompt injection 安全加固):
# 路径:/Users/zhang/Desktop/工作文件/PageIndex/
# remote URL: git@github.com:loveless58/PageIndex.git(待配)
```

### 步骤 2:PageIndex venv + 依赖

```bash
cd /Users/zhang/Desktop/工作文件/PageIndex
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

依赖: `litellm` / `pymupdf` / `PyPDF2` / `python-dotenv` / `pyyaml`

### 步骤 3:PageIndex LLM 配置(.env + config.yaml)

```bash
cat > /Users/zhang/Desktop/工作文件/PageIndex/.env << 'EOF'
OPENAI_API_BASE=http://172.18.125.202:9990/v1
OPENAI_API_KEY=<your-gpustack-key>
EOF

# config.yaml 里 model 字段改成:
# model: "openai/minimax-m3-mxfp8"
```

### 步骤 4:验证 PageIndex 可调通

```bash
cd /Users/zhang/Desktop/工作文件/PageIndex
.venv/bin/python run_pageindex.py --pdf_path /path/to/test.pdf
# 输出到 ./results/{pdf_name}_structure.json 即 OK
```

### 步骤 5:验证 project_manager ↔ PageIndex 联通

```bash
cd /Users/zhang/Desktop/工作文件/project_manager
/opt/homebrew/bin/python3.11 -c "
from integrations.pageindex.pageindex_client import PageIndexClient
c = PageIndexClient()
r = c.index_md('business_rules/document_parse/kb.md')
print(r['status'], len(r['structure']))
"
```

预期: `success` + 至少 1 个顶层节点(实际 7 个子节点: 招标公告/投标文件/合同文件/报名材料/其他/优先级不变量/LLM fallback)。

## 决策记录

- **2026-07-23**: 尝试用 `git submodule add` 把 PageIndex 集成入 project_manager,**失败**(git over HTTPS 超时,网络层可达但 git 协议层不可达)。改用 INSTALL.md 外部依赖方案。
- **未来增强**:
  - 如果网络恢复 → 改 `git submodule add https://github.com/VectifyAI/PageIndex.git integrations/pageindex/PageIndex`
  - 或者 fork VectifyAI/PageIndex 到 loveless58/PageIndex,submodule 自己的 fork(完全可控)

## 用途

把 PDF / Markdown 文档生成层级化 tree 结构(TOC + 各章节 summary + page_index 范围),供下游 skill 消费做精细化决策。

## 安装

### 1. PageIndex 仓库

`/Users/zhang/Desktop/工作文件/PageIndex/`(本地 fork 复制,git history 已丢失,remote URL 未配)。

如果首次安装,需要在 PageIndex 目录下创建 venv:

```bash
cd /Users/zhang/Desktop/工作文件/PageIndex
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

依赖:`litellm` / `pymupdf` / `PyPDF2` / `python-dotenv` / `pyyaml`

### 2. LLM 配置

PageIndex 需要 LLM endpoint。修改 `PageIndex/pageindex/config.yaml`:

```yaml
model: "openai/minimax-m3-mxfp8"   # 或其他 litellm 支持的模型
```

并在 `PageIndex/.env` 写入:

```
OPENAI_API_BASE=http://172.18.125.202:9990/v1
OPENAI_API_KEY=<your-key>
```

> 当前使用 GPUStack + minimax-m3-mxfp8。环境不通时 `index_pdf()` 会返回 `failed`。

### 3. project_manager 依赖

`pageindex_client.py` 用 PyPDF2 / PyMuPDF(项目主环境已装)。

```python
PyPDF2==3.0.1
PyMuPDF==1.26.4
```

## 调用示例

```python
import sys
sys.path.insert(0('/Users/zhang/Desktop/工作文件/project_manager/integrations/pageindex'))
from pageindex_client import PageIndexClient

client = PageIndexClient()

# 1. 索引 PDF (subprocess 调 PageIndex, 50 页约 55-65s)
result = client.index_pdf("/path/to/contract.pdf")
if result["status"] == "success":
    print(f"doc_name: {result['doc_name']}")
    print(f"elapsed: {result['elapsed_seconds']}s")
    print(f"顶层节点: {len(result['structure'])}")
    for n in result["structure"]:
        print(f"  - {n['title']} (pages {n['start_index']}-{n['end_index']})")

# 2. 按页码范围读 PDF 内容 (纯 Python, 几秒)
pages = client.get_page_content("/path/to/contract.pdf", "5-7")
for p in pages:
    print(f"=== Page {p['page']} ===\n{p['content']}\n")

# 3. 按章节标题找节点
matches = client.find_nodes_by_title(result, "签约")
for m in matches:
    print(f"  {m['node_id']}: {m['title']} (pages {m['start_index']}-{m['end_index']})")
```

## 文件清单

| 文件 | 用途 |
|---|---|
| `pageindex_client.py` | 客户端主实现(3 个核心 API) |
| `SKILL.md` | 契约 + 工作流(下游消费契约) |

## CLI 直接调用(开发调试用)

跳过客户端,直接用 PageIndex CLI:

```bash
cd /Users/zhang/Desktop/工作文件/PageIndex
.venv/bin/python run_pageindex.py --pdf_path /path/to/file.pdf
```

输出到 `./results/{pdf_name}_structure.json`。

## 已知问题

| 问题 | 临时方案 | 后续增强 |
|---|---|---|
| 扫描件 PDF get_page_content 返回空 | 用 OCR 先识别再调 PageIndex | get_page_content 自动 fallback 到 OCR (swift_ocr_bridge) |
| PageIndex subprocess 启动开销 ~200ms | 接受(单次 index 几十秒) | 改 daemon 长驻进程 |
| PageIndex 仓库 git history 丢失 | 接受(本地 fork 复制) | 重新 git clone 上游,然后 cherry-pick prompt injection 改动 |

## 决策历史

- **2026-07-23**: 初版落地,作为横向 infrastructure,被所有 skill 消费
