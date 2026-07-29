# DocumentManagementAgent

第一个正式工作流 Agent：接收明确文件列表和目标，维护运行状态，按解析结果渐进式选择 Skill，并交付外部可审查产物。

工作流固定为：`document_parse` →（仅发票）`document_facts` → `document_review`。每份输入都会得到结果；失败结果保留失败原因并强制 `no_archive`。

它只通过 Connector 访问源绑定、SQLite 和结果根目录。当前不加载 `BusinessQuerySkill`、`ArchiveSkill` 或 PageIndex，不扫描根目录，不移动、复制、重命名、删除或覆盖源文件。
