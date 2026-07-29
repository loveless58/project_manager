# document_facts

在 `document_parse` 已返回可用文本后加载。本技能当前只识别电子或增值税发票的可核验事实：发票号码、开票日期、购销双方、价税合计和服务名称。

输出字段均为 `{value, confidence, evidence}`；证据只来自解析后的页文本，绝不使用文件名作为证据。缺失字段保持缺失，不以零值或项目归属补全。

本技能不查询 PageIndex、数据库或业务项目，不决定归档位置，也不修改源文件。非发票或解析失败时只返回 `document_type=unknown`，由 Agent 继续生成可人工审查的交付物。
