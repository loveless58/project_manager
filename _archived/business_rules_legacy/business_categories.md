# Business Categories — 业务类别定义(跟 archive_files 协作)

本文档定义 5 大业务类别及其对应归档动作,作为 document_parse → archive_files 的协作契约。

## 业务类别 vs 归档 phase 对照表

| 业务类别 | 文档分类(category) | 归档 phase | 后续动作 |
|---|---|---|---|
| 商机获取 | 招标公告 | 项目投标 | 触发商机评估 + bid_files 解析 |
| 商机获取 | 报名材料 | 项目投标 | 记录报名状态 |
| 投标响应 | 投标文件 | 项目投标 | 投标产出归档 |
| 合同执行 | 合同文件 | 项目执行 | 合同归档 + ledger 写入 |
| 失败案例 | 招标公告(失败) | 项目弃标 / 项目丢标 | archive_files 判定 phase |

## 跟 archive_files 的协作契约

```
document_parse 输出:
  business_judgement.category = "招标公告" | "投标文件" | "合同文件" | "报名材料" | "其他"

archive_files 接收:
  作为辅助信号(archive_decision 的 inputs 之一)
  不是主决策(主决策在 archive_files 自己的 knowledge base)
```

详见 [`skills/archive_files/SKILL.md`](../../skills/archive_files/SKILL.md)。

## 数据来源

- 当前(v0.1.0): 5 类从 domain knowledge 总结(MVP 占位)
- 后续: 从历史 `archive_manifest.v1` + `document_parse.v1.business_judgement.category` 联合抽取高频映射,扩种到本表

## 关键不变量(硬规则)

1. **document_parse 不决定归档 phase** — 它只分类文档,phase 判定归 archive_files
2. **category 是辅助信号,不是 ground truth** — archive_files 可以基于其他信号(文件路径/项目目录/历史)覆盖 category
3. **新增 category 必须先文档化到本文件**,再改 skill 代码(数据驱动,避免硬编码漂移)
