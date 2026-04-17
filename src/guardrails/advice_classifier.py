"""Investment advice guardrail."""

from __future__ import annotations

ADVICE_MESSAGE = (
    "I only provide factual information from official sources. For personalized "
    "investment advice, please consult a SEBI-registered investment advisor. "
    "Here's a helpful resource: "
    "https://www.amfiindia.com/investor-corner/knowledge-center/how-to-select-MF.html"
)

ADVICE_SIGNALS = (
    "should i buy",
    "should i sell",
    "should i invest",
    "which is better",
    "recommend",
    "best fund",
    "portfolio",
    "allocate",
    "timing the market",
    "will it go up",
    "will it fall",
)


def detect_advice_request(query: str) -> tuple[bool, str | None]:
    """Return (is_blocked, message)."""
    lowered = query.lower()
    if any(signal in lowered for signal in ADVICE_SIGNALS):
        return True, ADVICE_MESSAGE
    return False, None
