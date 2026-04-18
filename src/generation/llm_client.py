"""Groq LLM client and generation pipeline (Phase 6)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import Groq

from src.generation.formatter import format_answer
from src.generation.prompt_templates import SYSTEM_PROMPT, build_user_prompt
from src.retrieval.query_preprocessor import preprocess_query
from src.retrieval.retriever import retrieve

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = "https://groww.in/mutual-funds/amc/nippon-india-mutual-funds"
LOW_CONFIDENCE_PHRASES = (
    "i don't have that information",
    "not available in the context",
    "cannot determine from provided context",
)
HOLDINGS_QUERY_TERMS = (
    "holding",
    "holdings",
    "sector",
    "sector weight",
    "sector weights",
    "weightage",
    "sector allocation",
)
METRIC_QUERY_TERMS = (
    "expense ratio",
    "exit load",
    "sip",
    "minimum sip",
    "minimum investment",
    "lumpsum",
    "aum",
    "fund size",
    "nav",
    "benchmark",
    "risk",
    "return",
    "annualised",
    "cagr",
    "3y",
    "3 year",
    "lock-in",
    "lock in",
    "rating",
    "star",
    "stars",
)

_METRIC_PATTERNS = {
    "expense ratio": re.compile(r"expense ratio[^0-9]*(\d+(?:\.\d+)?%)", re.IGNORECASE),
    "exit load": re.compile(r"(exit load[^.\n]*\.)", re.IGNORECASE),
    "sip": re.compile(r"min\.?\s*for\s*sip[^₹\n]*₹\s*([0-9,]+)", re.IGNORECASE),
    "minimum sip": re.compile(r"(?:min\.?\s*for\s*sip|minimum sip(?: investment)?)\b[^₹\n]*₹\s*([0-9,]+)", re.IGNORECASE),
    "minimum investment": re.compile(r"(?:minimum investment|min\.?\s*investment|lump\s*sum minimum amount|minimum lumpsum investment)\b[^₹\n]*₹\s*([0-9,]+)", re.IGNORECASE),
    "lumpsum": re.compile(r"(?:minimum investment|min\.?\s*investment|lump\s*sum minimum amount|minimum lumpsum investment)\b[^₹\n]*₹\s*([0-9,]+)", re.IGNORECASE),
    "aum": re.compile(r"fund size\s*\(aum\)\s*[:\n]?\s*₹\s*([0-9,\.]+\s*Cr)", re.IGNORECASE),
    "fund size": re.compile(r"fund size\s*\(aum\)\s*[:\n]?\s*₹\s*([0-9,\.]+\s*Cr)", re.IGNORECASE),
    "nav": re.compile(r"(?:latest\s+nav|nav)[^\n₹]{0,80}₹\s*([0-9,\.]+)", re.IGNORECASE),
    "3y": re.compile(r"(?:3y|3\s*year|3\s*yr)(?:\s+annuali[sz]ed)?[\s\S]{0,120}?([+-]?\d+(?:\.\d+)?\s*%)", re.IGNORECASE),
    "3 year": re.compile(r"(?:3y|3\s*year|3\s*yr)(?:\s+annuali[sz]ed)?[\s\S]{0,120}?([+-]?\d+(?:\.\d+)?\s*%)", re.IGNORECASE),
    "return": re.compile(r"(?:3y|3\s*year|annuali[sz]ed|return|cagr)[\s\S]{0,150}?([+-]?\d+(?:\.\d+)?\s*%)", re.IGNORECASE),
    "lock-in": re.compile(r"lock[\s-]?in[^\n]{0,60}(\d+\s*(?:year|years))", re.IGNORECASE),
    "lock in": re.compile(r"lock[\s-]?in[^\n]{0,60}(\d+\s*(?:year|years))", re.IGNORECASE),
    "benchmark": re.compile(r"fund benchmark[^.\n]*\n?([A-Za-z0-9 &\-\(\)]+(?:Index|TRI|Index\))?)", re.IGNORECASE),
    "risk": re.compile(r"rated\s+([A-Za-z ]+)\s+risk", re.IGNORECASE),
    "rating": re.compile(r"\brating\s*[:\n]\s*([1-5])\b", re.IGNORECASE),
    "star": re.compile(r"\brating\s*[:\n]\s*([1-5])\b", re.IGNORECASE),
    "stars": re.compile(r"\brating\s*[:\n]\s*([1-5])\b", re.IGNORECASE),
}


def _load_env() -> None:
    load_dotenv(REPO_ROOT / ".env")


def _build_client() -> Groq:
    _load_env()
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY in environment.")
    return Groq(api_key=api_key)


def _parse_json_response(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _deterministic_fallback(query: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    prep = preprocess_query(query)
    matched_scheme = prep.get("scheme_name")
    scoped_chunks = chunks
    if matched_scheme:
        filtered = [
            row
            for row in chunks
            if str(row.get("metadata", {}).get("scheme_name", "")).strip() == str(matched_scheme).strip()
        ]
        if filtered:
            scoped_chunks = filtered

    contexts = [row.get("text", "") for row in scoped_chunks]
    merged_context = "\n".join(contexts)
    sources = [row.get("metadata", {}).get("source_url", "") for row in scoped_chunks if row.get("metadata")]
    dates = [row.get("metadata", {}).get("last_scraped_date", "") for row in scoped_chunks if row.get("metadata")]

    answer = _extract_returns_answer(query, contexts)
    last_updated: str | None = None
    if answer:
        summary_row = _first_row_with_text_prefix(scoped_chunks, "annualised returns (cagr)")
        last_updated = _chunk_meta_date(summary_row) or _max_meta_dates(scoped_chunks)
    if not answer:
        answer = _extract_holdings_answer(query, contexts)
        if answer:
            holdings_row = _first_row_with_text_prefix(scoped_chunks, "top holdings for")
            last_updated = _chunk_meta_date(holdings_row) or _max_meta_dates(scoped_chunks)
    if not answer:
        answer = _extract_metric_answer(query, merged_context)
        if answer:
            last_updated = _max_meta_dates(scoped_chunks)
    if not answer:
        if contexts:
            preview = " ".join(contexts[0].splitlines()[:2])[:260]
            answer = f"I found relevant source information: {preview}"
        else:
            answer = (
                "I don't have that information in my sources. Please check the official source pages: "
                "https://groww.in/mutual-funds/amc/nippon-india-mutual-funds"
            )

    source_url = next((src for src in sources if src), DEFAULT_SOURCE)
    if last_updated is None:
        last_updated = next((item for item in dates if item), None) or _max_meta_dates(scoped_chunks)
    return {
        "answer": answer,
        "source_url": source_url,
        "last_updated": last_updated,
        "used_fallback": True,
    }


def _is_holdings_query(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in HOLDINGS_QUERY_TERMS)


def _is_metric_query(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in METRIC_QUERY_TERMS)


def _scope_chunks_to_scheme(query: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prep = preprocess_query(query)
    matched_scheme = prep.get("scheme_name")
    if not matched_scheme:
        return chunks
    filtered = [
        row
        for row in chunks
        if str(row.get("metadata", {}).get("scheme_name", "")).strip() == str(matched_scheme).strip()
    ]
    return filtered or chunks


def _chunk_meta_date(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return str(row.get("metadata", {}).get("last_scraped_date", "") or "").strip()


def _max_meta_dates(rows: list[dict[str, Any]]) -> str | None:
    dates = [_chunk_meta_date(r) for r in rows if r.get("metadata")]
    dates = [d for d in dates if d]
    return max(dates) if dates else None


def _first_row_with_text_prefix(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any] | None:
    p = prefix.lower()
    for row in rows:
        if str(row.get("text", "") or "").lower().startswith(p):
            return row
    return None


def _extract_metric_answer(query: str, context: str) -> str | None:
    lowered = query.lower()
    for metric, pattern in _METRIC_PATTERNS.items():
        if metric in lowered:
            match = pattern.search(context)
            if match:
                value = match.group(1).strip()
                if metric in {"sip", "minimum sip"}:
                    return f"Minimum SIP is ₹{value}."
                if metric in {"minimum investment", "lumpsum"}:
                    return f"Minimum investment (lumpsum) is ₹{value}."
                if metric in {"aum", "fund size"}:
                    return f"Fund size (AUM) is ₹{value}."
                if metric == "nav":
                    return f"Latest NAV is ₹{value}."
                if metric in {"3y", "3 year"}:
                    return f"3Y annualised return is {value}."
                if metric == "return":
                    return f"Return is {value}."
                if metric in {"lock-in", "lock in"}:
                    return f"Lock-in period is {value}."
                if metric == "benchmark":
                    value = value.replace("Fund benchmark", "").strip(" .:")
                    return f"Fund benchmark is {value}."
                if metric == "risk":
                    return f"Risk level is {value}."
                if metric in {"rating", "star", "stars"}:
                    return f"Groww rating is {value}/5 stars."
                return f"{metric.title()} is {value}."
    if ("lock" in lowered or "lock-in" in lowered) and "year" in lowered:
        year_match = re.search(r"\b(\d+\s*(?:year|years))\b", context, flags=re.IGNORECASE)
        if year_match:
            return f"Lock-in period is {year_match.group(1)}."
    return None


_RETURNS_QUERY_TERMS = (
    "return",
    "returns",
    "cagr",
    "annualised",
    "annualized",
    "performance",
    "past performance",
)

_PERIOD_ALIASES = {
    "1d": ("1d", "1 day", "one day", "daily"),
    "1w": ("1w", "1 week", "one week", "weekly"),
    "1m": ("1m", "1 month", "one month"),
    "3m": ("3m", "3 month", "three month", "quarter", "3 months"),
    "6m": ("6m", "6 month", "six month", "6 months"),
    "9m": ("9m", "9 month", "9 months"),
    "1y": ("1y", "1 year", "one year", "1-year", "1yr", "12 month"),
    "2y": ("2y", "2 year", "2 years", "two year"),
    "3y": ("3y", "3 year", "3 years", "three year", "3-year", "3yr"),
    "4y": ("4y", "4 year", "4 years"),
    "5y": ("5y", "5 year", "5 years", "five year", "5-year", "5yr"),
    "7y": ("7y", "7 year", "7 years"),
    "10y": ("10y", "10 year", "10 years", "ten year", "10-year", "10yr"),
    "all": (
        "all-time", "all time", "since inception", "since launch", "lifetime",
        "inception", "overall",
    ),
}


def _is_returns_query(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in _RETURNS_QUERY_TERMS)


def _detect_periods(query: str) -> list[str]:
    lowered = query.lower()
    found: list[str] = []
    for period, aliases in _PERIOD_ALIASES.items():
        for alias in aliases:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered):
                if period not in found:
                    found.append(period)
                break
    return found


def _parse_returns_summary(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    # Lines look like "- 3 Years (3Y): +21.52%".
    for match in re.finditer(r"\(([A-Z0-9]+)\):\s*([+-]?\d+(?:\.\d+)?)%", text):
        code = match.group(1).lower()
        try:
            values[code] = float(match.group(2))
        except ValueError:
            continue
    return values


def _format_return_value(period: str, value: float) -> str:
    sign = "+" if value >= 0 else ""
    label_map = {
        "1d": "1-day return",
        "1w": "1-week return",
        "1m": "1-month return",
        "3m": "3-month return",
        "6m": "6-month return",
        "9m": "9-month return",
        "1y": "1-year return",
        "2y": "2-year annualised return (CAGR)",
        "3y": "3-year annualised return (CAGR)",
        "4y": "4-year annualised return (CAGR)",
        "5y": "5-year annualised return (CAGR)",
        "7y": "7-year annualised return (CAGR)",
        "10y": "10-year annualised return (CAGR)",
        "all": "Return since inception (CAGR)",
    }
    label = label_map.get(period, period.upper())
    return f"{label} is {sign}{value:.2f}%."


def _extract_returns_answer(query: str, contexts: list[str]) -> str | None:
    if not _is_returns_query(query):
        return None
    summary_text = None
    for text in contexts:
        if text.lower().startswith("annualised returns (cagr)"):
            summary_text = text
            break
    if not summary_text:
        return None

    values = _parse_returns_summary(summary_text)
    if not values:
        return None

    periods = _detect_periods(query)
    lowered = query.lower()
    # If query doesn't specify a period, prefer the 3Y annualised CAGR as the canonical answer.
    if not periods:
        if "cagr" in lowered or "annualised" in lowered or "annualized" in lowered:
            periods = ["3y"]
        else:
            # Default: return a concise multi-period summary.
            preferred = ["1y", "3y", "5y", "10y", "all"]
            lines = []
            for p in preferred:
                if p in values:
                    sign = "+" if values[p] >= 0 else ""
                    lines.append(f"- {p.upper()}: {sign}{values[p]:.2f}%")
            if not lines:
                return None
            return "Annualised returns (CAGR) from Groww:\n" + "\n".join(lines)

    answers: list[str] = []
    missing: list[str] = []
    for period in periods:
        if period in values:
            answers.append(_format_return_value(period, values[period]))
        else:
            missing.append(period.upper())
    if not answers:
        return None
    text = " ".join(answers)
    if missing:
        text += f" (No data available for: {', '.join(missing)}.)"
    return text


def _extract_holdings_answer(query: str, contexts: list[str]) -> str | None:
    lowered = query.lower()
    if not _is_holdings_query(lowered):
        return None
    wants_sector = ("sector" in lowered) or ("weightage" in lowered) or ("allocation" in lowered)
    for text in contexts:
        if text.lower().startswith("top holdings for"):
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if wants_sector:
                sector_idx = next((i for i, line in enumerate(lines) if line.lower().startswith("sector weights")), None)
                if sector_idx is not None:
                    sector_lines = [line for line in lines[sector_idx + 1 :] if line.startswith("- ")]
                    if sector_lines:
                        return "Sector weights from the source:\n" + "\n".join(sector_lines)
            top = lines[:6]
            return "Top holdings from the source:\n" + "\n".join(top)
    return None


def generate_answer(query: str, chunks: list[dict[str, Any]]) -> str:
    """Generate and format answer from provided chunks."""
    result = _generate_answer_payload(query, chunks)
    fallback_dates = [
        row.get("metadata", {}).get("last_scraped_date", "")
        for row in chunks
        if row.get("metadata")
    ]
    return format_answer(
        answer=result["answer"],
        source_url=result.get("source_url"),
        last_updated=result.get("last_updated"),
        fallback_dates=fallback_dates,
    )


def _generate_answer_payload(query: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    if not chunks:
        return _deterministic_fallback(query, chunks)
    scoped_chunks = _scope_chunks_to_scheme(query, chunks)

    # Deterministic path for returns / CAGR queries — uses structured returns summary.
    if _is_returns_query(query):
        returns_answer = _extract_returns_answer(query, [row.get("text", "") for row in scoped_chunks])
        if returns_answer:
            summary_row = _first_row_with_text_prefix(scoped_chunks, "annualised returns (cagr)")
            meta = (summary_row or {}).get("metadata", {}) or {}
            source_url = str(meta.get("source_url", "") or "").strip() or next(
                (
                    str(row.get("metadata", {}).get("source_url", "") or "").strip()
                    for row in scoped_chunks
                    if row.get("metadata")
                ),
                DEFAULT_SOURCE,
            )
            last_updated = _chunk_meta_date(summary_row) or _max_meta_dates(scoped_chunks)
            return {
                "answer": returns_answer,
                "source_url": source_url or DEFAULT_SOURCE,
                "last_updated": last_updated,
                "used_fallback": True,
            }

    # Deterministic path for holdings/sector queries to avoid LLM drift in numbers.
    if _is_holdings_query(query):
        structured_answer = _extract_holdings_answer(query, [row.get("text", "") for row in scoped_chunks])
        if structured_answer:
            holdings_row = _first_row_with_text_prefix(scoped_chunks, "top holdings for")
            meta = (holdings_row or {}).get("metadata", {}) or {}
            source_url = str(meta.get("source_url", "") or "").strip() or next(
                (
                    str(row.get("metadata", {}).get("source_url", "") or "").strip()
                    for row in scoped_chunks
                    if row.get("metadata")
                ),
                DEFAULT_SOURCE,
            )
            last_updated = _chunk_meta_date(holdings_row) or _max_meta_dates(scoped_chunks)
            return {
                "answer": structured_answer,
                "source_url": source_url or DEFAULT_SOURCE,
                "last_updated": last_updated,
                "used_fallback": True,
            }

    # Deterministic path for numeric factual metrics to avoid cross-scheme leakage.
    if _is_metric_query(query):
        metric_answer = _extract_metric_answer(query, "\n".join(row.get("text", "") for row in scoped_chunks))
        if metric_answer:
            source_url = next(
                (
                    str(row.get("metadata", {}).get("source_url", "") or "").strip()
                    for row in scoped_chunks
                    if row.get("metadata")
                ),
                DEFAULT_SOURCE,
            )
            last_updated = _max_meta_dates(scoped_chunks)
            return {
                "answer": metric_answer,
                "source_url": source_url or DEFAULT_SOURCE,
                "last_updated": last_updated,
                "used_fallback": True,
            }

    try:
        client = _build_client()
        model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        prompt = build_user_prompt(query, scoped_chunks)
        print(f"[generation] Calling Groq model={model}")
        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            max_tokens=300,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content if response.choices else ""
        parsed = _parse_json_response(content or "")
        if not parsed:
            raise RuntimeError("LLM output was not valid JSON.")

        answer = str(parsed.get("answer", "")).strip()
        source_url = str(parsed.get("source_url", "")).strip() or None
        last_updated = str(parsed.get("last_updated", "")).strip() or None
        if not answer:
            raise RuntimeError("LLM returned empty answer.")

        if any(phrase in answer.lower() for phrase in LOW_CONFIDENCE_PHRASES):
            print("[generation] Low-confidence answer detected, using fallback")
            return _deterministic_fallback(query, scoped_chunks)

        return {
            "answer": answer,
            "source_url": source_url,
            "last_updated": last_updated,
            "used_fallback": False,
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[generation] Warning: fallback due to generation failure: {exc}")
        return _deterministic_fallback(query, scoped_chunks)


def generate_answer_struct(query: str) -> dict[str, Any]:
    """Compatibility wrapper used by Streamlit app."""
    chunks = retrieve(query, top_k=10, top_n=3, similarity_threshold=0.35)
    payload = _generate_answer_payload(query, chunks)
    fallback_dates = [
        row.get("metadata", {}).get("last_scraped_date", "")
        for row in chunks
        if row.get("metadata")
    ]
    formatted = format_answer(
        answer=payload["answer"],
        source_url=payload.get("source_url"),
        last_updated=payload.get("last_updated"),
        fallback_dates=fallback_dates,
    )
    return {
        "answer": formatted,
        "sources": [payload.get("source_url") or DEFAULT_SOURCE],
        "used_fallback": payload.get("used_fallback", False),
    }
