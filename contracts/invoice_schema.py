"""Stable, invoice-only field names for deterministic extraction.

Invoice line-item labels are accounting evidence.  They must never be
promoted to bid-project governance fields by callers.
"""

from typing import Final


INVOICE_ALLOWED_FIELDS: Final[frozenset[str]] = frozenset({
    "invoice_number",
    "invoice_date",
    "buyer",
    "seller",
    "untaxed_amount",
    "tax_amount",
    "total_amount",
    "line_items",
})

INVOICE_FORBIDDEN_BUSINESS_FIELDS: Final[frozenset[str]] = frozenset({
    "project_name",
    "project_code",
    "deadline",
    "bid_status",
    "lifecycle_stage",
})
