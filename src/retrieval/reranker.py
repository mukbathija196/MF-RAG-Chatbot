"""Cross-encoder re-ranking for retrieval candidates."""

from __future__ import annotations

import os
from typing import Any

_RERANK_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_RERANKER: Any = None
_RERANKER_LOAD_FAILED = False


def _get_reranker() -> Any:
    global _RERANKER, _RERANKER_LOAD_FAILED
    if _RERANKER is not None:
        return _RERANKER
    if _RERANKER_LOAD_FAILED:
        return None
    if os.getenv("ENABLE_CROSS_ENCODER", "0").strip().lower() not in {"1", "true", "yes"}:
        _RERANKER_LOAD_FAILED = True
        return None
    try:
        from sentence_transformers import CrossEncoder

        _RERANKER = CrossEncoder(_RERANK_MODEL_NAME, max_length=512)
        return _RERANKER
    except Exception as exc:  # noqa: BLE001
        _RERANKER_LOAD_FAILED = True
        print(f"[reranker] Falling back to vector-order ranking: {exc}")
        return None


def rerank(query: str, candidates: list[dict[str, Any]], top_n: int = 3) -> list[dict[str, Any]]:
    """Re-score candidates and return top_n."""
    if not candidates:
        return []

    reranker = _get_reranker()
    if reranker is None:
        sorted_candidates = sorted(candidates, key=lambda row: row.get("score", 0.0), reverse=True)
        return sorted_candidates[:top_n]

    pairs = [(query, row.get("text", "")) for row in candidates]
    scores = reranker.predict(pairs)
    for row, score in zip(candidates, scores):
        row["rerank_score"] = float(score)

    sorted_candidates = sorted(candidates, key=lambda row: row.get("rerank_score", -1e9), reverse=True)
    return sorted_candidates[:top_n]
