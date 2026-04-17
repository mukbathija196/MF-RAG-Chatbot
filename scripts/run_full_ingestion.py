#!/usr/bin/env python3
"""Entrypoint: scrape -> parse -> chunk -> embed/upsert."""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.ingestion.chunker import build_chunks
from src.ingestion.embedder import run_embedder
from src.ingestion.parser import parse_documents
from src.ingestion.scraper import run_scraper


def _print_stage(name: str) -> None:
    print(f"\n=== {name} ===")


def main() -> None:
    start = time.time()
    print("Starting full ingestion pipeline")

    _print_stage("1/4 SCRAPE")
    manifest_rows = run_scraper()
    total_sources = len(manifest_rows)
    ok_sources = sum(1 for row in manifest_rows if row.get("error") is None)
    if total_sources == 0:
        raise RuntimeError("No sources found in config/sources.yaml")
    if ok_sources == 0:
        raise RuntimeError("Scrape failed for all sources. Aborting pipeline.")
    print(f"Scrape summary: {ok_sources}/{total_sources} successful")

    _print_stage("2/4 PARSE")
    parsed_rows = parse_documents()
    if not parsed_rows:
        raise RuntimeError("No parsed documents produced. Aborting pipeline.")
    print(f"Parse summary: {len(parsed_rows)} documents")

    _print_stage("3/4 CHUNK")
    chunks = build_chunks()
    if not chunks:
        raise RuntimeError("No chunks produced. Aborting pipeline.")
    print(f"Chunk summary: {len(chunks)} chunks")

    _print_stage("4/4 EMBED + UPSERT")
    run_embedder(prepare_artifacts=False)

    elapsed = time.time() - start
    print(f"\nPipeline complete in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
