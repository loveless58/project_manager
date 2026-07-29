# document_parse

只在 Agent 已收到明确文件输入并完成只读存储绑定后加载。该 Skill 是现有 native-first `DocumentParseSkill` 的工作流适配器：优先本地解析，必要时按已配置的 OCR 链回退，统一输出 `structured_document.v1`。

它不扫描业务根目录、不移动文件、不判断项目归属，也不写入结果目录。解析失败仍返回可审查的 `needs_review` 结构，因此后续 `document_review` 可以交付失败原因。
