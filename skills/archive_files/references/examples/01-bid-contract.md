# Example 01: 投标合同归档(标准 happy path)

## 场景

归档一个扫描件投标合同到对应项目目录。

## 输入

**文件**: `TS软件外包云泰智汇电子26722V.pdf`(5 页扫描件,150dpi,2.4MB)

**extracted**(来自 data_cleaning_tools.py):
```python
extracted = {
    "document_type": "扫描件",
    "extracted_text": "...喀什大学-国产化XDR-20260625503资产接入...",
    "fields": {
        "project_name": "云泰智汇电子",
        "subject_name": "云泰智汇电子",
        "contract_number": "20260625503",
    },
    "ocr": {
        "engine": "rapidocr",
        "quality": {"quality": "good", "mean_confidence": 0.9832}
    }
}
```

**ledger_result**:
```python
{"business_judgement": {"human_review_required": False}}
```

**project_files_dir**: `/Users/zhang/Desktop/工作文件/项目文件/项目投标`

## 调用

```python
from skills.archive_files.scripts.build_archive_decision import build_archive_action

action = build_archive_action(
    run_id="run-2026-07-17-001",
    source_file="/Users/zhang/Desktop/工作文件/TS软件外包云泰智汇电子26722V.pdf",
    project_name="云泰智汇电子",
    extracted=extracted,
    ledger_result={"business_judgement": {"human_review_required": False}},
    project_files_dir="/Users/zhang/Desktop/工作文件/项目文件/项目投标",
)
```

## 决策路径(Three-Layer Fallback)

1. **Layer 1 知识库**: `business_knowledge/归档规则_v1.md` 当前是种子,无 "TS 软件外包" 相关规则
   - kb_result.reason = `knowledge_base_no_match`
2. **Layer 2 硬编码**: `evaluate_archive_decision()`
   - source_file 路径无 phase 段
   - `extracted.fields.project_name = "云泰智汇电子"` 有值
   - archive_phase = "项目投标" (默认)
   - confidence = 0.45 (有 project_name + 非项目丢标)
3. **Layer 3 LLM 兜底**: kb 是 no_match → 触发 LLM
   - llm_result.phase = "项目投标"(假设 LLM 推断正确)
   - 最终: reason=success, llm_fallback_used=True

## 输出 archive_action.v1

```json
{
  "schema_version": "archive_action.v1",
  "run_id": "run-2026-07-17-001",
  "status": "ready",
  "source_file": "/Users/zhang/Desktop/工作文件/TS软件外包云泰智汇电子26722V.pdf",
  "project_name": "云泰智汇电子",
  "document_type": "扫描件",
  "proposed_name": "TS软件外包云泰智汇电子26722V.pdf",
  "target_dir": "/Users/zhang/Desktop/工作文件/项目文件/项目投标/云泰智汇电子/原始文件",
  "target_path": "/Users/zhang/Desktop/工作文件/项目文件/项目投标/云泰智汇电子/原始文件/TS软件外包云泰智汇电子26722V.pdf",
  "blockers": [],
  "business_judgement": {"human_review_required": false},
  "archive_decision": {
    "subject_type": "bid_project",
    "subject_name": "云泰智汇电子",
    "archive_phase": "项目投标",
    "document_type": "扫描件",
    "confidence": 0.45,
    "target_dir": "...",
    "target_path": "...",
    "blockers": [],
    "human_review_required": false,
    "reasons": ["fallback_bid_project_policy"]
  },
  "reason": "success",
  "knowledge_base_used": false,
  "pageindex_query_id": null
}
```

## 调用方处理

```python
assert action["reason"] == "success"
assert "source_missing" not in action["blockers"]
assert "target_exists" not in action["blockers"]
# status == "ready", 可执行归档
```

## 关键字段验证(人工核对)

实际 OCR 抽取到的字段(用于确认项目名提取正确):
- 合同名: 喀什大学-国产化XDR-20260625503资产接入功能定制服务合同
- 甲方: 新疆云泰智汇电子科技有限公司
- 乙方: 北京华胜天成科技股份有限公司
- 联系人: 张大师(zhangds@yuntaie.com)
- 签署日期: 2026年7月15日

**项目名推断**: extracted.fields.project_name = "云泰智汇电子"(从甲方简化),如果未来要更精确可改为 "新疆云泰智汇电子科技有限公司"。
