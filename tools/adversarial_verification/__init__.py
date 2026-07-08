"""Adversarial Verification Package — 对抗性验证包（PRD 阶段 1：纯 Python 实现）。

5-Agent 三方对抗验证循环:

- Reviewer (reviewer.py): 审查提取结果（4 个审查维度 + 跨文档一致性 V1.1+）
- Devil's Advocate (devils_advocate.py): 提出系统性反驳（5 种反驳策略）
- Defender (defender.py): 原始证据辩护（缓解 R1：能读原始文档）
- Runner (runner.py): 主调度循环 + 归档门控检查（缓解 R2/R7/R10）
- Error Collector (error_collector.py): 错误案例收集（缓解 R3）

Risk Mitigations（5 条贯穿全部 Agent）:
- R1: Defender 能读原始文档
- R2: OCR 低置信度时升级人工复核
- R3: 错误案例永久保留
- R7: 归档阻断消息含可读 finding 摘要
- R10: 验证报告按月份归档

实现方式: 纯 Python（json / re / typing / datetime），无 LLM 调用。
保持与 DataCleaningTools 一致的技术栈。

Roadmap:
- 阶段 2: 叠加 LLM agent 实现层（双轨 + feature flag，AV_MODE=pure_python|llm|dual）
- 阶段 3: 淘汰纯代码 fallback（6 个月 deprecation 缓冲）

Backward Compatibility:
原有的 `from tools.adversarial_verification import ReviewerAgent` 之类的导入
仍然可用（通过 __all__ 显式导出）。
"""
from .defender import DefenderAgent
from .devils_advocate import DevilsAdvocateAgent
from .error_collector import ERROR_TYPES, ErrorCaseCollector
from .reviewer import REQUIRED_FIELDS, REVIEW_DIMENSIONS, ReviewerAgent
from .runner import OCR_CONFIDENCE_THRESHOLD, AdversarialVerification, check_archive_gate


__all__ = [
    # 常量
    "REVIEW_DIMENSIONS",
    "REQUIRED_FIELDS",
    "OCR_CONFIDENCE_THRESHOLD",
    "ERROR_TYPES",
    # Agent 类
    "ReviewerAgent",
    "DevilsAdvocateAgent",
    "DefenderAgent",
    # 调度器
    "AdversarialVerification",
    "check_archive_gate",
    # 错误案例
    "ErrorCaseCollector",
]
