# Docx Field Patterns — Word 文档业务字段抽取模式(L1)

本文档定义 docx_executor 提取后,skill 层如何从 `raw_data` 抽取业务字段(MVP 占位)。

## 字段清单(待真实样本驱动扩种)

| 字段 | 来源 | 模式(正则) | 状态 |
|---|---|---|---|
| `project_name` | paragraphs | `项目名称[:：]\s*(.+)` | TODO |
| `bid_deadline` | paragraphs | `截止时间[:：]\s*(.+)` / `开标时间[:：]\s*(.+)` | TODO |
| `amount` | paragraphs/tables | `金额[:：]\s*(.+)` / `预算[:：]\s*(.+)` | TODO |
| `purchaser` | paragraphs | `采购人[:：]\s*(.+)` | TODO |
| `bidder` | paragraphs | `投标人[:：]\s*(.+)` | TODO |

## MVP 实现状态

`_apply_business_rules` 当前只返回 `category`,`extracted_fields={}`(空)。

下一步(Step 6+):
1. 把上表 5 个字段的**正则模式**写入本文档(已完成)
2. 在 `_apply_business_rules` 加 `_extract_business_fields(raw_data, rules)`
3. L1 读 KB → L2 用本文档正则 → L3 LLM 兜底

## 数据来源(待真实样本)

**等真实样本扩种**:
- 当前工作目录: `/Users/zhang/Desktop/工作文件/项目投标/*/原始文件/*.docx`(如有)
- 数据源: `document_parse.v1.business_judgement.extracted_fields`
- 抽取流程: 跟 `skills/archive_files/references/knowledge-base-pattern.md` 一致

## LLM 抽取边界(防幻觉)

`_extract_business_fields` 必须遵循:
- **只**抽取 regex 能 match 的字段,不允许"自由发挥"
- regex 不命中 → 字段值=`null`,不返回猜测值
- LLM(L3)只能**补充** regex 漏掉的字段,不能**覆盖** regex 已抽取的值

详见 [`skills/document_parse/SKILL.md §Critical Requirements`](../../skills/document_parse/SKILL.md)。
