"""Chunk parsed documents into token-aware overlapping chunks (Phase 3)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import tiktoken
from langchain.text_splitter import RecursiveCharacterTextSplitter

REPO_ROOT = Path(__file__).resolve().parents[2]
PARSED_PATH = REPO_ROOT / "data" / "processed" / "parsed_documents.jsonl"
CHUNKS_PATH = REPO_ROOT / "data" / "processed" / "chunks.jsonl"

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
MIN_TOKENS = 50


def _token_len(text: str) -> int:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


def _load_parsed_documents() -> list[dict[str, Any]]:
    if not PARSED_PATH.exists():
        raise FileNotFoundError(f"Parsed docs missing: {PARSED_PATH}")
    docs: list[dict[str, Any]] = []
    with PARSED_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def build_chunks() -> list[dict[str, Any]]:
    docs = _load_parsed_documents()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=_token_len,
    )

    chunks: list[dict[str, Any]] = []
    for doc in docs:
        doc_id = str(doc["doc_id"])
        text = str(doc["text"])
        parts = splitter.split_text(text)
        seen_norm_hashes: set[str] = set()
        chunk_index = 0
        for part in parts:
            part = part.strip()
            if not part:
                continue
            token_count = _token_len(part)
            if token_count < MIN_TOKENS:
                continue
            norm_hash = hashlib.sha256(" ".join(part.lower().split()).encode("utf-8")).hexdigest()
            if norm_hash in seen_norm_hashes:
                continue
            seen_norm_hashes.add(norm_hash)
            chunk_id = f"{doc_id}:{chunk_index}"
            metadata = {
                "chunk_id": chunk_id,
                "chunk_index": chunk_index,
                "token_count": token_count,
                "source_url": doc.get("source_url"),
                "scheme_name": doc.get("scheme_name"),
                "amc": doc.get("amc"),
                "doc_type": doc.get("doc_type"),
                "last_scraped_date": doc.get("last_scraped_date"),
                "doc_id": doc_id,
            }
            chunks.append({"id": chunk_id, "text": part, "metadata": metadata})
            chunk_index += 1

    CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CHUNKS_PATH.open("w", encoding="utf-8") as handle:
        for row in chunks:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    print(f"Chunks created: {len(chunks)}")
    print(f"Output: {CHUNKS_PATH}")
    return chunks


def main() -> None:
    build_chunks()


if __name__ == "__main__":
    main()
