# document_review

在解析完成（包括 `needs_review`）后加载。它将解析结果与已提取的候选事实装配为 `document_result.v1`，用于外部 `document.json` 和 `review.md`。

该阶段强制 `suggested_action=no_archive`：不查询 PageIndex、项目库或归档位置，不生成移动、复制、重命名或删除指令。解析失败会保留原始失败原因，以便人工审查。
