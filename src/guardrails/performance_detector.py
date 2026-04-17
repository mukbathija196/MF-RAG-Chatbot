"""Performance-claim / projection guardrail."""

from __future__ import annotations

PERFORMANCE_MESSAGE = (
    "I can't compute custom return projections or compare performance across funds. "
    "I can share factual return/CAGR values from source pages for a specific scheme."
)

# Keep factual 'returns' and 'CAGR' questions allowed; only block projection/comparison intent.
PERFORMANCE_BLOCK_SIGNALS = (
    "compare returns",
    "compare cagr",
    "which is better",
    "higher return",
    "best return",
    "outperform",
    " vs ",
    "if i invest",
    "how much profit",
    "future return",
    "expected return",
    "calculate xirr",
    "calculate cagr",
)


def detect_performance_projection(query: str) -> tuple[bool, str | None]:
    """Return (is_blocked, message)."""
    lowered = query.lower()
    if any(signal in lowered for signal in PERFORMANCE_BLOCK_SIGNALS):
        return True, PERFORMANCE_MESSAGE
    return False, None
