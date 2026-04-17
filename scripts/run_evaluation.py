#!/usr/bin/env python3
"""
Phase 8 evaluation runner.

Runs factual + guardrail suites from tests/test_queries.yaml and writes:
  data/processed/evaluation_report.json
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.generation.llm_client import generate_answer_struct
from src.guardrails import check_guardrails
from src.retrieval.retriever import retrieve

TEST_QUERIES_PATH = REPO_ROOT / "tests" / "test_queries.yaml"
REPORT_PATH = REPO_ROOT / "data" / "processed" / "evaluation_report.json"


def _load_tests() -> dict[str, Any]:
    with TEST_QUERIES_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _contains_all_terms(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return all(term.lower() in lowered for term in terms)


def _normalize_text(text: str) -> str:
    lowered = text.lower()
    lowered = lowered.replace("₹", "rs ")
    lowered = lowered.replace("&", " and ")
    lowered = re.sub(r"[^a-z0-9.%+\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _semantic_candidates(term: str) -> list[str]:
    normalized = _normalize_text(term)
    candidates = {normalized}
    alias_map = {
        "min for sip": ["minimum sip", "sip"],
        "minimum sip": ["min for sip", "sip"],
        "fund size": ["aum", "asset under management"],
        "aum": ["fund size", "asset under management"],
        "lock in": ["lockin", "lock in period", "lock"],
        "annualised": ["annualized"],
        "annualized": ["annualised"],
        "lumpsum": ["lump sum", "minimum investment"],
    }
    for src, aliases in alias_map.items():
        if src in normalized:
            for alias in aliases:
                candidates.add(normalized.replace(src, alias))
    return [item for item in candidates if item]


def _contains_all_terms_semantic(text: str, terms: list[str]) -> bool:
    normalized_text = _normalize_text(text)
    for term in terms:
        variants = _semantic_candidates(term)
        if not any(variant in normalized_text for variant in variants):
            return False
    return True


def _evaluate_factual_case(case: dict[str, Any]) -> dict[str, Any]:
    query = str(case["query"])
    expected_source = str(case.get("expected_source", "")).strip()
    retrieval_terms = [str(term) for term in case.get("expected_retrieval_terms", [])]
    answer_terms = [str(term) for term in case.get("expected_answer_terms", [])]

    start = time.perf_counter()
    retrieved = retrieve(query, top_k=10, top_n=3, similarity_threshold=0.35)
    answer_struct = generate_answer_struct(query)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

    retrieval_text = "\n".join(row.get("text", "") for row in retrieved)
    retrieval_sources = [str(row.get("metadata", {}).get("source_url", "")) for row in retrieved]
    answer = str(answer_struct.get("answer", ""))

    retrieval_hit = bool(expected_source and expected_source in retrieval_sources)
    retrieval_terms_ok = _contains_all_terms(retrieval_text, retrieval_terms) if retrieval_terms else True
    answer_terms_ok = _contains_all_terms(answer, answer_terms) if answer_terms else True
    retrieval_terms_semantic_ok = _contains_all_terms_semantic(retrieval_text, retrieval_terms) if retrieval_terms else True
    answer_terms_semantic_ok = _contains_all_terms_semantic(answer, answer_terms) if answer_terms else True
    citation_ok = "📄 Source:" in answer and "🕐 Last updated from sources:" in answer

    return {
        "id": case.get("id"),
        "query": query,
        "latency_ms": elapsed_ms,
        "retrieval_hit": retrieval_hit,
        "retrieval_terms_ok": retrieval_terms_ok,
        "retrieval_terms_semantic_ok": retrieval_terms_semantic_ok,
        "answer_terms_ok": answer_terms_ok,
        "answer_terms_semantic_ok": answer_terms_semantic_ok,
        "citation_ok": citation_ok,
        "retrieved_sources": retrieval_sources,
        "answer_preview": answer[:300],
        "pass": retrieval_hit and retrieval_terms_ok and answer_terms_ok and citation_ok,
        "semantic_pass": retrieval_hit and retrieval_terms_semantic_ok and answer_terms_semantic_ok and citation_ok,
    }


def _evaluate_guardrail_case(case: dict[str, Any]) -> dict[str, Any]:
    query = str(case["query"])
    expected_blocked = bool(case.get("expected_blocked", True))
    expected_message = str(case.get("expected_message_contains", "")).strip().lower()

    start = time.perf_counter()
    result = check_guardrails(query)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

    blocked = bool(result.get("blocked", False))
    reason = str(result.get("reason", "") or "")
    message_ok = True
    if expected_message:
        message_ok = expected_message in reason.lower()

    return {
        "id": case.get("id"),
        "query": query,
        "latency_ms": elapsed_ms,
        "expected_blocked": expected_blocked,
        "actual_blocked": blocked,
        "message_ok": message_ok,
        "reason": reason,
        "pass": blocked == expected_blocked and message_ok,
    }


def _summarize(factual_rows: list[dict[str, Any]], guardrail_rows: list[dict[str, Any]]) -> dict[str, Any]:
    factual_total = len(factual_rows)
    guardrail_total = len(guardrail_rows)

    factual_pass = sum(1 for row in factual_rows if row["pass"])
    guardrail_pass = sum(1 for row in guardrail_rows if row["pass"])
    retrieval_hits = sum(1 for row in factual_rows if row["retrieval_hit"])
    retrieval_terms_strict_ok = sum(1 for row in factual_rows if row["retrieval_terms_ok"])
    retrieval_terms_semantic_ok = sum(1 for row in factual_rows if row["retrieval_terms_semantic_ok"])
    answer_strict_ok = sum(1 for row in factual_rows if row["answer_terms_ok"])
    answer_semantic_ok = sum(1 for row in factual_rows if row["answer_terms_semantic_ok"])
    citation_ok = sum(1 for row in factual_rows if row["citation_ok"])
    semantic_pass = sum(1 for row in factual_rows if row["semantic_pass"])

    factual_latencies = [row["latency_ms"] for row in factual_rows]
    guardrail_latencies = [row["latency_ms"] for row in guardrail_rows]
    all_latencies = factual_latencies + guardrail_latencies

    return {
        "factual_total": factual_total,
        "guardrail_total": guardrail_total,
        "factual_pass_count": factual_pass,
        "guardrail_pass_count": guardrail_pass,
        "factual_pass_rate": round((factual_pass / factual_total) * 100, 2) if factual_total else 0.0,
        "factual_semantic_pass_rate": round((semantic_pass / factual_total) * 100, 2) if factual_total else 0.0,
        "guardrail_pass_rate": round((guardrail_pass / guardrail_total) * 100, 2) if guardrail_total else 0.0,
        "retrieval_accuracy_pct": round((retrieval_hits / factual_total) * 100, 2) if factual_total else 0.0,
        "retrieval_terms_strict_pct": round((retrieval_terms_strict_ok / factual_total) * 100, 2) if factual_total else 0.0,
        "retrieval_terms_semantic_pct": round((retrieval_terms_semantic_ok / factual_total) * 100, 2) if factual_total else 0.0,
        "answer_correctness_strict_pct": round((answer_strict_ok / factual_total) * 100, 2) if factual_total else 0.0,
        "answer_correctness_semantic_pct": round((answer_semantic_ok / factual_total) * 100, 2) if factual_total else 0.0,
        "answer_correctness_pct": round((answer_strict_ok / factual_total) * 100, 2) if factual_total else 0.0,
        "citation_accuracy_pct": round((citation_ok / factual_total) * 100, 2) if factual_total else 0.0,
        "latency_avg_ms": round(statistics.mean(all_latencies), 2) if all_latencies else 0.0,
        "latency_p95_ms": round(statistics.quantiles(all_latencies, n=20)[18], 2) if len(all_latencies) >= 20 else max(all_latencies, default=0.0),
    }


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    tests = _load_tests()
    factual_cases = tests.get("factual_queries", [])
    guardrail_cases = tests.get("guardrail_queries", [])

    factual_rows = [_evaluate_factual_case(case) for case in factual_cases]
    guardrail_rows = [_evaluate_guardrail_case(case) for case in guardrail_cases]
    summary = _summarize(factual_rows, guardrail_rows)

    report = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": summary,
        "factual_results": factual_rows,
        "guardrail_results": guardrail_rows,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=True)

    print("Phase 8 evaluation complete.")
    print(f"Report: {REPORT_PATH}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
