"""Input guardrails facade."""

from __future__ import annotations

from src.guardrails.advice_classifier import detect_advice_request
from src.guardrails.performance_detector import detect_performance_projection
from src.guardrails.pii_detector import detect_pii


def check_guardrails(query: str) -> dict:
    """Return first guardrail failure or pass."""
    for detector in (detect_pii, detect_advice_request, detect_performance_projection):
        blocked, reason = detector(query)
        if blocked:
            return {"blocked": True, "reason": reason}
    return {"blocked": False, "reason": None}
