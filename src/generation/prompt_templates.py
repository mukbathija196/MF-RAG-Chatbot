"""Prompt templates for the Phase 6 generation pipeline."""

from __future__ import annotations

SYSTEM_PROMPT = """You are a facts-only mutual fund FAQ assistant for Indian mutual fund schemes.
You must answer using ONLY the provided context chunks.

Rules:
1. Answer in at most 3 sentences.
2. Include exactly one source URL from context as `source_url`.
3. Include the corresponding last scraped date as `last_updated`.
4. If context does not contain the answer, reply:
   "I don't have that information in my sources. Please check the official source pages: https://groww.in/mutual-funds/amc/nippon-india-mutual-funds"
5. Never give investment advice, opinions, projections, or comparisons.
6. Never fabricate facts not present in context.
7. Return STRICT JSON only with keys: answer, source_url, last_updated.
"""


def build_user_prompt(query: str, chunks: list[dict]) -> str:
    context_blocks: list[str] = []
    for idx, row in enumerate(chunks[:3], start=1):
        text = row.get("text", "").strip()
        metadata = row.get("metadata", {}) or {}
        source_url = metadata.get("source_url", "")
        scheme_name = metadata.get("scheme_name", "")
        last_scraped_date = metadata.get("last_scraped_date", "")
        context_blocks.append(
            "\n".join(
                [
                    f"--- Chunk {idx} ---",
                    text,
                    f"Source: {source_url}",
                    f"Scheme: {scheme_name}",
                    f"Last Scraped Date: {last_scraped_date}",
                ]
            )
        )

    context_text = "\n\n".join(context_blocks) if context_blocks else "No context available."
    return (
        "CONTEXT:\n"
        f"{context_text}\n\n"
        "USER QUERY:\n"
        f"{query}\n"
    )
