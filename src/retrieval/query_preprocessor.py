"""Query normalization and optional metadata filters."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_PATH = REPO_ROOT / "config" / "sources.yaml"

_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s\-&]")


def _normalize_text(text: str) -> str:
    lowered = text.lower().strip()
    lowered = _PUNCT_RE.sub(" ", lowered)
    lowered = _SPACE_RE.sub(" ", lowered)
    return lowered.strip()


def _load_scheme_names() -> list[str]:
    if not SOURCES_PATH.exists():
        return []
    with SOURCES_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    schemes = [str(item.get("scheme_name", "")).strip() for item in payload.get("sources", [])]
    return [name for name in schemes if name]


SCHEME_NAMES = _load_scheme_names()


def preprocess_query(query: str) -> dict[str, Any]:
    """Return normalized query and optional Pinecone metadata filter."""
    cleaned = _normalize_text(query)
    if not cleaned:
        return {"normalized_query": "", "filter": None, "scheme_name": None}

    matched_scheme = None
    for scheme_name in SCHEME_NAMES:
        scheme_norm = _normalize_text(scheme_name.replace("—", " "))
        if scheme_norm and scheme_norm in cleaned:
            matched_scheme = scheme_name
            break

    metadata_filter = {"scheme_name": matched_scheme} if matched_scheme else None
    return {
        "normalized_query": cleaned,
        "filter": metadata_filter,
        "scheme_name": matched_scheme,
    }
