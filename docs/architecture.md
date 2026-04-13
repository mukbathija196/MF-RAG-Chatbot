# RAG Architecture — Mutual Fund FAQ Chatbot

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          USER INTERFACE (Streamlit)                      │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  Welcome line · 3 example questions · "Facts-only. No advice."   │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │                    Chat Input Box                           │  │  │
│  │  └─────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ user query
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         GUARDRAILS LAYER                                │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│  │  PII Detector     │  │ Advice / Opinion │  │  Performance-Claim   │  │
│  │  (regex + NER)    │  │  Classifier      │  │  Detector            │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ safe query
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        RETRIEVAL PIPELINE                               │
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────────────┐  │
│  │ Query         │───▶│ Embedding    │───▶│  Vector Similarity Search │  │
│  │ Preprocessor  │    │ (same model) │    │  (ChromaDB / FAISS)       │  │
│  └──────────────┘    └──────────────┘    └────────────┬──────────────┘  │
│                                                       │ top-k chunks    │
│                                           ┌───────────▼──────────────┐  │
│                                           │  Re-ranker (cross-enc.)  │  │
│                                           └───────────┬──────────────┘  │
│                                                       │ top-n chunks    │
│                                                       │ + source URLs   │
└───────────────────────────────────────────────────────┼─────────────────┘
                                                        │
                                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       GENERATION PIPELINE                               │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  Prompt Template                                                  │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │ System: You are a facts-only MF FAQ assistant. Answer in    │  │  │
│  │  │ ≤3 sentences. Include one source link. Refuse advice.       │  │  │
│  │  ├─────────────────────────────────────────────────────────────┤  │  │
│  │  │ Context: {retrieved_chunks_with_metadata}                   │  │  │
│  │  ├─────────────────────────────────────────────────────────────┤  │  │
│  │  │ User: {query}                                               │  │  │
│  │  └─────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                           │                                             │
│                           ▼                                             │
│               ┌───────────────────────┐                                 │
│               │   LLM (OpenAI GPT-4o) │                                 │
│               └───────────┬───────────┘                                 │
│                           │                                             │
│                           ▼                                             │
│               ┌───────────────────────┐                                 │
│               │  Output Formatter     │                                 │
│               │  (answer + citation   │                                 │
│               │   + last-updated tag) │                                 │
│               └───────────────────────┘                                 │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ formatted response
                                 ▼
                          Back to UI


========================= OFFLINE / INGESTION =========================

┌─────────────────────────────────────────────────────────────────────────┐
│  SCHEDULER — GitHub Actions (`schedule`: daily 10:00 — see §2.2 TZ)      │
│  Triggers workflow → scrape → parse → chunk → embed → refresh ChromaDB   │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ daily (or manual `workflow_dispatch`)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    SCRAPING SERVICE + INDEX BUILD                        │
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────────────┐  │
│  │ Scraping     │───▶│ Parser       │───▶│  Chunking service           │  │
│  │ service      │    │ (clean HTML) │    │  (§2.4 — tokenized splits)  │  │
│  │ (§2.3)       │    │              │    └───────────┬───────────────┘  │
│  └──────────────┘    └──────────────┘                │ chunks + meta   │
│         ▲                                            ▼                  │
│         │                                ┌───────────────────────────┐  │
│         │                                │  Embedding service         │  │
│  reads `sources.yaml`                    │  (§2.4 — batched vectors)  │  │
│  (7 Groww URLs)                          └───────────┬───────────────┘  │
│                                                    │ vectors          │
│                                          ┌─────────▼───────────────┐  │
│                                          │  Vector Store (ChromaDB) │  │
│                                          │  `data/vectorstore/`      │  │
│                                          └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Deep-Dive

### 2.1 Data Collection & Corpus

| # | Document Type | Source (this project) | Notes |
|---|--------------|------------------------|--------|
| 1 | AMC overview & fund listing context (HTML) | Groww | Single AMC page — ingest |
| 2 | Per-scheme facts, fees, risk, benchmarks (HTML) | Groww | One URL per scheme — ingest |

**Chosen AMC:** Nippon India Mutual Fund (public-facing pages on **Groww** are the **only** ingestion and citation sources for this build’s scope).

**Ingestion policy:** `config/sources.yaml` lists **exactly** the Groww URLs below. Every chunk’s `source_url` metadata must be one of these URLs so the assistant can cite them verbatim. The AMC’s own website ([mf.nipponindiaim.com](https://mf.nipponindiaim.com/)) is **out of scope** for ingestion unless you later expand `sources.yaml`.

**Scraper note:** Groww may render key content client-side. If `requests` + static HTML yields empty panels, use a headless browser (e.g. Playwright) in `scraper.py` / `parser.py` — still store the **Groww** URL as `source_url`.

**Official corpus (`config/sources.yaml`) — 7 URLs (1 AMC + 6 schemes)**

| # | Role | URL |
|---|------|-----|
| 1 | AMC | https://groww.in/mutual-funds/amc/nippon-india-mutual-funds |
| 2 | Nippon India Large Cap Fund — Direct Growth | https://groww.in/mutual-funds/nippon-india-large-cap-fund-direct-growth |
| 3 | Nippon India Small Cap Fund — Direct Growth | https://groww.in/mutual-funds/nippon-india-small-cap-fund-direct-growth |
| 4 | Nippon India ELSS Tax Saver Fund — Direct Growth | https://groww.in/mutual-funds/nippon-india-elss-tax-saver-fund-direct-growth |
| 5 | Nippon India Multi Cap Fund — Direct Growth | https://groww.in/mutual-funds/nippon-india-multi-cap-fund-direct-growth |
| 6 | Nippon India Index Fund — Nifty 50 Plan — Direct Growth | https://groww.in/mutual-funds/nippon-india-index-fund-nifty-50-plan-direct-growth |
| 7 | Nippon India Conservative Hybrid Fund — Direct Growth | https://groww.in/mutual-funds/nippon-india-conservative-hybrid-fund-direct-growth |

**Chosen schemes (6) — map each row to the matching Groww URL above**

| Scheme | Category |
|--------|----------|
| Nippon India Large Cap Fund | Large cap |
| Nippon India Small Cap Fund | Small cap |
| Nippon India ELSS Tax Saver Fund | ELSS / tax saver |
| Nippon India Multi Cap Fund | Multi cap |
| Nippon India Index Fund — Nifty 50 Plan | Index — Nifty 50 |
| Nippon India Conservative Hybrid Fund | Conservative hybrid |

**Target corpus size:** **7** seed HTML documents; expect on the order of **tens to low hundreds** of chunks after parsing (depends on how much text each page exposes to the scraper).

---

### 2.2 Scheduler service (GitHub Actions)

**Purpose:** Run the full **refresh pipeline** once per day so the vector store reflects the **latest** Groww HTML (fees, loads, labels, copy can change).

| Property | Choice |
|----------|--------|
| Platform | **GitHub Actions** |
| Workflow file | `.github/workflows/daily-ingestion.yml` (name can vary) |
| Trigger | `schedule` — **every day at 10:00** in the timezone you choose (see below) |
| Manual run | `workflow_dispatch` (optional) for on-demand refresh |

**Cron and timezone:** GitHub-hosted runners evaluate `cron` in **UTC**. Examples:

| Desired local time | Example `cron` (UTC) |
|--------------------|----------------------|
| 10:00 **UTC** daily | `0 10 * * *` |
| 10:00 **Asia/Kolkata** (IST, UTC+5:30) | `30 4 * * *` (04:30 UTC = 10:00 IST) |

Adjust if your “10 AM” is another region. Alternative: run the job at a fixed UTC time and document “data refresh time” in the UI footer.

**Typical job graph**

```
on: schedule + workflow_dispatch
  → checkout repo
  → setup Python 3.11
  → pip install -r requirements.txt (+ Playwright browser install if used)
  → run ingestion entrypoint (single script or Makefile target), e.g.:
        python scripts/run_full_ingestion.py
     which internally: scrape (§2.3) → parse → chunk → embed (§2.4)
  → persist outputs (see below)
```

**Outputs and persistence (pick one pattern for your deployment)**

| Pattern | What happens after embed | Good for |
|---------|---------------------------|----------|
| **A. Artifact only** | Upload `data/raw/` + `data/vectorstore/` as workflow **artifacts** (e.g. 14-day retention) | CI proof; not auto-consumed by Streamlit |
| **B. Commit to repo** | Commit updated Chroma + manifest on a branch or main | Simple demo; watch repo size / LFS |
| **C. Blob / object storage** | `aws s3 sync` or similar → app pulls on startup | Production-style |
| **D. Self-hosted runner** | Same machine as app; workflow writes to local disk Streamlit reads | Lowest latency |

**Operational defaults**

- **`concurrency`:** `group: ingestion` + `cancel-in-progress: false` so overlapping days do not corrupt a half-written store (or use a lock file inside the job).
- **Failures:** `continue-on-error: false` on the ingestion step; optional Slack / email via third-party action on failure.
- **Secrets:** Scraping usually needs no API keys; if you later add paid proxies, store tokens in GitHub **Secrets**.

**Minimal workflow sketch** (adjust cron for your timezone; install Playwright browsers if needed):

```yaml
# .github/workflows/daily-ingestion.yml
name: Daily MF corpus refresh
on:
  schedule:
    - cron: '0 10 * * *'   # 10:00 UTC daily — replace with IST or your TZ (see table above)
  workflow_dispatch: {}

concurrency:
  group: ingestion
  cancel-in-progress: false

jobs:
  ingest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      # - run: playwright install chromium   # uncomment if scraper uses Playwright
      - run: python scripts/run_full_ingestion.py
      - uses: actions/upload-artifact@v4
        with:
          name: vectorstore-and-raw
          path: |
            data/raw
            data/vectorstore
```

---

### 2.3 Scraping service

**Responsibility:** Given `config/sources.yaml`, **fetch the latest HTML** for each URL, save **raw snapshots** to `data/raw/`, and emit a **manifest** so downstream steps know what succeeded and when.

**Inputs**

| Input | Description |
|-------|-------------|
| `config/sources.yaml` | Ordered list of 7 Groww URLs (AMC + 6 schemes) |
| Config / env | Optional: timeout seconds, max retries, `PLAYWRIGHT=1` toggle |

**Processing flow**

```
For each URL in sources.yaml:
  1. Normalize URL (strip fragments, default https).
  2. Fetch:
       - Try HTTP GET + parse (fast path).
       - If body empty / SPA shell → Playwright: goto URL, wait for networkidle
         or a known selector, then dump `page.content()`.
  3. Retry with exponential backoff (e.g. 3 attempts: 2s, 8s, 32s) on 429/5xx/timeout.
  4. Rate-limit: sleep 1–2s between URLs to reduce blocking risk.
  5. Write raw HTML to data/raw/{slug}.html (slug from URL path or stable hash).
  6. Append row to data/raw/ingest_manifest.jsonl:
       { source_url, fetched_at_utc, http_status, bytes, sha256, error|null }
```

**HTTP client hygiene**

| Concern | Mitigation |
|---------|------------|
| Blocking / bot detection | Realistic **User-Agent**; gentle concurrency (sequential is fine for 7 URLs) |
| Timeouts | Connect + read timeouts (e.g. 30s / 60s) |
| TLS / redirects | Follow redirects; log final URL if it differs |

**Outputs**

| Output | Purpose |
|--------|---------|
| `data/raw/*.html` | Parser input; **one file per seed URL** after successful fetch |
| `data/raw/ingest_manifest.jsonl` | Audit trail; drives `last_scraped_date` in chunk metadata |

**Out of scope for scraper:** tokenization, chunking, embeddings (handled in §2.4). The scraper **must not** mutate ChromaDB.

---

### 2.4 Chunking & embedding architecture

This is the **index build** stage: turn cleaned text into **vectorized chunks** stored in ChromaDB. It runs **after** the scraping service (locally or in the same GitHub Actions job).

#### 2.4.1 End-to-end flow (chunking + embedding)

```
ingest_manifest.jsonl + *.html
        │
        ▼
┌─────────────────────┐
│  Parser             │  BeautifulSoup: main content, tables, lists;
│  (per file)         │  strip nav, cookie banners, script/style;
└──────────┬──────────┘  collapse whitespace; output plain text + doc-level meta
           │  { text, source_url, scheme_name, amc, doc_type, last_scraped_date }
           ▼
┌─────────────────────┐
│  Chunking service   │  See §2.4.2 — produces N overlapping chunks per document
└──────────┬──────────┘
           │  List[{ text, metadata }]  metadata includes chunk_index
           ▼
┌─────────────────────┐
│  Embedding service  │  See §2.4.3 — batch encode → L2-normalized vectors
└──────────┬──────────┘
           │  embeddings (float32, dim 384) + same metadata
           ▼
┌─────────────────────┐
│  Chroma upsert      │  See §2.4.4 — replace or versioned write
└─────────────────────┘
```

#### 2.4.2 Chunking service — design details

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Splitter | LangChain `RecursiveCharacterTextSplitter` | Respects paragraph / sentence boundaries before arbitrary cuts |
| `chunk_size` | **500** (token-oriented) | Fits a dense MF fact block + leaves room in the LLM context |
| `chunk_overlap` | **100** | Reduces boundary loss (e.g. exit load split across two chunks) |
| Length function | Prefer **token** length (e.g. `tiktoken` cl100k_base approximation) or LangChain token counter aligned with embedding model behavior | Keeps “500” meaningful vs character splits |
| `separators` | `["\n\n", "\n", ". ", " ", ""]` | Prefer breaks at paragraphs, then lines, then sentences |

**Metadata on every chunk (inherited or computed)**

| Field | Rule |
|-------|------|
| `source_url` | Canonical Groww URL for this document |
| `scheme_name` | From URL→scheme map in config (or `"Nippon India Mutual Fund (AMC)"` for AMC page) |
| `amc` | `"Nippon India Mutual Fund"` |
| `doc_type` | e.g. `groww_scheme_page` / `groww_amc_page` |
| `last_scraped_date` | ISO date from manifest `fetched_at_utc` |
| `chunk_index` | 0..N-1 within that `source_url` |

**Quality guards**

- Drop chunks shorter than **50 tokens** after strip (noise / boilerplate fragments).
- Optional: skip duplicate chunks (same normalized text hash) within a document.

#### 2.4.3 Embedding service — design details

| Property | Choice |
|----------|--------|
| Model | `sentence-transformers/all-MiniLM-L6-v2` |
| Vector dimension | **384** |
| Device | CPU in CI is sufficient; GPU optional locally |
| Batch size | **32–64** texts per `model.encode` call (tune to runner RAM) |
| Normalization | **L2-normalize** each vector (required for cosine distance in Chroma) |
| Dtype | `float32` for storage |

**Alignment rule:** The **same** model weights and preprocessing must be used in **ingestion** (this section) and at **query time** (retrieval pipeline) so query and document vectors live in the same space.

**Alternative model (optional):** `BAAI/bge-small-en-v1.5` — better retrieval quality, slightly heavier; if switched, re-embed the **full** corpus and bump a `embedding_model_version` in metadata.

#### 2.4.4 ChromaDB write strategy (scheduled refresh)

For a **small corpus** and a **daily full scrape**, the simplest correct approach is **full rebuild per run**:

1. After successful scrape + parse + chunk + embed for **all** URLs:
2. **Delete** the existing collection `mf_faq_chunks` (or drop the persist directory), then **recreate** and `add` all new chunks with embeddings.

This avoids orphan chunks from removed UI sections and keeps IDs stable within a run. If you need zero downtime, use **dual collections** (`mf_faq_chunks_active` / `_next`) and swap an alias file or env var after a successful build.

---

### 2.5 Vector Store

| Property | Choice |
|----------|--------|
| Database | **ChromaDB** (persistent mode) |
| Storage path | `data/vectorstore/` |
| Collection name | `mf_faq_chunks` |
| Distance metric | Cosine similarity |
| Stored metadata per chunk | `scheme_name`, `amc`, `doc_type`, `source_url`, `last_scraped_date`, `chunk_index` |

**Why ChromaDB?**
- Zero-config, file-based persistence — ideal for a prototype.
- Native LangChain integration.
- Metadata filtering support (filter by `scheme_name` or `doc_type` at query time).

---

### 2.6 Retrieval Pipeline

```
User Query: "What is the exit load for Nippon India Small Cap Fund?"
        │
        ▼
┌────────────────────────────────┐
│  1. Query Preprocessor          │
│     - Lowercase, strip noise    │
│     - Extract scheme name if    │
│       present → metadata filter │
└──────────────┬─────────────────┘
               │
               ▼
┌────────────────────────────────┐
│  2. Embed query                 │
│     (same model as ingestion)   │
└──────────────┬─────────────────┘
               │  query vector (384-d)
               ▼
┌────────────────────────────────┐
│  3. Vector search               │
│     ChromaDB.query(             │
│       query_embeddings,         │
│       n_results = 10,           │
│       where = {"scheme_name":   │
│         "Nippon India Small Cap Fund"} │   ← optional metadata filter
│     )                           │
└──────────────┬─────────────────┘
               │  top-10 chunks
               ▼
┌────────────────────────────────┐
│  4. Cross-encoder Re-ranker     │
│     Model: cross-encoder/       │
│       ms-marco-MiniLM-L-6-v2   │
│     Re-score (query, chunk)     │
│     Keep top-3                  │
└──────────────┬─────────────────┘
               │  top-3 chunks + metadata
               ▼
         Pass to LLM prompt
```

**Retrieval parameters:**

| Parameter | Value | Notes |
|-----------|-------|-------|
| Initial `k` (vector search) | 10 | Cast a wide net |
| Final `n` (after re-rank) | 3 | Keep the most relevant; fits in prompt window |
| Metadata filter | Optional | Applied when a scheme name is detected in the query |
| Similarity threshold | 0.35 | Chunks below this score are discarded before re-ranking |

---

### 2.7 Guardrails Layer

Three guardrails run **before** retrieval to reject unsafe queries early:

#### 2.7.1 PII Detector

```python
PII_PATTERNS = {
    "PAN":     r"[A-Z]{5}[0-9]{4}[A-Z]",
    "Aadhaar": r"\b\d{4}\s?\d{4}\s?\d{4}\b",
    "Phone":   r"\b[6-9]\d{9}\b",
    "Email":   r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
    "OTP":     r"\b\d{4,6}\b",  # combined with intent keywords
}
```

If any PII is detected → immediately return:
> "I can't process personal information like PAN, Aadhaar, phone numbers, or email addresses. Please ask a factual question about mutual fund schemes."

#### 2.7.2 Advice / Opinion Classifier

Keyword + intent approach:

```
ADVICE_SIGNALS = [
    "should I buy", "should I sell", "should I invest",
    "which is better", "recommend", "best fund",
    "portfolio", "allocate", "timing the market",
    "will it go up", "will it fall",
]
```

If matched → return:
> "I only provide factual information from official sources. For personalized investment advice, please consult a SEBI-registered investment advisor. Here's a helpful resource: [AMFI – How to Select a Mutual Fund](https://www.amfiindia.com/investor-corner/knowledge-center/how-to-select-MF.html)"

#### 2.7.3 Performance-Claim Detector

Catches queries asking to compute or compare returns:

```
PERFORMANCE_SIGNALS = [
    "returns", "CAGR", "XIRR", "performance",
    "compare returns", "how much profit",
    "annualized", "NAV growth",
]
```

If matched → return:
> "I don't compute or compare returns. For performance data, see the scheme’s Groww page: [link from chunk metadata source_url]."

---

### 2.8 Generation Pipeline

#### LLM Selection

| Property | Choice |
|----------|--------|
| Primary model | **OpenAI GPT-4o-mini** |
| Fallback model | **OpenAI GPT-3.5-turbo** (cost fallback) |
| Temperature | 0.1 (near-deterministic for factual Q&A) |
| Max output tokens | 300 |

#### Prompt Template

```
SYSTEM:
You are a facts-only mutual fund FAQ assistant for Indian mutual fund
schemes. You answer using ONLY the provided context chunks.

Rules:
1. Answer in ≤ 3 sentences.
2. Include exactly ONE source link from the chunk metadata.
3. End every answer with "Last updated from sources: <last_scraped_date>".
4. If the context does not contain the answer, say:
   "I don't have that information in my sources. Please check the
    official source pages: https://groww.in/mutual-funds/amc/nippon-india-mutual-funds"
5. NEVER give investment advice, opinions, or return comparisons.
6. NEVER fabricate facts not present in the context.

CONTEXT:
---
{chunk_1_text}
Source: {chunk_1_source_url} | Scheme: {chunk_1_scheme_name}
---
{chunk_2_text}
Source: {chunk_2_source_url} | Scheme: {chunk_2_scheme_name}
---
{chunk_3_text}
Source: {chunk_3_source_url} | Scheme: {chunk_3_scheme_name}
---

USER:
{user_query}
```

#### Output Format

```
<answer text — max 3 sentences>

📄 Source: <source_url>
🕐 Last updated from sources: <YYYY-MM-DD>
```

---

### 2.9 UI Layer (Streamlit)

```
┌──────────────────────────────────────────────────────┐
│  🏦 Mutual Fund FAQ Assistant                        │
│                                                      │
│  Welcome! Ask me factual questions about Nippon India │
│  Mutual Fund schemes (sources: Groww pages in scope). │
│  Examples:                                           │
│                                                      │
│  • "What is the expense ratio of Nippon India Large   │
│     Cap Fund?"                                       │
│  • "What is the ELSS lock-in for Nippon India ELSS    │
│     Tax Saver Fund?"                                 │
│  • "What is the exit load for Nippon India Small Cap  │
│     Fund?"                                           │
│                                                      │
│  ⚠️  Facts-only. No investment advice.               │
│──────────────────────────────────────────────────────│
│                                                      │
│  You: What is the exit load for Nippon India Small   │
│  Cap Fund?                                           │
│                                                      │
│  Bot: <answer from official source, ≤3 sentences>    │
│                                                      │
│  📄 Source: https://groww.in/mutual-funds/...        │
│  🕐 Last updated from sources: 2026-04-10           │
│                                                      │
│  ┌──────────────────────────────────────────┐  ┌──┐ │
│  │  Type your question...                   │  │➤ │ │
│  └──────────────────────────────────────────┘  └──┘ │
└──────────────────────────────────────────────────────┘
```

---

## 3. Tech Stack Summary

| Layer | Technology | Version |
|-------|-----------|---------|
| Language | Python | 3.11+ |
| Scheduler / CI | **GitHub Actions** (`schedule` + optional `workflow_dispatch`) | workflow YAML |
| Orchestration | LangChain | 0.2.x |
| LLM | OpenAI GPT-4o-mini | via `openai` SDK |
| Embeddings | sentence-transformers (`all-MiniLM-L6-v2`) | HuggingFace |
| Re-ranker | cross-encoder (`ms-marco-MiniLM-L-6-v2`) | HuggingFace |
| Vector Store | ChromaDB | 0.5.x |
| PDF Parsing | PyMuPDF (`fitz`) | 1.24.x |
| Web Scraping | `requests` + `BeautifulSoup4`; Playwright if Groww is CSR-heavy | — |
| Chunk length | `tiktoken` (or LangChain token counter) aligned with splitter | optional dep |
| UI | Streamlit | 1.38.x |
| Config | `python-dotenv` | — |
| PII Detection | regex (built-in) | — |

---

## 4. Project Directory Structure

```
project-root/
├── .github/
│   └── workflows/
│       └── daily-ingestion.yml  ← cron: daily 10:00 (see §2.2); runs full ingestion
├── scripts/
│   └── run_full_ingestion.py      ← entrypoint: scrape → parse → chunk → embed (§2.2–2.4)
├── docs/
│   └── architecture.md          ← this file
├── data/
│   ├── raw/                     ← Groww HTML snapshots (+ optional PDFs later)
│   ├── processed/               ← cleaned text files with metadata JSON
│   └── vectorstore/             ← ChromaDB persistent storage
├── src/
│   ├── ingestion/
│   │   ├── scraper.py           ← fetch Groww HTML (Playwright if needed)
│   │   ├── parser.py            ← extract text from PDF / HTML
│   │   ├── chunker.py           ← split into overlapping chunks
│   │   └── embedder.py          ← embed & store in ChromaDB
│   ├── retrieval/
│   │   ├── query_preprocessor.py
│   │   ├── retriever.py         ← vector search + metadata filter
│   │   └── reranker.py          ← cross-encoder re-ranking
│   ├── generation/
│   │   ├── prompt_templates.py  ← system + user prompt templates
│   │   ├── llm_client.py        ← OpenAI API wrapper
│   │   └── formatter.py         ← format answer + citation + date
│   ├── guardrails/
│   │   ├── pii_detector.py
│   │   ├── advice_classifier.py
│   │   └── performance_detector.py
│   └── app.py                   ← Streamlit entry point
├── config/
│   └── sources.yaml             ← Groww URLs only (Section 2.1)
├── .env                         ← OPENAI_API_KEY (gitignored)
├── .gitignore
├── requirements.txt
└── ProblemStatement.md
```

---

## 5. Data Flow — End to End

### 5.1 Ingestion (scheduled + manual)

**Path A — Scheduled (production of “latest data”)**

Triggered by **GitHub Actions** daily at **10:00** (timezone per cron in §2.2). The workflow calls the same entrypoint as local runs, e.g. `python scripts/run_full_ingestion.py`.

```
1.  Scheduler fires (GitHub Actions `schedule`)
2.  Read sources.yaml → 7 Groww URLs (AMC + 6 schemes)
3.  Scraping service (§2.3): fetch HTML → data/raw/*.html + ingest_manifest.jsonl
4.  Parser: clean text + doc-level metadata per file
5.  Chunking service (§2.4.2): 500 tok / 100 overlap → chunk list + metadata
6.  Embedding service (§2.4.3): batch encode → L2-normalized vectors
7.  ChromaDB write (§2.4.4): full collection rebuild (or blue/green swap)
8.  Publish artifacts / commit / upload per §2.2 persistence table
9.  Log stats: URLs OK/failed, chunk count, embed time, total duration
```

**Path B — Manual (developer laptop)**

Identical steps 2–9, run from the repo root without GitHub (used during Phase 2–3 development).

```
# example
python scripts/run_full_ingestion.py
```

### 5.2 Query (online, per user message)

```
1.  User types query in Streamlit
2.  Guardrails check:
      ├─ PII detected?        → reject with PII message
      ├─ Advice intent?       → reject with advice message
      └─ Performance claim?   → reject with Groww scheme-page link (from metadata)
3.  Query preprocessor:
      - Normalize text
      - Extract scheme name → optional metadata filter
4.  Embed query → 384-d vector
5.  ChromaDB similarity search (k=10, optional where filter)
6.  Cross-encoder re-rank → top 3 chunks
7.  Build prompt (system + context chunks + user query)
8.  Call GPT-4o-mini (temp=0.1, max_tokens=300)
9.  Format response:
      - Answer (≤3 sentences)
      - Source link
      - Last-updated timestamp
10. Display in Streamlit chat
```

---

## 6. Implementation Phases

Development is split into 8 phases. Each phase produces a **testable deliverable** before moving on. Phases are sequential — later phases depend on artifacts from earlier ones.

```
Phase 1       Phase 2         Phase 3            Phase 4
Setup    ───▶ Data        ───▶ Ingestion     ───▶ Retrieval
& Config      Collection       Pipeline           Pipeline
  │                               │                   │
  │                               ▼                   ▼
  │                          ChromaDB populated   search works
  │
  ▼
Phase 5       Phase 6         Phase 7            Phase 8
Guardrails ─▶ Generation  ───▶ UI             ───▶ Integration
               Pipeline        (Streamlit)         & Evaluation
                  │                │                   │
                  ▼                ▼                   ▼
             LLM answers      chat interface      end-to-end
             with citations   working             tested & polished
```

---

### Phase 1 — Project Setup & Configuration

**Goal:** Runnable project skeleton with all dependencies installable in one command.

| Step | Action | Output |
|------|--------|--------|
| 1.1 | Create the directory structure from Section 4 | All folders exist |
| 1.2 | Initialize `requirements.txt` with pinned versions | `requirements.txt` |
| 1.3 | Create `.env.example` with placeholder keys | `.env.example` |
| 1.4 | Create `.gitignore` (ignore `.env`, `data/vectorstore/`, `__pycache__/`) | `.gitignore` |
| 1.5 | Create `config/sources.yaml` with the seven Groww URLs from Section 2.1 | `config/sources.yaml` |
| 1.6 | Initialize git repo | `.git/` |
| 1.7 | Add `.github/workflows/daily-ingestion.yml` + `scripts/run_full_ingestion.py` per §2.2 | Scheduled + manual ingestion |

**Deliverable:** `pip install -r requirements.txt` succeeds; folder tree matches Section 4.

**Files touched:**
`requirements.txt`, `.env.example`, `.gitignore`, `config/sources.yaml`, `.github/workflows/daily-ingestion.yml`, `scripts/run_full_ingestion.py`

---

### Phase 2 — Data Collection

**Goal:** All seven Groww URLs from Section 2.1 saved locally as HTML snapshots with a manifest of what was collected.

| Step | Action | Output |
|------|--------|--------|
| 2.1 | Implement `src/ingestion/scraper.py` per §2.3 — reads `sources.yaml`, manifest, retries, Playwright fallback | `data/raw/*.html` |
| 2.2 | Add retry logic and logging for failed downloads | Console logs |
| 2.3 | Run the scraper; verify all 7 URLs in `sources.yaml` fetched and non-empty | `data/raw/` populated |

**Deliverable:** Run `python -m src.ingestion.scraper` → `data/raw/` reflects the full official corpus (Section 2.1). A printed summary shows file name, size, and source URL for each.

**Files touched:**
`src/ingestion/scraper.py`, `config/sources.yaml` (refinements)

**Dependencies:** Phase 1 complete.

---

### Phase 3 — Document Processing & Ingestion Pipeline

**Goal:** Raw files parsed, chunked, embedded, and stored in ChromaDB. The vector store is queryable.

| Step | Action | Output |
|------|--------|--------|
| 3.1 | Implement `src/ingestion/parser.py` — extract clean text from HTML (BeautifulSoup). Strip boilerplate, normalize whitespace. (PyMuPDF optional if PDFs are added later.) | Clean text per document |
| 3.2 | Implement `src/ingestion/chunker.py` per §2.4.2 — splitter params, metadata, min-chunk filter | `List[Document]` |
| 3.3 | Implement `src/ingestion/embedder.py` per §2.4.3–2.4.4 — batched encode, L2 norm, full Chroma rebuild | `data/vectorstore/` populated |
| 3.4 | Write a small smoke test: query ChromaDB with a sample question, print top-5 chunks and their metadata | Verified retrieval from store |

**Deliverable:** Run `python -m src.ingestion.embedder` → ChromaDB collection has N chunks (expect roughly tens to low hundreds for seven Groww pages). Smoke-test query returns relevant chunks.

**Files touched:**
`src/ingestion/parser.py`, `src/ingestion/chunker.py`, `src/ingestion/embedder.py`

**Dependencies:** Phase 2 complete (`data/raw/` populated).

---

### Phase 4 — Retrieval Pipeline

**Goal:** Given a user query string, return the top-3 most relevant chunks with metadata and source URLs.

| Step | Action | Output |
|------|--------|--------|
| 4.1 | Implement `src/retrieval/query_preprocessor.py` — normalize query, attempt to extract scheme name for metadata filter | Cleaned query + optional filter dict |
| 4.2 | Implement `src/retrieval/retriever.py` — embed query, run ChromaDB `.query(n_results=10)` with optional `where` filter, apply similarity threshold (0.35) | Top-10 candidate chunks |
| 4.3 | Implement `src/retrieval/reranker.py` — load `cross-encoder/ms-marco-MiniLM-L-6-v2`, re-score each (query, chunk) pair, return top-3 | Top-3 chunks + scores + metadata |
| 4.4 | Write integration test: 5 sample queries → verify correct chunk appears in top-3 | Test results logged |

**Deliverable:** Call `retrieve("What is the exit load for Nippon India Small Cap Fund?")` → returns 3 chunks with `source_url` and `scheme_name` metadata. Manual inspection confirms relevance.

**Files touched:**
`src/retrieval/query_preprocessor.py`, `src/retrieval/retriever.py`, `src/retrieval/reranker.py`

**Dependencies:** Phase 3 complete (ChromaDB populated).

---

### Phase 5 — Guardrails Layer

**Goal:** Three input guardrails that block unsafe queries before they reach retrieval.

| Step | Action | Output |
|------|--------|--------|
| 5.1 | Implement `src/guardrails/pii_detector.py` — regex patterns for PAN, Aadhaar, phone, email, OTP | `detect_pii(query) → (bool, message)` |
| 5.2 | Implement `src/guardrails/advice_classifier.py` — keyword/phrase matching for advice/opinion intent | `detect_advice(query) → (bool, message)` |
| 5.3 | Implement `src/guardrails/performance_detector.py` — keyword matching for return/performance queries | `detect_performance(query) → (bool, message)` |
| 5.4 | Create a unified `check_guardrails(query)` function that runs all three in order and returns the first failure (or `None` if safe) | Single entry point |
| 5.5 | Test with 10 unsafe + 10 safe queries — 100% correct classification | Test results |

**Deliverable:** `check_guardrails("My PAN is ABCDE1234F")` returns a rejection message. `check_guardrails("What is the expense ratio of Nippon India Small Cap Fund?")` returns `None`.

**Files touched:**
`src/guardrails/pii_detector.py`, `src/guardrails/advice_classifier.py`, `src/guardrails/performance_detector.py`, `src/guardrails/__init__.py`

**Dependencies:** None — can be developed in parallel with Phases 3–4.

---

### Phase 6 — Generation Pipeline

**Goal:** Given retrieved chunks and a user query, produce a factual, cited, formatted answer via the LLM.

| Step | Action | Output |
|------|--------|--------|
| 6.1 | Implement `src/generation/prompt_templates.py` — system prompt + context + user query template (as defined in Section 2.8) | Prompt builder function |
| 6.2 | Implement `src/generation/llm_client.py` — OpenAI API wrapper (`GPT-4o-mini`, temp=0.1, max_tokens=300), reads key from `.env` | `generate(prompt) → raw_response` |
| 6.3 | Implement `src/generation/formatter.py` — extract answer text, append source link and last-updated timestamp | Formatted answer string |
| 6.4 | End-to-end test: hardcode 3 retrieved chunks → call LLM → verify answer ≤3 sentences, has citation, has date | Verified output |

**Deliverable:** `generate_answer(query, chunks)` returns a formatted string with answer + source + date. Tested with 3 queries.

**Files touched:**
`src/generation/prompt_templates.py`, `src/generation/llm_client.py`, `src/generation/formatter.py`

**Dependencies:** Phase 4 complete (retriever returns chunks). Needs `.env` with `OPENAI_API_KEY`.

---

### Phase 7 — Streamlit UI

**Goal:** A working chat interface with welcome message, example questions, and disclaimer.

| Step | Action | Output |
|------|--------|--------|
| 7.1 | Implement `src/app.py` — Streamlit page config, title, welcome section with 3 clickable example questions, facts-only disclaimer | UI shell |
| 7.2 | Add `st.chat_input` + `st.chat_message` for conversational flow | Chat works visually |
| 7.3 | Wire input → guardrails → retrieval → generation → display. Show citation and date below each answer | Full pipeline in UI |
| 7.4 | Add a spinner/loading state while the LLM responds | UX polish |
| 7.5 | Handle edge cases: empty input, very long input, ChromaDB returning 0 results | Graceful error messages |

**Deliverable:** Run `streamlit run src/app.py` → chat UI appears, accepts questions, returns cited answers or guardrail rejections.

**Files touched:**
`src/app.py`

**Dependencies:** Phases 4, 5, 6 all complete.

---

### Phase 8 — Integration Testing & Evaluation

**Goal:** Validate the full system against the evaluation criteria from Section 8, fix issues, finalize.

| Step | Action | Output |
|------|--------|--------|
| 8.1 | Create `tests/test_queries.yaml` — 20 factual queries with expected answers/source URLs + 10 guardrail test cases | Test dataset |
| 8.2 | Run all 20 factual queries through the full pipeline. Record: retrieval hit (correct chunk in top-3), answer correctness, citation present, latency | Evaluation report |
| 8.3 | Run all 10 guardrail cases. Verify 100% block rate and 0% false-block on factual queries | Guardrail report |
| 8.4 | Fix any retrieval misses (adjust chunk size, overlap, or metadata), prompt issues (refine template), or guardrail false positives | Code refinements |
| 8.5 | Add error handling and logging across all modules | Robust error paths |
| 8.6 | Write `README.md` with setup instructions, usage guide, and architecture overview | `README.md` |
| 8.7 | Final cleanup: remove dead code, ensure `.env` is gitignored, verify requirements.txt is complete | Clean repo |

**Deliverable:** All evaluation targets met (≥85% retrieval accuracy, ≥90% answer correctness, 100% citation accuracy, <5s latency). README complete. Repo is clean and runnable from scratch.

**Files touched:**
`tests/test_queries.yaml`, `README.md`, various `src/` files (bug fixes)

**Dependencies:** Phase 7 complete.

---

### Phase Summary

| Phase | Name | Est. Effort | Key Output | Can Parallelize? |
|-------|------|-------------|------------|-------------------|
| 1 | Project Setup & Config | 1–2 hrs | Installable skeleton | — |
| 2 | Data Collection | 2–3 hrs | Corpus from Section 2.1 in `data/raw/` | — |
| 3 | Ingestion Pipeline | 3–4 hrs | ChromaDB populated with chunks | — |
| 4 | Retrieval Pipeline | 2–3 hrs | Top-3 retrieval working | — |
| 5 | Guardrails | 1–2 hrs | Input safety checks | Yes (with 3 & 4) |
| 6 | Generation Pipeline | 2–3 hrs | LLM answers with citations | — |
| 7 | Streamlit UI | 2–3 hrs | Working chat interface | — |
| 8 | Integration & Evaluation | 3–4 hrs | Tested, polished, documented | — |
| | **Total** | **~16–24 hrs** | | |

---

## 7. Key Design Decisions

| Decision | Choice | Alternative Considered | Why |
|----------|--------|----------------------|-----|
| Vector DB | ChromaDB | FAISS, Pinecone | Zero-config persistence; metadata filtering; free; LangChain native |
| Embedding model | all-MiniLM-L6-v2 | OpenAI text-embedding-3-small | Runs locally, no API cost, fast inference for small corpus |
| Re-ranker | Cross-encoder | None | Dramatically improves precision for factual Q&A; cheap to run on 10 candidates |
| Chunk size | 500 tokens | 256 / 1000 | Balances fact density vs. context completeness for MF documents |
| LLM | GPT-4o-mini | GPT-4o, Llama-3 | Best cost/quality ratio for constrained factual generation |
| Guardrails | Rule-based (regex + keywords) | LLM-based classification | Deterministic, zero-latency, no false negatives for PII patterns |
| UI | Streamlit | Gradio, React | Fastest to prototype; native chat component; Python-only stack |
| Corpus / citations | Groww (7 URLs: AMC + 6 schemes) | AMC PDFs / SEBI | Explicit project scope: all ingested chunks cite one of the listed Groww URLs |
| Freshness | **GitHub Actions** daily **10:00** (§2.2) | Manual only | Automated scrape + full index rebuild keeps answers aligned with latest Groww copy |

---

## 8. Guardrail Response Examples

| User Query | Guardrail Triggered | Response |
|-----------|---------------------|----------|
| "My PAN is ABCDE1234F, show my investments" | PII Detector | "I can't process personal information. Please ask a factual question about mutual fund schemes." |
| "Should I invest in Nippon India Small Cap Fund?" | Advice Classifier | "I only provide factual information. For personalized advice, consult a SEBI-registered advisor. Resource: [AMFI guide](https://www.amfiindia.com/...)" |
| "Compare returns of Nippon India Small Cap vs Multi Cap" | Performance Detector | "I don't compute or compare returns. See the scheme pages on Groww: [link]" |
| "What is the expense ratio of Nippon India ELSS Tax Saver Fund?" | None (valid query) | *proceeds to retrieval + generation* |

---

## 9. Evaluation Strategy

| Metric | How | Target |
|--------|-----|--------|
| **Retrieval accuracy** | 20 hand-crafted test queries; check if correct chunk is in top-3 | ≥ 85% |
| **Answer correctness** | Manual review: does the answer match the source document? | ≥ 90% |
| **Citation accuracy** | Does every answer include a valid, working source link? | 100% |
| **Guardrail precision** | Test 10 PII / advice / performance queries | 100% block rate |
| **Guardrail recall** | Test 20 valid factual queries | 0% false-block rate |
| **Latency** | End-to-end response time | < 5 seconds |
| **Hallucination rate** | Claims not grounded in retrieved chunks | 0% (target) |

---

## 10. Future Enhancements (Out of Scope for MVP)

- **Multi-AMC support:** Expand corpus to 3–5 AMCs with collection-per-AMC in ChromaDB.
- **Different scheduler:** Migrate off GitHub Actions to Cloud Scheduler, Airflow, or a VM cron if you need private-network scraping or static egress IPs.
- **Hybrid search:** Combine BM25 (keyword) with dense retrieval for better recall on exact terms like fund codes.
- **Conversation memory:** Add LangChain `ConversationBufferWindowMemory` for follow-up questions.
- **Feedback loop:** Thumbs up/down on answers → log to improve re-ranker or fine-tune prompts.
- **Deployment:** Containerize with Docker; deploy on AWS/GCP with a managed vector DB (Pinecone/Weaviate).
