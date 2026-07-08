"""字段契约 — 跨包共享的字段配置。

阶段 1：从 tools/adversarial_verification/reviewer.py + runner.py 机械抽取。
阶段 2：扩展 FIELD_TYPES / FIELD_CONSTRAINTS / FIELD_SENSITIVITY（业务层）。

来源历史：
- REQUIRED_FIELDS 原位于 tools/adversarial_verification/reviewer.py L40-46
- REVIEW_DIMENSIONS 原位于 tools/adversarial_verification/reviewer.py L31-38
- OCR_CONFIDENCE_THRESHOLD 原位于 tools/adversarial_verification/runner.py L58
"""


# ── 审查维度（Reviewer 用） ──────────────────────────────────────────

REVIEW_DIMENSIONS = [
    "field_completeness",          # 字段完整性：必填字段是否全部提取
    "field_accuracy",              # 字段准确性：值是否与原文一致
    "document_classification",     # 文档分类：classify_document 是否匹配内容
    "archive_plan",                # 归档合理性：目标路径与文档类型是否匹配
    "cross_doc_consistency",       # 跨文档一致性（V1.1+）
]


# ── 文档类型枚举 ──────────────────────────────────────────────────────

DOCUMENT_TYPES = ["投标文件", "采购公告", "合同", "未分类"]


# ── 必填字段配置（按文档类型） ────────────────────────────────────────
#
# 业务含义（从代码注释与历史提取）：
# - 投标文件 / 采购公告：必须有 project_name + budget + customer + deadline
# - 合同：必须有 project_name + customer + contract_status
# - 未分类：至少有 project_name

REQUIRED_FIELDS = {
    "投标文件": ["project_name", "budget", "customer", "deadline"],
    "采购公告": ["project_name", "budget", "customer", "deadline"],
    "合同": ["project_name", "customer", "contract_status"],
    "未分类": ["project_name"],
}


# ── OCR 置信度阈值（R2 缓解用） ────────────────────────────────────────
#
# 业务含义（来自 reviewer.py 原始注释）：
# 低于此值直接跳过对抗验证，升级为 needs_human_review。

OCR_CONFIDENCE_THRESHOLD = 0.60
