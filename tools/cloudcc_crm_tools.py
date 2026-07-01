"""
CloudCC CRM Tools - controlled CRM tool domain.

This module is intentionally browser-free. It defines the agent-facing tool
contract and returns structured blocked / needs_confirmation results until a
real browser adapter is wired in behind the same boundary.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional


def _result(
    *,
    operation: str,
    status: str,
    ok: bool,
    object_type: str = "",
    data: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    pending_confirmation: Optional[Dict[str, Any]] = None,
    blocked_reason: Optional[str] = None,
    next_steps: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the standard CRM tool result envelope."""
    return {
        "schema_version": "cloudcc.crm.result.v1",
        "ok": ok,
        "status": status,
        "operation": operation,
        "object_type": object_type,
        "data": data or {},
        "evidence": evidence or {},
        "pending_confirmation": pending_confirmation,
        "blocked_reason": blocked_reason,
        "next_steps": next_steps or [],
        "secrets_included": False,
        "created_at": datetime.now().isoformat(),
    }


class CloudCCCrmTools:
    """Controlled CloudCC/CRM tool domain.

    Current behavior is a safe fake adapter:
    - read/session tools return blocked until a real browser/session adapter is
      available;
    - draft tools can prepare local structured plans;
    - fill/submit tools never perform final CRM writes without confirmation.
    """

    def __init__(self, adapter_available: bool = False):
        self.adapter_available = adapter_available

    def cloudcc_session_probe(self) -> Dict[str, Any]:
        """Check whether CloudCC browser/session control is available."""
        if not self.adapter_available:
            return _result(
                operation="cloudcc_session_probe",
                status="blocked",
                ok=False,
                blocked_reason="browser_adapter_unavailable",
                evidence={
                    "session_state": "unknown",
                    "adapter": "fake",
                    "login_verified": False,
                },
                next_steps=[
                    "Connect a read-only CloudCC browser adapter.",
                    "Verify visible login/session state before CRM search.",
                ],
            )
        return _result(
            operation="cloudcc_session_probe",
            status="success",
            ok=True,
            evidence={
                "session_state": "available",
                "adapter": "browser",
                "login_verified": True,
            },
        )

    def cloudcc_search_record(
        self,
        object_type: str,
        query: str,
    ) -> Dict[str, Any]:
        """Search a CRM object type. Fake adapter returns blocked, not no-match."""
        return _result(
            operation="cloudcc_search_record",
            status="blocked",
            ok=False,
            object_type=object_type,
            blocked_reason="browser_adapter_unavailable",
            evidence={
                "query": query,
                "search_executed": False,
                "no_match_concluded": False,
            },
            next_steps=[
                "Run cloudcc_session_probe successfully before CRM search.",
            ],
        )

    def cloudcc_duplicate_check(
        self,
        project_code: str = "",
        project_name: str = "",
        customer: str = "",
    ) -> Dict[str, Any]:
        """Check duplicate opportunity evidence in CRM.

        Blocked session/search access must not be interpreted as no duplicate.
        """
        search_terms = {
            "project_code": project_code,
            "project_name": project_name,
            "customer": customer,
        }
        return _result(
            operation="cloudcc_duplicate_check",
            status="blocked",
            ok=False,
            object_type="opportunity",
            blocked_reason="browser_adapter_unavailable",
            evidence={
                "search_terms": search_terms,
                "search_executed": False,
                "duplicate_conclusion": "blocked",
            },
            next_steps=[
                "Verify CloudCC session state.",
                "Search opportunity/customer records with page evidence.",
            ],
        )

    def cloudcc_prepare_opportunity_draft(
        self,
        bid_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Prepare a CRM opportunity draft from bid_context without writing CRM."""
        draft = {
            "opportunity_name": bid_context.get("project_name", ""),
            "customer": bid_context.get("customer", ""),
            "amount": bid_context.get("budget", ""),
            "expected_close_date": bid_context.get("deadline", ""),
            "source": "招标公告",
            "project_code": bid_context.get("project_code", ""),
            "status": "draft_only",
        }
        missing = [
            key for key, value in draft.items()
            if key in {"opportunity_name", "customer"} and not value
        ]
        return _result(
            operation="cloudcc_prepare_opportunity_draft",
            status="success",
            ok=True,
            object_type="opportunity",
            data={
                "draft": draft,
                "missing_required_fields": missing,
            },
            evidence={
                "source": "bid_context",
                "crm_write_performed": False,
            },
            next_steps=[
                "Review missing_required_fields.",
                "Run duplicate check before any CRM write.",
            ],
        )

    def cloudcc_fill_draft_gated(
        self,
        draft: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Gate CRM form fill/submit behind explicit confirmation."""
        return _result(
            operation="cloudcc_fill_draft_gated",
            status="needs_confirmation",
            ok=False,
            object_type="opportunity",
            data={
                "draft": draft,
                "crm_write_performed": False,
                "stopped_before": "submit",
            },
            pending_confirmation={
                "action": "submit_opportunity",
                "requires_user_confirmation": True,
                "fields": draft,
            },
            evidence={
                "adapter": "fake",
                "form_filled": False,
                "submit_clicked": False,
            },
            next_steps=[
                "Confirm fields with the user.",
                "Use a real browser adapter only after confirmation.",
            ],
        )

    def cloudcc_readback_record(
        self,
        record_id: str = "",
        record_url: str = "",
    ) -> Dict[str, Any]:
        """Read back a CRM record after confirmed write."""
        return _result(
            operation="cloudcc_readback_record",
            status="blocked",
            ok=False,
            object_type="opportunity",
            blocked_reason="browser_adapter_unavailable",
            evidence={
                "record_id": record_id,
                "record_url": record_url,
                "readback_executed": False,
            },
            next_steps=[
                "Provide browser adapter and record locator for readback.",
            ],
        )
