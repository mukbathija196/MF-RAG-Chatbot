"""Fetch Groww HTML corpus and write ingest manifest (Phase 2)."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_PATH = REPO_ROOT / "config" / "sources.yaml"
RAW_DIR = REPO_ROOT / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "ingest_manifest.jsonl"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
RETRY_BACKOFF_SECONDS = (2, 8, 32)


@dataclass
class SourceConfig:
    url: str
    role: str
    scheme_name: str


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    scheme = parsed.scheme or "https"
    if not parsed.netloc and parsed.path:
        parsed = urlparse(f"{scheme}://{parsed.path}")
    clean = parsed._replace(scheme=scheme, fragment="")
    return urlunparse(clean)


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "index"
    slug = path.replace("/", "_")
    return f"{slug}.html"


def _load_sources() -> tuple[str, list[SourceConfig]]:
    with SOURCES_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    amc = payload.get("amc", "Nippon India Mutual Fund")
    rows: list[SourceConfig] = []
    for item in payload.get("sources", []):
        rows.append(
            SourceConfig(
                url=_normalize_url(str(item["url"])),
                role=str(item.get("role", "scheme")),
                scheme_name=str(item.get("scheme_name", "Unknown Scheme")),
            )
        )
    return amc, rows


def _is_likely_spa_shell(html: str) -> bool:
    lowered = html.lower()
    if len(html.strip()) < 1200:
        return True
    heuristics = (
        "enable javascript",
        "loading...",
        "id=\"root\"",
        "id=\"__next\"",
    )
    return sum(1 for token in heuristics if token in lowered) >= 2


def _fetch_requests(
    url: str,
    timeout_connect: int,
    timeout_read: int,
    user_agent: str,
) -> tuple[int, str, str]:
    response = requests.get(
        url,
        headers={"User-Agent": user_agent},
        timeout=(timeout_connect, timeout_read),
        allow_redirects=True,
    )
    return response.status_code, response.text, response.url


def _fetch_playwright(url: str, timeout_read: int, user_agent: str) -> tuple[int, str, str]:
    # Imported lazily so requests-only mode works without browser install.
    from playwright.sync_api import sync_playwright

    timeout_ms = timeout_read * 1000
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=user_agent)
        page = context.new_page()
        resp = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        page.wait_for_timeout(1200)
        html = page.content()
        final_url = page.url
        status = resp.status if resp else 200
        context.close()
        browser.close()
        return status, html, final_url


def _fetch_with_retries(
    url: str,
    timeout_connect: int,
    timeout_read: int,
    user_agent: str,
    use_playwright: bool,
    max_retries: int,
) -> tuple[int | None, str | None, str | None, str | None]:
    attempts = max(1, min(max_retries, len(RETRY_BACKOFF_SECONDS)))
    for idx in range(attempts):
        try:
            status, html, final_url = _fetch_requests(url, timeout_connect, timeout_read, user_agent)
            if status in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"Retriable HTTP status {status}")
            if use_playwright and _is_likely_spa_shell(html):
                status, html, final_url = _fetch_playwright(url, timeout_read, user_agent)
            if not html or len(html.strip()) < 100:
                raise RuntimeError("Empty/short response body")
            return status, html, final_url, None
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            if idx < attempts - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[idx])
                continue
            return None, None, None, error
    return None, None, None, "Unknown fetch failure"


def run_scraper() -> list[dict[str, Any]]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    amc_name, sources = _load_sources()
    timeout_connect = int(float(os.getenv("SCRAPER_CONNECT_TIMEOUT", "30")))
    timeout_read = int(float(os.getenv("SCRAPER_READ_TIMEOUT", "60")))
    rate_limit = float(os.getenv("SCRAPER_RATE_LIMIT_SEC", "1.5"))
    max_retries = int(os.getenv("SCRAPER_MAX_RETRIES", "3"))
    user_agent = os.getenv("HTTP_USER_AGENT", DEFAULT_USER_AGENT)
    use_playwright = _env_bool("PLAYWRIGHT", False)

    manifest_rows: list[dict[str, Any]] = []
    for idx, src in enumerate(sources):
        status, html, final_url, error = _fetch_with_retries(
            url=src.url,
            timeout_connect=timeout_connect,
            timeout_read=timeout_read,
            user_agent=user_agent,
            use_playwright=use_playwright,
            max_retries=max_retries,
        )
        fetched_at = datetime.now(timezone.utc).isoformat()
        output_file = _slug_from_url(src.url)
        output_path = RAW_DIR / output_file
        byte_count = 0
        sha256 = None
        final_target = final_url or src.url

        if html:
            output_path.write_text(html, encoding="utf-8")
            encoded = html.encode("utf-8")
            byte_count = len(encoded)
            sha256 = hashlib.sha256(encoded).hexdigest()

        row = {
            "source_url": src.url,
            "final_url": final_target,
            "role": src.role,
            "scheme_name": src.scheme_name,
            "amc": amc_name,
            "fetched_at_utc": fetched_at,
            "http_status": status,
            "bytes": byte_count,
            "sha256": sha256,
            "output_file": output_file if html else None,
            "error": error,
        }
        manifest_rows.append(row)
        status_text = "OK" if not error else "FAILED"
        print(
            f"[{idx + 1}/{len(sources)}] {status_text} | "
            f"{src.url} -> {output_file if html else '-'} | bytes={byte_count}"
        )
        if idx < len(sources) - 1:
            time.sleep(rate_limit)

    with MANIFEST_PATH.open("w", encoding="utf-8") as handle:
        for row in manifest_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    _prune_stale_raw_html(manifest_rows)

    ok_count = sum(1 for row in manifest_rows if row["error"] is None)
    print(f"\nCompleted scrape: {ok_count}/{len(manifest_rows)} successful")
    print(f"Manifest written: {MANIFEST_PATH}")
    return manifest_rows


def _prune_stale_raw_html(manifest_rows: list[dict[str, Any]]) -> None:
    """Remove data/raw/*.html not referenced by this run so only the latest corpus remains."""
    keep: set[str] = set()
    for row in manifest_rows:
        name = row.get("output_file")
        if name and row.get("error") is None:
            keep.add(str(name))
    removed = 0
    for path in RAW_DIR.glob("*.html"):
        if path.name not in keep:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    if removed:
        print(f"Pruned {removed} stale raw HTML file(s) under {RAW_DIR}")


def main() -> None:
    run_scraper()


if __name__ == "__main__":
    main()
