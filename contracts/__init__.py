"""Field Contracts — 跨 reviewer / tools / loop_packages 共享的字段契约。

本目录是数据契约层（Data Contract Layer），区别于：
- governance/：治理契约（目录结构 / 项目账本 schema / 校验器）
- business_rules/：业务规则（具体实现，如 bid_project_rules.py）
- ocr/：OCR 结果 schema

数据契约 vs 业务规则：
- 数据契约：字段是什么 / 必填哪些 / 类型是什么（机械性）
- 业务规则：字段值合规与否 / 业务敏感度（领域性，留给 hard_gates/）

阶段 1（机械抽取）：从 adversarial_verification 包内抽取常量
阶段 2（扩样本 + 业务硬门控）：在 contracts/fields.py 基础上扩展
"""
