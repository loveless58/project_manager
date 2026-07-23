# Document Classification Rules — 文档分类规则知识库(L1)

本文档定义 document_parse skill 的文档分类规则,从原 `skills/document_parse/parse.py:_hardcoded_classify` 硬编码迁出。

## 5 大类

| 类别 | 触发关键词(任一) | 置信度 | 典型文件 |
|---|---|---|---|
| 招标公告 | `招标公告`, `采购公告`, `公开招标` | high | 政府/企业发布的招标信息 |
| 投标文件 | `投标文件`, `投标书`, `投标响应` | high | 投标人提交的响应文件 |
| 合同文件 | `合同` + (`签订` / `签署` / `签订合同`) | medium | 合同文本(需复合条件) |
| 报名材料 | `报名`, `报名表`, `登记` | medium | 报名/登记材料 |
| 其他 | (无关键词命中) | medium | 默认 fallback |

## 触发逻辑

```
1. raw_text 包含任一关键词 → 类别命中
2. 多类都命中 → 取第一个(high 优先于 medium)
3. 未命中 → "其他" + confidence=medium
4. rule_source 默认 "hard_code" → 后续 KB 接入后改 "knowledge_base"
```

## 真实样本驱动(渐进式)

当前(v0.1.0): 5 类从硬编码迁出(MVP 占位,5 个关键词组)。

后续路径(数据驱动):
1. 收集 10+ 真实 `document_parse.v1` 样本
2. 按 `business_judgement.category` 分组,统计高频关键词
3. 把高频(≥3 次)关键词追加到本表
4. confidence 等级按命中率动态调整

## 跟硬编码的关系

| 层 | 来源 | 当前状态 |
|---|---|---|
| L1(知识库) | 本文件 + `business_rules/document_parse/*.md` | TODO: 待 KB reader 实现 |
| L2(硬编码) | `skills/document_parse/parse.py:_hardcoded_classify` | **当前 default** |
| L3(LLM) | `common.llm_adapter` | TODO: 待 LLM 接入 |

L2 跟 L1 保持同步(改 KB 必须同步改硬编码),保证 fallback 不漂移。

详见 [`skills/document_parse/SKILL.md §Three-Layer Fallback`](../../skills/document_parse/SKILL.md)。
