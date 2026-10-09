"""Legacy qualification ports, not composed product capabilities."""

from uuid import UUID

from signal_core.business_brain import PageType, PageTypeDecision


def labelled_page_type_fallback(decision_id: UUID) -> PageTypeDecision:
    """Fail visibly and conservatively when Jev/model routing is not configured."""
    return PageTypeDecision(PageType.OTHER, "jev_unavailable_fallback", decision_id)
