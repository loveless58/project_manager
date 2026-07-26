"""Shared project-management data contracts.

This package is the data-contract layer used by tools, reviewers, and loop
packages. Governance schemas live under ``governance/``; domain decisions live
under ``business_rules/``.
"""

from .feedback_schema import (
    FeedbackValidationError,
    build_parser_test_candidates,
    build_rule_candidates,
    feedback_event,
    normalize_feedback_decision,
)
from .review_queue_schema import normalize_review_queue, normalize_review_queue_item
from .agent_judgement import (
    AgentJudgementSchemaError,
    build_agent_judgement_request,
    parse_agent_judgement_response,
)

__all__ = [
    "FeedbackValidationError",
    "build_parser_test_candidates",
    "build_rule_candidates",
    "feedback_event",
    "normalize_feedback_decision",
    "normalize_review_queue",
    "normalize_review_queue_item",
    "AgentJudgementSchemaError",
    "build_agent_judgement_request",
    "parse_agent_judgement_response",
]
