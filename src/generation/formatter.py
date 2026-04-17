"""Answer formatting helpers."""

from __future__ import annotations


def _pick_last_updated(candidate: str | None, fallback_dates: list[str]) -> str:
    if candidate:
        return candidate
    for item in fallback_dates:
        if item:
            return item
    return "N/A"


def format_answer(answer: str, source_url: str | None, last_updated: str | None, fallback_dates: list[str] | None = None) -> str:
    final_source = source_url or "https://groww.in/mutual-funds/amc/nippon-india-mutual-funds"
    final_date = _pick_last_updated(last_updated, fallback_dates or [])
    return f"{answer}\n\n📄 Source: {final_source}\n🕐 Last updated from sources: {final_date}"
