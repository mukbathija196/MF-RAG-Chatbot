"""Pinecone-based retrieval with optional metadata filter and reranking."""

from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pinecone import Pinecone

from src.retrieval.query_preprocessor import preprocess_query
from src.retrieval.reranker import rerank

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"
CHUNKS_PATH = DATA_DIR / "chunks.jsonl"
HOLDINGS_PATH = DATA_DIR / "holdings_records.jsonl"
RETURNS_PATH = DATA_DIR / "returns_records.jsonl"
REPO_ROOT = Path(__file__).resolve().parents[2]

_RETURNS_QUERY_TERMS = (
    "return",
    "returns",
    "cagr",
    "annualised",
    "annualized",
    "performance",
    "past performance",
)

_PERIOD_LABELS = {
    "1d": "1 day",
    "1w": "1 week",
    "1m": "1 month",
    "3m": "3 months",
    "6m": "6 months",
    "9m": "9 months",
    "1y": "1 year",
    "2y": "2 years",
    "3y": "3 years",
    "4y": "4 years",
    "5y": "5 years",
    "7y": "7 years",
    "10y": "10 years",
    "all": "since inception",
}
_METRIC_HINTS = {
    "expense ratio": ("expense ratio", "%"),
    "exit load": ("exit load", "%"),
    "sip": ("sip", "₹"),
    "minimum sip": ("minimum sip", "₹"),
    "minimum investment": ("minimum investment", "₹"),
    "lumpsum": ("minimum investment", "₹"),
    "aum": ("fund size", "₹"),
    "fund size": ("fund size", "₹"),
    "nav": ("nav", "₹"),
    "benchmark": ("fund benchmark", "index"),
    "risk": ("risk", "rated"),
    "rating": ("rating", ""),
    "star": ("rating", ""),
    "stars": ("rating", ""),
    "lock-in": ("lock", "year"),
    "lock in": ("lock", "year"),
    "3y": ("3y", "%"),
    "3 year": ("3y", "%"),
    "annualised return": ("annualised", "%"),
    "return": ("return", "%"),
    "cagr": ("cagr", "%"),
}
_METRIC_STRICT_PATTERNS = {
    "expense ratio": re.compile(r"expense ratio[^0-9]{0,40}(\d+(?:\.\d+)?%)", re.IGNORECASE),
    "exit load": re.compile(r"exit load[^.\n]{0,120}\d+(?:\.\d+)?%", re.IGNORECASE),
    "sip": re.compile(r"(min\.?\s*for\s*sip|minimum sip(?: investment)?)\b[^₹\n]{0,60}₹\s*[0-9,]+", re.IGNORECASE),
    "minimum sip": re.compile(r"(min\.?\s*for\s*sip|minimum sip(?: investment)?)\b[^₹\n]{0,60}₹\s*[0-9,]+", re.IGNORECASE),
    "minimum investment": re.compile(r"(minimum investment|min\.?\s*investment)\b[^₹\n]{0,60}₹\s*[0-9,]+", re.IGNORECASE),
    "lumpsum": re.compile(r"(minimum investment|min\.?\s*investment)\b[^₹\n]{0,60}₹\s*[0-9,]+", re.IGNORECASE),
    "aum": re.compile(r"fund size\s*\(aum\)\s*[:\n]?\s*₹\s*[0-9,\.]+\s*cr", re.IGNORECASE),
    "fund size": re.compile(r"fund size\s*\(aum\)\s*[:\n]?\s*₹\s*[0-9,\.]+\s*cr", re.IGNORECASE),
    "nav": re.compile(r"(latest\s+nav|nav)[^\n₹]{0,60}₹\s*[0-9,\.]+", re.IGNORECASE),
    "benchmark": re.compile(r"fund benchmark[^\n]{0,120}(index|tri)", re.IGNORECASE),
    "risk": re.compile(r"rated\s+[a-z ]+\s+risk", re.IGNORECASE),
    "rating": re.compile(r"\brating\s*[:\n]\s*([1-5])\b", re.IGNORECASE),
    "star": re.compile(r"\brating\s*[:\n]\s*([1-5])\b", re.IGNORECASE),
    "stars": re.compile(r"\brating\s*[:\n]\s*([1-5])\b", re.IGNORECASE),
    "lock-in": re.compile(r"(lock[\s-]?in)[^\n]{0,80}\b\d+\s*(year|years)\b", re.IGNORECASE),
    "lock in": re.compile(r"(lock[\s-]?in)[^\n]{0,80}\b\d+\s*(year|years)\b", re.IGNORECASE),
    "3y": re.compile(r"(3y|3\s*year|3\s*yr)[^\n]{0,100}\d+(?:\.\d+)?%", re.IGNORECASE),
    "3 year": re.compile(r"(3y|3\s*year|3\s*yr)[^\n]{0,100}\d+(?:\.\d+)?%", re.IGNORECASE),
    "annualised return": re.compile(r"(annuali[sz]ed|return|cagr)[^\n]{0,120}\d+(?:\.\d+)?%", re.IGNORECASE),
    "return": re.compile(r"(annuali[sz]ed|return|cagr)[^\n]{0,120}\d+(?:\.\d+)?%", re.IGNORECASE),
    "cagr": re.compile(r"(cagr|annuali[sz]ed)[^\n]{0,120}\d+(?:\.\d+)?%", re.IGNORECASE),
}
_HOLDINGS_QUERY_TERMS = (
    "holding",
    "holdings",
    "sector",
    "sector weight",
    "sector weights",
    "weightage",
    "sector allocation",
    "portfolio weights",
)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _is_holdings_query(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in _HOLDINGS_QUERY_TERMS)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        return values
    return [v / norm for v in values]


def _extract_vectors(response: Any) -> list[list[float]]:
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if data is None:
        try:
            data = list(response)
        except Exception:  # noqa: BLE001
            data = []

    vectors: list[list[float]] = []
    for item in data:
        if isinstance(item, dict):
            vec = item.get("values") or item.get("embedding")
        else:
            vec = getattr(item, "values", None) or getattr(item, "embedding", None)
        if vec:
            vectors.append([float(v) for v in vec])
    return vectors


def _embed_query(pc: Pinecone, model: str, query: str) -> list[float]:
    response = pc.inference.embed(
        model=model,
        inputs=[query],
        parameters={"input_type": "query", "truncate": "END"},
    )
    vectors = _extract_vectors(response)
    if not vectors:
        raise RuntimeError("Failed to embed query vector.")
    return _normalize(vectors[0])


def _build_chunk_text_lookup() -> dict[str, dict[str, Any]]:
    rows = _load_jsonl(CHUNKS_PATH)
    lookup: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = str(row.get("id", ""))
        if row_id:
            lookup[row_id] = row
    return lookup


def _build_structured_holdings_candidates(
    query: str,
    scheme_name_filter: str | None,
) -> list[dict[str, Any]]:
    if not _is_holdings_query(query):
        return []
    records = _load_jsonl(HOLDINGS_PATH)
    if not records:
        return []

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        scheme_name = str(row.get("scheme_name", "")).strip()
        if not scheme_name:
            continue
        if scheme_name_filter and scheme_name != scheme_name_filter:
            continue
        grouped[scheme_name].append(row)

    candidates: list[dict[str, Any]] = []
    for scheme_name, rows in grouped.items():
        if not rows:
            continue
        sorted_rows = sorted(rows, key=lambda item: float(item.get("asset_weight_pct", 0.0)), reverse=True)
        top_rows = sorted_rows[:5]
        holding_lines = [
            f"{idx + 1}. {row.get('holding_name')} ({row.get('sector')}) - {float(row.get('asset_weight_pct', 0.0)):.2f}%"
            for idx, row in enumerate(top_rows)
        ]

        sector_totals: dict[str, float] = defaultdict(float)
        for row in rows:
            sector_name = str(row.get("sector", "Unknown")).strip() or "Unknown"
            sector_totals[sector_name] += float(row.get("asset_weight_pct", 0.0))
        # Keep full sector distribution (not top-5) so "each sector" queries are accurate.
        all_sectors = sorted(sector_totals.items(), key=lambda item: item[1], reverse=True)
        sector_lines = []
        for name, weight in all_sectors:
            label = "Other / Unclassified" if name.strip() == "--" else name
            sector_lines.append(f"- {label}: {weight:.2f}%")

        text = (
            f"Top holdings for {scheme_name}:\n"
            + "\n".join(holding_lines)
            + "\nSector weights:\n"
            + "\n".join(sector_lines)
        )
        source_url = str(rows[0].get("source_url", ""))
        last_scraped_date = str(rows[0].get("last_scraped_date", ""))
        candidates.append(
            {
                "id": f"structured-holdings::{scheme_name}",
                "score": 0.999,  # ensure reranker/fallback sees these for holdings queries
                "text": text,
                "metadata": {
                    "scheme_name": scheme_name,
                    "source_url": source_url,
                    "doc_type": "structured_holdings_summary",
                    "last_scraped_date": last_scraped_date,
                },
            }
        )

    return candidates


def _is_returns_query(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in _RETURNS_QUERY_TERMS)


def _build_structured_returns_candidates(
    query: str,
    scheme_name_filter: str | None,
) -> list[dict[str, Any]]:
    if not _is_returns_query(query):
        return []
    records = _load_jsonl(RETURNS_PATH)
    if not records:
        return []

    candidates: list[dict[str, Any]] = []
    for row in records:
        scheme_name = str(row.get("scheme_name", "")).strip()
        if not scheme_name:
            continue
        if scheme_name_filter and scheme_name != scheme_name_filter:
            continue
        returns = row.get("returns") or {}
        if not isinstance(returns, dict) or not returns:
            continue

        # Ordered printable summary so downstream regex/LLM can read cleanly.
        ordered_periods = ["1d", "1w", "1m", "3m", "6m", "9m", "1y", "2y", "3y", "4y", "5y", "7y", "10y", "all"]
        lines = []
        for period in ordered_periods:
            if period not in returns:
                continue
            try:
                value = float(returns[period])
            except (TypeError, ValueError):
                continue
            label = _PERIOD_LABELS.get(period, period)
            sign = "+" if value >= 0 else ""
            lines.append(f"- {label.title()} ({period.upper()}): {sign}{value:.2f}%")

        if not lines:
            continue

        text = (
            f"Annualised returns (CAGR) for {scheme_name} from Groww:\n"
            + "\n".join(lines)
            + "\nNote: 1D/1W/1M/3M/6M are simple point-to-point returns; 1Y and beyond are annualised (CAGR)."
        )
        candidates.append(
            {
                "id": f"structured-returns::{scheme_name}",
                "score": 0.999,
                "text": text,
                "metadata": {
                    "scheme_name": scheme_name,
                    "source_url": row.get("source_url"),
                    "doc_type": "structured_returns_summary",
                    "last_scraped_date": row.get("last_scraped_date"),
                },
            }
        )
    return candidates


def _matching_metric_hint(query: str) -> str | None:
    lowered = query.lower()
    for metric in _METRIC_HINTS:
        if metric in lowered:
            return metric
    return None


def _enforce_metric_coverage(
    query: str,
    candidates: list[dict[str, Any]],
    chunk_lookup: dict[str, dict[str, Any]],
    matched_scheme: str | None = None,
) -> list[dict[str, Any]]:
    metric = _matching_metric_hint(query)
    if not metric or not candidates:
        return candidates

    # Keep order but ensure at least one metric-bearing chunk is near the front.
    metric_candidate = None
    for row in candidates:
        text = str(row.get("text", ""))
        if _metric_strict_match(query, text):
            metric_candidate = row
            break

    if metric_candidate is None:
        # Search in all available chunks as a deterministic fallback.
        for row in chunk_lookup.values():
            row_scheme = str(row.get("metadata", {}).get("scheme_name", "")).strip()
            if matched_scheme and row_scheme and row_scheme != matched_scheme:
                continue
            text = str(row.get("text", ""))
            if _metric_strict_match(query, text):
                metric_candidate = {
                    "id": row.get("id"),
                    "score": 0.351,  # just above threshold so reranker can consider it
                    "text": row.get("text", ""),
                    "metadata": row.get("metadata", {}),
                }
                break

    if metric_candidate is None:
        return candidates

    # Move the metric-bearing chunk to front (or prepend if from lookup fallback).
    existing_idx = next(
        (idx for idx, item in enumerate(candidates) if item.get("id") == metric_candidate.get("id")),
        None,
    )
    if existing_idx is None:
        return [metric_candidate, *candidates]
    if existing_idx == 0:
        return candidates
    return [candidates[existing_idx], *candidates[:existing_idx], *candidates[existing_idx + 1 :]]


def _ensure_metric_in_final_results(
    query: str,
    final_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    metric = _matching_metric_hint(query)
    if not metric or not final_rows:
        return final_rows

    def _has_metric(row: dict[str, Any]) -> bool:
        text = str(row.get("text", ""))
        return _metric_strict_match(query, text)

    if any(_has_metric(row) for row in final_rows):
        return final_rows

    metric_row = next((row for row in candidates if _has_metric(row)), None)
    if metric_row is None:
        return final_rows

    # Force include one metric-bearing row by replacing the last result.
    return [*final_rows[:-1], metric_row]


def _metric_strict_match(query: str, text: str) -> bool:
    metric = _matching_metric_hint(query)
    if not metric:
        return False
    pattern = _METRIC_STRICT_PATTERNS.get(metric)
    if pattern is None:
        return False
    return pattern.search(text) is not None


def retrieve(
    query: str,
    top_k: int = 10,
    top_n: int = 3,
    similarity_threshold: float = 0.35,
) -> list[dict[str, Any]]:
    """Retrieve top_n reranked chunks for the query."""
    load_dotenv(REPO_ROOT / ".env")
    prep = preprocess_query(query)
    normalized_query = prep["normalized_query"]
    metadata_filter = prep["filter"]
    matched_scheme = prep["scheme_name"]
    if not normalized_query:
        return []

    api_key = _required_env("PINECONE_API_KEY")
    index_name = _required_env("PINECONE_INDEX")
    namespace = _required_env("PINECONE_NAMESPACE")
    model = os.getenv("EMBEDDING_MODEL", "llama-text-embed-v2")
    host = os.getenv("PINECONE_HOST", "").strip()

    pc = Pinecone(api_key=api_key)
    index = pc.Index(host=host) if host else pc.Index(name=index_name)
    query_vector = _embed_query(pc, model, normalized_query)
    metric_query = _matching_metric_hint(normalized_query) is not None
    effective_top_k = max(top_k, 50) if metric_query else top_k
    result = index.query(
        vector=query_vector,
        top_k=effective_top_k,
        include_metadata=True,
        namespace=namespace,
        filter=metadata_filter,
    )

    matches = getattr(result, "matches", None) or result.get("matches", [])
    chunk_lookup = _build_chunk_text_lookup()
    candidates: list[dict[str, Any]] = []
    for match in matches:
        if isinstance(match, dict):
            score = float(match.get("score", 0.0))
            match_id = str(match.get("id", ""))
            metadata = dict(match.get("metadata", {}) or {})
        else:
            score = float(getattr(match, "score", 0.0))
            match_id = str(getattr(match, "id", ""))
            metadata = dict(getattr(match, "metadata", {}) or {})

        if score < similarity_threshold or not match_id:
            continue
        chunk_row = chunk_lookup.get(match_id, {})
        text = str(chunk_row.get("text", "") or "").strip()
        if not text:
            text = str(metadata.get("chunk_text", "") or "").strip()
        if not text:
            continue
        candidates.append(
            {
                "id": match_id,
                "score": score,
                "text": text,
                "metadata": metadata,
            }
        )

    candidates.extend(_build_structured_holdings_candidates(normalized_query, matched_scheme))
    candidates.extend(_build_structured_returns_candidates(normalized_query, matched_scheme))
    candidates = _enforce_metric_coverage(
        normalized_query,
        candidates,
        chunk_lookup,
        matched_scheme=matched_scheme,
    )
    final_rows = rerank(normalized_query, candidates, top_n=top_n)
    return _ensure_metric_in_final_results(normalized_query, final_rows, candidates)
