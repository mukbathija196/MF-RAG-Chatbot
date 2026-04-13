#!/usr/bin/env python3
"""
Entrypoint: scrape → parse → chunk → embed (docs/architecture.md §2.2, §5.1).

Phases 2–3 implement the pipeline. Phase 1 leaves a no-op that exits successfully
so GitHub Actions and local checks can run.
"""

from __future__ import annotations


def main() -> None:
    print(
        "run_full_ingestion: pipeline not yet implemented (Phases 2–3).\n"
        "Phase 1 skeleton is ready — wire scraper, parser, chunker, and embedder here."
    )


if __name__ == "__main__":
    main()
