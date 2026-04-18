"""Embed chunks and upsert to Pinecone (Phase 3)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from pinecone import Pinecone
from dotenv import load_dotenv

from src.ingestion.chunker import build_chunks
from src.ingestion.parser import parse_documents

REPO_ROOT = Path(__file__).resolve().parents[2]
CHUNKS_PATH = REPO_ROOT / "data" / "processed" / "chunks.jsonl"

# Pinecone metadata per-vector size budget; keep headroom for other keys and UTF-8.
_CHUNK_TEXT_METADATA_MAX = 32000


def _load_chunks() -> list[dict[str, Any]]:
    if not CHUNKS_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    with CHUNKS_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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


def _embed_batch(pc: Pinecone, model: str, texts: list[str], input_type: str) -> list[list[float]]:
    response = pc.inference.embed(
        model=model,
        inputs=texts,
        parameters={"input_type": input_type, "truncate": "END"},
    )
    vectors = _extract_vectors(response)
    if len(vectors) != len(texts):
        raise RuntimeError(f"Embedding size mismatch: got {len(vectors)} vectors for {len(texts)} texts")
    return [_normalize(vec) for vec in vectors]


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def run_embedder(*, prepare_artifacts: bool = True) -> None:
    load_dotenv(REPO_ROOT / ".env")
    if prepare_artifacts:
        # Default behavior keeps embedder runnable as a standalone stage.
        parse_documents()
        build_chunks()
    chunks = _load_chunks()
    if not chunks:
        raise RuntimeError("No chunks available for embedding.")

    api_key = _required_env("PINECONE_API_KEY")
    index_name = _required_env("PINECONE_INDEX")
    namespace = _required_env("PINECONE_NAMESPACE")
    model = os.getenv("EMBEDDING_MODEL", "llama-text-embed-v2")
    batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    host = os.getenv("PINECONE_HOST", "").strip()

    pc = Pinecone(api_key=api_key)
    index = pc.Index(host=host) if host else pc.Index(name=index_name)

    # Doc IDs are content-derived; when Groww pages change, chunk IDs change too.
    # Upsert alone would leave orphaned vectors and pollute retrieval unless we
    # clear the namespace first (scheduler / full ingestion default).
    if _env_bool("PINECONE_REPLACE_NAMESPACE", default=True):
        index.delete(delete_all=True, namespace=namespace)
        print(f"Cleared Pinecone namespace before upsert: {namespace}")

    total = len(chunks)
    for start in range(0, total, batch_size):
        batch = chunks[start : start + batch_size]
        texts = [row["text"] for row in batch]
        vectors = _embed_batch(pc, model, texts, input_type="passage")
        upserts = []
        for row, vector in zip(batch, vectors):
            meta = dict(row["metadata"])
            body = str(row.get("text", "") or "")
            meta["chunk_text"] = body[:_CHUNK_TEXT_METADATA_MAX]
            upserts.append(
                {
                    "id": row["id"],
                    "values": vector,
                    "metadata": meta,
                }
            )
        index.upsert(vectors=upserts, namespace=namespace)
        print(f"Upserted {start + len(batch)}/{total} chunks")

    print(f"Embedding + upsert complete. Namespace: {namespace}")
    _run_smoke_query(pc=pc, index=index, model=model, namespace=namespace)


def _run_smoke_query(pc: Pinecone, index: Any, model: str, namespace: str) -> None:
    question = "What is the exit load for Nippon India Small Cap Fund?"
    vector = _embed_batch(pc, model, [question], input_type="query")[0]
    result = index.query(vector=vector, top_k=5, include_metadata=True, namespace=namespace)
    matches = getattr(result, "matches", None) or result.get("matches", [])
    print("\nSmoke test top-5 matches:")
    for idx, match in enumerate(matches[:5], start=1):
        if isinstance(match, dict):
            meta = match.get("metadata", {})
            match_id = match.get("id")
            score = match.get("score")
        else:
            meta = getattr(match, "metadata", {}) or {}
            match_id = getattr(match, "id", None)
            score = getattr(match, "score", None)
        print(
            f"{idx}. id={match_id} score={score} "
            f"scheme={meta.get('scheme_name')} source={meta.get('source_url')}"
        )


def main() -> None:
    run_embedder()


if __name__ == "__main__":
    main()
