"""Parse raw HTML snapshots into cleaned documents (Phase 3)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "ingest_manifest.jsonl"
PARSED_PATH = REPO_ROOT / "data" / "processed" / "parsed_documents.jsonl"
HOLDINGS_PATH = REPO_ROOT / "data" / "processed" / "holdings_records.jsonl"
RETURNS_PATH = REPO_ROOT / "data" / "processed" / "returns_records.jsonl"

# Groww return_stats field -> canonical period key used by retriever/generator.
_RETURN_FIELD_TO_PERIOD = {
    "return1d": "1d",
    "return1w": "1w",
    "return1m": "1m",
    "return3m": "3m",
    "return6m": "6m",
    "return9m": "9m",
    "return1y": "1y",
    "return2y": "2y",
    "return3y": "3y",
    "return4y": "4y",
    "return5y": "5y",
    "return7y": "7y",
    "return10y": "10y",
    "return_since_created": "all",
}

_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
_HOLDINGS_QUERY_BREAKPOINTS = {
    "see all",
    "minimum investments",
    "understand terms",
    "returns and rankings",
    "about",
    "exit load",
}


def _load_manifest() -> list[dict[str, Any]]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Manifest not found: {MANIFEST_PATH}")
    rows: list[dict[str, Any]] = []
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    for tag_name in ("header", "footer", "nav", "aside"):
        for tag in soup.find_all(tag_name):
            tag.decompose()
    text = soup.get_text(separator="\n")
    text = _WHITESPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    merged = "\n".join(lines)
    merged = _BLANK_LINES_RE.sub("\n\n", merged).strip()
    return merged


def _parse_weight_pct(value: str) -> float | None:
    match = _WEIGHT_RE.search(value)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_holdings_rows(clean_text: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
    if not lines:
        return []

    # Find the holdings section marker.
    start_idx = None
    for idx, line in enumerate(lines):
        if line.lower().startswith("holdings ("):
            start_idx = idx
            break
    if start_idx is None:
        return []

    cursor = start_idx + 1
    if cursor + 3 < len(lines) and [lines[cursor + k].lower() for k in range(4)] == [
        "name",
        "sector",
        "instruments",
        "assets",
    ]:
        cursor += 4

    records: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    while cursor + 3 < len(lines):
        name = lines[cursor].strip()
        sector = lines[cursor + 1].strip()
        instrument = lines[cursor + 2].strip()
        assets = lines[cursor + 3].strip()

        if name.lower() in _HOLDINGS_QUERY_BREAKPOINTS:
            break

        weight = _parse_weight_pct(assets)
        if weight is None:
            cursor += 1
            continue

        dedupe_key = (name.lower(), weight)
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            records.append(
                {
                    "holding_name": name,
                    "sector": sector,
                    "instrument": instrument,
                    "asset_weight_pct": weight,
                }
            )
        cursor += 4

    return records


def _extract_holdings_from_next_data(raw_html: str) -> list[dict[str, Any]]:
    """Prefer exact holdings from Groww's __NEXT_DATA__ payload when available."""
    soup = BeautifulSoup(raw_html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []
    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        return []

    mf_data = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("mfServerSideData", {})
    )
    rows = mf_data.get("holdings", [])
    if not isinstance(rows, list):
        return []

    records: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("company_name", "")).strip()
        if not name:
            continue
        sector = str(row.get("sector_name") or "--").strip() or "--"
        instrument = str(row.get("instrument_name") or "").strip() or "Unknown"
        try:
            weight = float(row.get("corpus_per"))
        except (TypeError, ValueError):
            continue
        dedupe_key = (name.lower(), weight)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        records.append(
            {
                "holding_name": name,
                "sector": sector,
                "instrument": instrument,
                "asset_weight_pct": weight,
            }
        )
    return records


def _extract_returns_from_next_data(raw_html: str) -> dict[str, float]:
    """Extract structured annualised returns from Groww's __NEXT_DATA__ payload."""
    soup = BeautifulSoup(raw_html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return {}
    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        return {}

    mf_data = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("mfServerSideData", {})
    )
    stats_list = mf_data.get("return_stats")
    if not isinstance(stats_list, list) or not stats_list:
        return {}
    stats = stats_list[0]
    if not isinstance(stats, dict):
        return {}

    returns: dict[str, float] = {}
    for field, period in _RETURN_FIELD_TO_PERIOD.items():
        raw = stats.get(field)
        if raw is None:
            continue
        try:
            returns[period] = round(float(raw), 2)
        except (TypeError, ValueError):
            continue
    return returns


def _write_returns_records(
    parsed_rows: list[dict[str, Any]],
    raw_html_by_doc_id: dict[str, str],
) -> int:
    RETURNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    output_rows: list[dict[str, Any]] = []
    for doc in parsed_rows:
        if doc.get("doc_type") != "groww_scheme_page":
            continue
        raw_html = raw_html_by_doc_id.get(str(doc.get("doc_id", "")), "")
        returns = _extract_returns_from_next_data(raw_html) if raw_html else {}
        if not returns:
            continue
        output_rows.append(
            {
                "record_id": f"{doc['doc_id']}:returns",
                "doc_id": doc["doc_id"],
                "scheme_name": doc.get("scheme_name"),
                "source_url": doc.get("source_url"),
                "last_scraped_date": doc.get("last_scraped_date"),
                "returns": returns,
            }
        )

    with RETURNS_PATH.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    return len(output_rows)


def _write_holdings_records(parsed_rows: list[dict[str, Any]], raw_html_by_doc_id: dict[str, str]) -> int:
    HOLDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    output_rows: list[dict[str, Any]] = []
    for doc in parsed_rows:
        if doc.get("doc_type") != "groww_scheme_page":
            continue
        raw_html = raw_html_by_doc_id.get(str(doc.get("doc_id", "")), "")
        holdings = _extract_holdings_from_next_data(raw_html) if raw_html else []
        if not holdings:
            # Fallback path for robustness if __NEXT_DATA__ is unavailable.
            holdings = _extract_holdings_rows(str(doc.get("text", "")))
        for idx, row in enumerate(holdings):
            output_rows.append(
                {
                    "record_id": f"{doc['doc_id']}:holding:{idx}",
                    "doc_id": doc["doc_id"],
                    "scheme_name": doc.get("scheme_name"),
                    "source_url": doc.get("source_url"),
                    "last_scraped_date": doc.get("last_scraped_date"),
                    "holding_name": row["holding_name"],
                    "sector": row["sector"],
                    "instrument": row["instrument"],
                    "asset_weight_pct": row["asset_weight_pct"],
                }
            )

    with HOLDINGS_PATH.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    return len(output_rows)


def parse_documents() -> list[dict[str, Any]]:
    manifest = _load_manifest()
    PARSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    parsed_rows: list[dict[str, Any]] = []
    raw_html_by_doc_id: dict[str, str] = {}

    for row in manifest:
        output_file = row.get("output_file")
        if not output_file:
            continue
        html_path = RAW_DIR / str(output_file)
        if not html_path.exists():
            continue
        raw_html = html_path.read_text(encoding="utf-8", errors="ignore")
        clean_text = _clean_html(raw_html)
        if not clean_text:
            continue

        doc_id = row.get("sha256") or hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
        raw_html_by_doc_id[str(doc_id)] = raw_html
        fetched_at = str(row.get("fetched_at_utc", ""))
        last_scraped_date = fetched_at.split("T")[0] if "T" in fetched_at else fetched_at
        role = str(row.get("role", "scheme"))
        doc_type = "groww_amc_page" if role == "amc" else "groww_scheme_page"

        parsed_rows.append(
            {
                "doc_id": doc_id,
                "text": clean_text,
                "source_url": row.get("source_url"),
                "scheme_name": row.get("scheme_name"),
                "amc": row.get("amc"),
                "doc_type": doc_type,
                "last_scraped_date": last_scraped_date,
                "output_file": output_file,
                "sha256": row.get("sha256"),
            }
        )

    with PARSED_PATH.open("w", encoding="utf-8") as handle:
        for item in parsed_rows:
            handle.write(json.dumps(item, ensure_ascii=True) + "\n")

    holdings_count = _write_holdings_records(parsed_rows, raw_html_by_doc_id)
    returns_count = _write_returns_records(parsed_rows, raw_html_by_doc_id)
    print(f"Parsed documents: {len(parsed_rows)}")
    print(f"Output: {PARSED_PATH}")
    print(f"Holdings records: {holdings_count}")
    print(f"Output: {HOLDINGS_PATH}")
    print(f"Returns records: {returns_count}")
    print(f"Output: {RETURNS_PATH}")
    return parsed_rows


def main() -> None:
    parse_documents()


if __name__ == "__main__":
    main()
