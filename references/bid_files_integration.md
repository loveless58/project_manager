# bid-files 集成说明

## 定位

bid-files 是项目文件处理能力的兼容脚本集合。Project Manager Agent 负责意图路由、审批、运行边界和结果汇总；兼容脚本只执行显式允许的文件操作，不拥有独立的目录默认值。

所有目录均从 `AppSettings` 派生：

- `business_root`：业务输入与项目文件根，可由本机挂载或受控同步目录提供；
- `runtime_workspace`：日志、trace、临时文件、脚本输出和中间状态的节点本地根；
- `projection_root`：可重建投影的节点本地根；
- `common.workspace_config`：仅供旧调用方使用的兼容门面。

`runtime_workspace` 和 `projection_root` 不得等于或位于 `business_root` 下，也不得使用 UNC、SMB、NFS 或 AFP 网络位置。Windows 盘符是否为映射盘需要部署时额外确认。

## 适用能力

| 能力 | 调用方式 | 触发条件 |
| --- | --- | --- |
| 项目索引读取 | 工具层优先，脚本兼容 | 查询项目清单或基本信息 |
| 报名/投标材料处理 | 审批后的脚本调用 | 输入目录和目标项目均已明确 |
| 合同与交付物核对 | 工具层或允许列表脚本 | 只读检查或已批准写入 |
| 项目文件归档 | 生成计划后执行 | `confirmed=True` 且回读验证通过 |

兼容脚本不得绕过 `ArchivePlan`、人工确认、源文件可读性检查和目标回读。

## 配置

默认配置来源是 `platform_core.settings.load_app_settings`。脚本目录可通过节点本地环境变量显式覆盖；未覆盖时从业务根下的兼容目录解析。

```python
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from platform_core.settings import load_app_settings


settings = load_app_settings()
if settings.business_root is None:
    raise RuntimeError("bid-files requires a configured business_root")

business_root = settings.business_root
runtime_root = settings.runtime_workspace / "bid-files"
script_root = Path(
    os.environ.get(
        "PROJECT_MANAGER_BID_FILES_DIR",
        str(business_root / "项目文件" / ".oa-manager" / "scripts"),
    )
).expanduser().resolve()
```

`runtime_root` 用于 stdout/stderr 摘要、trace、临时转换结果和失败诊断；业务源文件仍位于 `business_root`。兼容脚本不得在业务目录中创建数据库、缓存或运行日志。

## 安全调用约定

脚本名必须来自静态允许列表，不能直接使用用户输入拼接路径；调用禁用 shell，设置超时并在业务根作为只读上下文启动。

```python
ALLOWED_SCRIPTS = {
    "project_index": "00_project_index.py",
    "registration_materials": "01_registration_materials.py",
    "deliverable_check": "01d_deliverable_manager.py",
}


def run_bid_script(capability: str, *args: str) -> subprocess.CompletedProcess[str]:
    script_name = ALLOWED_SCRIPTS.get(capability)
    if script_name is None:
        raise ValueError(f"unsupported bid-files capability: {capability}")

    script_path = (script_root / script_name).resolve()
    script_path.relative_to(script_root)
    runtime_root.mkdir(parents=True, exist_ok=True)

    return subprocess.run(
        ["python3", str(script_path), *args],
        cwd=str(business_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
        env={
            **os.environ,
            "PROJECT_MANAGER_RUNTIME_DIR": str(runtime_root),
        },
    )
```

## 返回码处理

| 返回码 | 含义 | Agent 行为 |
| --- | --- | --- |
| 0 | 成功 | 校验结构化结果并继续 |
| 1 | 输入错误 | 请求补充缺失信息 |
| 2 | 文件处理失败 | 写入节点本地诊断并进入复核 |
| 3 | 项目未找到 | 返回结构化 `not_found`，不猜测项目 |
| 其他 | 未知错误 | 记录节点本地 trace，返回脱敏摘要 |

错误摘要不得包含凭据、个人目录或完整业务文件内容。需要保留的业务证据应使用稳定文档 ID、逻辑 URI 和内容哈希。

## 目录契约

```python
PROJECT_FILES_DIR = business_root / "项目文件"
INDEX_PATH = PROJECT_FILES_DIR / "index.json"
RUNTIME_OUTPUT_DIR = runtime_root / "outputs"
RUNTIME_TRACE_DIR = runtime_root / "traces"
```

旧环境变量 `LOOP_PROJECT_BASE_DIR` 仅由兼容门面读取，不应出现在新集成代码中。新部署使用：

- `PROJECT_MANAGER_BUSINESS_ROOT` 配置业务根；
- `PROJECT_MANAGER_WORKSPACE_DIR` 配置节点本地运行根；
- `PROJECT_MANAGER_BID_FILES_DIR` 可选配置兼容脚本目录。

## 渐进式替代

1. **编排阶段**：Agent 通过允许列表调用兼容脚本，统一审批和 trace。
2. **内联阶段**：把高频脚本逻辑迁移到类型化工具接口，消除重复目录解析。
3. **收敛阶段**：兼容脚本只保留迁移入口；数据库、缓存和投影始终由平台层节点本地基础设施管理。

任何阶段都不得让脚本目录成为新的配置源，也不得把业务根当作运行工作区。
