"""PII detector."""

from __future__ import annotations

import re

PII_MESSAGE = (
    "I can't process personal information like PAN, Aadhaar, phone numbers, or "
    "email addresses. Please ask a factual question about mutual fund schemes."
)

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "PAN": re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE),
    "Aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "Phone": re.compile(r"\b[6-9]\d{9}\b"),
    "Email": re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b"),
}

_OTP_PATTERN = re.compile(r"\b\d{4,6}\b")
_OTP_INTENT_PATTERN = re.compile(r"\b(otp|one time password|verification code|verify)\b", re.IGNORECASE)


def detect_pii(query: str) -> tuple[bool, str | None]:
    """Return (is_blocked, message)."""
    text = query.strip()
    if not text:
        return False, None

    for pattern in PII_PATTERNS.values():
        if pattern.search(text):
            return True, PII_MESSAGE

    # OTP should be treated as PII only when combined with verification intent.
    if _OTP_PATTERN.search(text) and _OTP_INTENT_PATTERN.search(text):
        return True, PII_MESSAGE

    return False, None
