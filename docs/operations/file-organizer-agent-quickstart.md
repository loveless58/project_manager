# 文件整理 Agent：本机试运行

本 Agent 的第一阶段仅需要节点本地 SQLite；不要把 SQLite、备份、运行工作区放到 SynologyDrive、NAS 或任意同步目录中。源文件和归档目录通过显式 StorageBinding 配置，可以位于本机、macOS 挂载目录或其他当前节点可访问的位置。

1. 安装基础解析依赖：`python -m pip install -r requirements.txt`。
2. 如需本地 OCR，按节点能力安装：`python -m pip install -r requirements-ocr.txt`。Provider 顺序固定为 RapidOCR、MinerU、EasyOCR；任何 Provider 不会在运行时自行安装、下载模型或向外发送文件。
3. 复制 `config/file-organizer.example.json` 为**节点本地、未提交**的配置，填写节点本地 SQLite 路径和两个 StorageBinding。源绑定应为只读，归档绑定才可写。
4. 首次使用前通过 `python -m infrastructure.database.cli --database <node-local-sqlite> migrate --backup-dir <node-local-backups>` 显式创建备份并执行迁移。
5. 首轮请使用 `prepare-review`：配置中必须填写 `results_root`，它必须位于 Git 仓库和所有源 StorageBinding 之外。该目录存放运行产物，不能设为 SynologyDrive 源目录或归档目录。

```powershell
python -X utf8 -B scripts/run_file_organizer.py --config <node-local-config.json> prepare-review --goal "解析并生成审查结果，不归档" <file-1> <file-2>
```

该命令只处理明确输入，依次按需加载解析、发票事实和审查 Skill；它会在 `<results_root>/runs/<run-id>/` 生成 `document.json`、`review.md`、`run.json` 和 `run-summary.md`，并向 SQLite 写入逻辑 URI、哈希和大小。它不会扫描目录、查询项目归属、调用 PageIndex、移动、复制、重命名、删除或覆盖源文件。

输出使用逻辑 URI 而不是物理路径。`prepare`/`execute-confirmed` 是旧兼容工作流；检查建议后，才在节点本地创建确认文件（一个数组），例如：

```json
[{"item_id":"<item-id>","decision":"confirmed"}]
```

随后才可以执行：

```powershell
python -X utf8 -B scripts/run_file_organizer.py --config <node-local-config.json> execute-confirmed --run-id <run-id> --confirmations <node-local-confirmations.json>
```

`prepare` 不改动任何文件。`execute-confirmed` 会对每个确认条目校验源内容哈希、拒绝覆盖目标、移动后回读，再写入 SQLite 位置历史。真实 SynologyDrive 的首轮验证只运行 `prepare`，不提供确认文件。
