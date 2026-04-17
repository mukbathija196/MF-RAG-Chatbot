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
│  │ Preprocessor  │    │ (same model) │    │  (Pinecone)               │  │
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
│               │   LLM (Groq-hosted Llama) │                              │
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
│  Triggers workflow → scrape → parse → chunk → embed → upsert Pinecone    │
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
│                                          │  Vector Store (Pinecone) │  │
│                                          │  cloud index             │  │
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
| **A. Artifact only** | Upload `data/raw/` + `data/processed/` as workflow **artifacts** (short retention, e.g. **1 day**) | CI proof and debugging trail |
| **B. Pinecone direct** | Ingestion job upserts vectors directly into Pinecone namespace/index | Recommended for this project |
| **C. Commit manifests** | Commit only manifests/debug outputs; keep vectors in Pinecone | Keeps repo small |
| **D. Blob / object storage** | Store parsed/chunked intermediates in S3/GCS if needed | Production observability |

**Operational defaults**

- **`concurrency`:** `group: ingestion` + `cancel-in-progress: false` so overlapping days do not corrupt a half-written store (or use a lock file inside the job).
- **Failures:** `continue-on-error: false` on the ingestion step; optional Slack / email via third-party action on failure.
- **Secrets:** Store `PINECONE_API_KEY` (and index/region vars) in GitHub **Secrets**; add proxy creds only if needed.
- **Latest-only semantics:** Each successful run **replaces** prior data where it would otherwise linger: Pinecone namespace is cleared before upsert (`PINECONE_REPLACE_NAMESPACE`, default on), `data/raw/*.html` is pruned to match the new manifest, processed JSONL files are overwritten, and workflow artifacts use short retention. See `docs/deployment.md` §4.1.1.

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
    env:
      GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
      GROQ_MODEL: ${{ secrets.GROQ_MODEL }}
      PINECONE_API_KEY: ${{ secrets.PINECONE_API_KEY }}
      PINECONE_INDEX: ${{ secrets.PINECONE_INDEX }}
      PINECONE_NAMESPACE: ${{ secrets.PINECONE_NAMESPACE }}
      PINECONE_CLOUD: ${{ secrets.PINECONE_CLOUD }}
      PINECONE_REGION: ${{ secrets.PINECONE_REGION }}
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
          name: raw-and-manifest
          path: |
            data/raw
            data/processed
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

**Out of scope for scraper:** tokenization, chunking, embeddings (handled in §2.4). The scraper **must not** mutate Pinecone.

---

### 2.4 Chunking & embedding architecture

This is the **index build** stage: turn cleaned text into **vectorized chunks** stored in Pinecone. It runs **after** the scraping service (locally or in the same GitHub Actions job).

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
           │  embeddings (float32, dim = provider model output) + same metadata
           ▼
┌─────────────────────┐
│  Pinecone upsert    │  See §2.4.4 — namespace upsert strategy
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
| Model | `llama-text-embed-v2` |
| Vector dimension | **Match model output dimension** (set Pinecone index dimension accordingly) |
| Runtime | API-based embedding inference (no local GPU required) |
| Batch size | Provider/API dependent; use moderate request batching with retries |
| Normalization | Optional if using Pinecone cosine; keep consistent between index/query |
| Dtype | `float32` for storage |

**Alignment rule:** The **same** model weights and preprocessing must be used in **ingestion** (this section) and at **query time** (retrieval pipeline) so query and document vectors live in the same space.

**Alternative model (optional):** `text-embedding-3-small` (OpenAI) or local sentence-transformers model; if switched, re-embed the **full** corpus and bump `embedding_model_version` in metadata.

#### 2.4.4 Pinecone write strategy (scheduled refresh)

For this small corpus and daily refresh, use **namespace replace per run**:

1. Build vectors for all URLs.
2. Upsert into a timestamped namespace (for example `mf-faq-2026-04-14`).
3. After success, switch active namespace via config/env (for example `PINECONE_NAMESPACE`).
4. Delete old namespace(s) on retention policy (e.g. keep last 3).

This gives simple rollback and avoids partial-index corruption during a failed run.

---

### 2.5 Vector Store

| Property | Choice |
|----------|--------|
| Database | **Pinecone** (managed cloud vector DB) |
| Index name | `mf-faq-chunks` (example) |
| Namespace | `mf-faq-active` (switchable per refresh) |
| Distance metric | Cosine similarity |
| Index dimension | Must equal `llama-text-embed-v2` output dimension |
| Stored metadata per chunk | `scheme_name`, `amc`, `doc_type`, `source_url`, `last_scraped_date`, `chunk_index` |

**Why Pinecone?**
- Managed cloud index (no local persistence management).
- Easy metadata filtering (`scheme_name`, `doc_type`) at query time.
- Fits scheduled ingestion from GitHub Actions with stable API.

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
               │  query vector (same dim as index)
               ▼
┌────────────────────────────────┐
│  3. Vector search               │
│     Pinecone.query(             │
│       vector=query_embedding,   │
│       top_k = 10,               │
│       filter = {"scheme_name":  │
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

Blocks only projection/comparison queries (not factual scheme return lookups):

```
PERFORMANCE_BLOCK_SIGNALS = [
    "compare returns", "compare CAGR", "which is better",
    "higher return", "best return", "outperform", "vs",
    "if I invest", "how much profit", "future return",
    "expected return", "calculate XIRR", "calculate CAGR"
]
```

If matched → return:
> "I can't compute custom return projections or compare performance across funds. I can share factual return/CAGR values from source pages for a specific scheme."

If user asks factual scheme-level performance (for example, “What is the 3Y return of Nippon India Large Cap Fund?”), the query is **allowed** and processed normally via retrieval + generation.

---

### 2.8 Generation Pipeline

#### LLM Selection

| Property | Choice |
|----------|--------|
| Primary model | **Groq `llama-3.1-8b-instant`** |
| Fallback model | **Groq `llama-3.1-70b-versatile`** (quality fallback) |
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
| LLM | Groq Llama models | via `groq` SDK |
| Embeddings | `llama-text-embed-v2` | provider API |
| Re-ranker | cross-encoder (`ms-marco-MiniLM-L-6-v2`) | HuggingFace |
| Vector Store | Pinecone | cloud |
| PDF Parsing | PyMuPDF (`fitz`) | 1.24.x |
| Web Scraping | `requests` + `BeautifulSoup4`; Playwright if Groww is CSR-heavy | — |
| Chunk length | `tiktoken` (or LangChain token counter) aligned with splitter | optional dep |
| UI | Streamlit | 1.38.x |
| Pinecone SDK | `pinecone` | latest |
| Config | `python-dotenv` + environment variables | — |
| PII Detection | regex (built-in) | — |

---

## 3.1 Configuration & Environment Variables

Use environment variables for all runtime config that differs by environment (local vs CI). Keep `.env` local-only; use GitHub Secrets in Actions.

### 3.1.1 Required `.env` variables (local)

| Variable | Required | Example | Used in |
|----------|----------|---------|---------|
| `GROQ_API_KEY` | Yes (Phase 6+) | `gsk_...` | `src/generation/llm_client.py` |
| `GROQ_MODEL` | Recommended | `llama-3.1-8b-instant` | `src/generation/llm_client.py` |
| `PINECONE_API_KEY` | Yes (Phase 3+) | `pcsk_...` | `src/ingestion/embedder.py`, `src/retrieval/retriever.py` |
| `PINECONE_INDEX` | Yes (Phase 3+) | `mf-faq-chunks` | Ingestion + retrieval |
| `PINECONE_NAMESPACE` | Yes (Phase 3+) | `mf-faq-active` | Ingestion + retrieval |
| `PINECONE_CLOUD` | Recommended | `aws` | Pinecone client init |
| `PINECONE_REGION` | Recommended | `us-east-1` | Pinecone client init |
| `PINECONE_HOST` | Optional | `https://...pinecone.io` | Use host-based index connection instead of index-name lookup |
| `PINECONE_REPLACE_NAMESPACE` | Recommended | `1` | Ingestion: `delete_all` on namespace before upsert (`src/ingestion/embedder.py`). Set `0` only for local experiments. |
| `EMBEDDING_MODEL` | Recommended | `llama-text-embed-v2` | Ingestion + retrieval |
| `PLAYWRIGHT` | Optional | `1` / `true` | `src/ingestion/scraper.py` fallback behavior |
| `HTTP_USER_AGENT` | Optional | Browser UA string | Scraper requests header |

### 3.1.2 Optional scraping controls

| Variable | Default | Purpose |
|----------|---------|---------|
| `SCRAPER_CONNECT_TIMEOUT` | `30` | HTTP connect timeout (seconds) |
| `SCRAPER_READ_TIMEOUT` | `60` | HTTP read timeout (seconds) |
| `SCRAPER_RATE_LIMIT_SEC` | `1.5` | Delay between URL fetches |
| `SCRAPER_MAX_RETRIES` | `3` | Retry attempts for retriable failures |

### 3.1.3 GitHub Actions secrets mapping

Set these in **GitHub → Settings → Secrets and variables → Actions**:

| GitHub Secret | Exported env var in workflow |
|---------------|------------------------------|
| `GROQ_API_KEY` | `GROQ_API_KEY` |
| `GROQ_MODEL` | `GROQ_MODEL` |
| `PINECONE_API_KEY` | `PINECONE_API_KEY` |
| `PINECONE_INDEX` | `PINECONE_INDEX` |
| `PINECONE_NAMESPACE` | `PINECONE_NAMESPACE` |
| `PINECONE_CLOUD` | `PINECONE_CLOUD` |
| `PINECONE_REGION` | `PINECONE_REGION` |
| `PINECONE_HOST` | `PINECONE_HOST` *(optional)* |
| `EMBEDDING_MODEL` | `EMBEDDING_MODEL` |
| — | `PINECONE_REPLACE_NAMESPACE` — set literal `"1"` in workflow `env` (not a secret) |

### 3.1.4 Example `.env` template

```bash
GROQ_API_KEY=
GROQ_MODEL=llama-3.1-8b-instant
PINECONE_API_KEY=
PINECONE_INDEX=mf-faq-chunks
PINECONE_NAMESPACE=mf-faq-active
PINECONE_CLOUD=aws
PINECONE_REGION=us-east-1
# PINECONE_HOST=https://faq-chatbot-xxxx.svc.<env>.pinecone.io
PINECONE_REPLACE_NAMESPACE=1
EMBEDDING_MODEL=llama-text-embed-v2
PLAYWRIGHT=0
```

### 3.1.5 Example workflow env block

```yaml
jobs:
  ingest:
    runs-on: ubuntu-latest
    env:
      GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
      GROQ_MODEL: ${{ secrets.GROQ_MODEL }}
      PINECONE_API_KEY: ${{ secrets.PINECONE_API_KEY }}
      PINECONE_INDEX: ${{ secrets.PINECONE_INDEX }}
      PINECONE_NAMESPACE: ${{ secrets.PINECONE_NAMESPACE }}
      PINECONE_CLOUD: ${{ secrets.PINECONE_CLOUD }}
      PINECONE_REGION: ${{ secrets.PINECONE_REGION }}
      PINECONE_HOST: ${{ secrets.PINECONE_HOST }}
      EMBEDDING_MODEL: ${{ secrets.EMBEDDING_MODEL }}
      PINECONE_REPLACE_NAMESPACE: "1"
```

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
│   └── processed/               ← manifests, parsed/chunk debug outputs
├── src/
│   ├── ingestion/
│   │   ├── scraper.py           ← fetch Groww HTML (Playwright if needed)
│   │   ├── parser.py            ← extract/clean text from HTML
│   │   ├── chunker.py           ← split into overlapping chunks
│   │   └── embedder.py          ← embed & upsert to Pinecone
│   ├── retrieval/
│   │   ├── query_preprocessor.py
│   │   ├── retriever.py         ← vector search + metadata filter
│   │   └── reranker.py          ← cross-encoder re-ranking
│   ├── generation/
│   │   ├── prompt_templates.py  ← system + user prompt templates
│   │   ├── llm_client.py        ← Groq API wrapper
│   │   └── formatter.py         ← format answer + citation + date
│   ├── guardrails/
│   │   ├── pii_detector.py
│   │   ├── advice_classifier.py
│   │   └── performance_detector.py
│   └── app.py                   ← Streamlit entry point
├── config/
│   └── sources.yaml             ← Groww URLs only (Section 2.1)
├── .env                         ← Groq + Pinecone vars (see §3.1, gitignored)
├── .env.example                 ← template for required env vars
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
7.  Pinecone upsert (§2.4.4): write to new namespace, then switch active namespace
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
4.  Embed query → vector with same dimension as Pinecone index
5.  Pinecone similarity search (top_k=10, optional metadata filter)
6.  Cross-encoder re-rank → top 3 chunks
7.  Build prompt (system + context chunks + user query)
8.  Call Groq model (temp=0.1, max_tokens=300)
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
  │                          Pinecone indexed     search works
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
| 1.3 | Create `.env.example` with Groq + Pinecone placeholders (see §3.1) | `.env.example` |
| 1.4 | Create `.gitignore` (ignore `.env`, local caches, `__pycache__/`) | `.gitignore` |
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

**Goal:** Raw files parsed, chunked, embedded, and upserted to Pinecone. The vector index is queryable.

| Step | Action | Output |
|------|--------|--------|
| 3.1 | Implement `src/ingestion/parser.py` — extract clean text from HTML (BeautifulSoup). Strip boilerplate, normalize whitespace. (PyMuPDF optional if PDFs are added later.) | Clean text per document |
| 3.2 | Implement `src/ingestion/chunker.py` per §2.4.2 — splitter params, metadata, min-chunk filter | `List[Document]` |
| 3.3 | Implement `src/ingestion/embedder.py` per §2.4.3–2.4.4 — batched encode, namespace upsert to Pinecone | Pinecone namespace populated |
| 3.4 | Write a small smoke test: query Pinecone with a sample question, print top-5 chunks and their metadata | Verified retrieval from store |

**Deliverable:** Run `python -m src.ingestion.embedder` → Pinecone namespace has N vectors (expect roughly tens to low hundreds for seven Groww pages). Smoke-test query returns relevant chunks.

**Files touched:**
`src/ingestion/parser.py`, `src/ingestion/chunker.py`, `src/ingestion/embedder.py`

**Dependencies:** Phase 2 complete (`data/raw/` populated).

---

### Phase 4 — Retrieval Pipeline

**Goal:** Given a user query string, return the top-3 most relevant chunks with metadata and source URLs.

| Step | Action | Output |
|------|--------|--------|
| 4.1 | Implement `src/retrieval/query_preprocessor.py` — normalize query, attempt to extract scheme name for metadata filter | Cleaned query + optional filter dict |
| 4.2 | Implement `src/retrieval/retriever.py` — embed query, run Pinecone `query(top_k=10)` with optional metadata `filter`, apply similarity threshold (0.35) | Top-10 candidate chunks |
| 4.3 | Implement `src/retrieval/reranker.py` — load `cross-encoder/ms-marco-MiniLM-L-6-v2`, re-score each (query, chunk) pair, return top-3 | Top-3 chunks + scores + metadata |
| 4.4 | Write integration test: 5 sample queries → verify correct chunk appears in top-3 | Test results logged |

**Deliverable:** Call `retrieve("What is the exit load for Nippon India Small Cap Fund?")` → returns 3 chunks with `source_url` and `scheme_name` metadata. Manual inspection confirms relevance.

**Files touched:**
`src/retrieval/query_preprocessor.py`, `src/retrieval/retriever.py`, `src/retrieval/reranker.py`

**Dependencies:** Phase 3 complete (Pinecone namespace populated).

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
| 6.2 | Implement `src/generation/llm_client.py` — Groq API wrapper (`llama-3.1-8b-instant`, temp=0.1, max_tokens=300), reads key from `.env` | `generate(prompt) → raw_response` |
| 6.3 | Implement `src/generation/formatter.py` — extract answer text, append source link and last-updated timestamp | Formatted answer string |
| 6.4 | End-to-end test: hardcode 3 retrieved chunks → call LLM → verify answer ≤3 sentences, has citation, has date | Verified output |

**Deliverable:** `generate_answer(query, chunks)` returns a formatted string with answer + source + date. Tested with 3 queries.

**Files touched:**
`src/generation/prompt_templates.py`, `src/generation/llm_client.py`, `src/generation/formatter.py`

**Dependencies:** Phase 4 complete (retriever returns chunks). Needs `.env` with `GROQ_API_KEY`.

---

### Phase 7 — Streamlit UI

**Goal:** A working chat interface with welcome message, example questions, and disclaimer.

| Step | Action | Output |
|------|--------|--------|
| 7.1 | Implement `src/app.py` — Streamlit page config, title, welcome section with 3 clickable example questions, facts-only disclaimer | UI shell |
| 7.2 | Add `st.chat_input` + `st.chat_message` for conversational flow | Chat works visually |
| 7.3 | Wire input → guardrails → retrieval → generation → display. Show citation and date below each answer | Full pipeline in UI |
| 7.4 | Add a spinner/loading state while the LLM responds | UX polish |
| 7.5 | Handle edge cases: empty input, very long input, Pinecone returning 0 matches | Graceful error messages |

**Deliverable:** Run `streamlit run src/app.py` → chat UI appears, accepts questions, returns cited answers or guardrail rejections.

**Files touched:**
`src/app.py`

**Dependencies:** Phases 4, 5, 6 all complete.

---

### Phase 7b — Next.js Frontend + FastAPI

**Goal:** Production-quality chat UI matching the design spec (see `Sample UI/screen 5.png`), backed by a REST API.

| Step | Action | Output |
|------|--------|--------|
| 7b.1 | Create `src/api.py` — FastAPI REST server with `POST /api/chat` wrapping guardrails + generation | Backend API |
| 7b.2 | Scaffold `frontend/` — Next.js 14, TypeScript, Tailwind CSS | Project skeleton |
| 7b.3 | Build `frontend/src/app/page.tsx` — chat page matching screenshot: user bubble (teal, right-aligned), bot card (mint, robot icon, source link cards, copy/thumbs actions), example question tiles, rounded input bar with Send button | Chat UI |
| 7b.4 | Add `fastapi` + `uvicorn` to `requirements.txt` | Deps |

**Run locally:**
```
uvicorn src.api:app --reload --port 8000   # API
cd frontend && npm run dev                  # UI at localhost:3000
```

**Files touched:**
`src/api.py`, `frontend/` (new), `requirements.txt`, `README.md`

**Dependencies:** Phase 7 complete.

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
| 3 | Ingestion Pipeline | 3–4 hrs | Pinecone namespace populated with vectors | — |
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
| Vector DB | Pinecone | ChromaDB, FAISS | Managed cloud service, metadata filtering, easy CI integration |
| Embedding model | llama-text-embed-v2 | OpenAI text-embedding-3-small | Strong semantic quality; managed API workflow fits Pinecone cloud setup |
| Re-ranker | Cross-encoder | None | Dramatically improves precision for factual Q&A; cheap to run on 10 candidates |
| Chunk size | 500 tokens | 256 / 1000 | Balances fact density vs. context completeness for MF documents |
| LLM | Groq `llama-3.1-8b-instant` | Groq `llama-3.1-70b-versatile`, hosted alternatives | Fast, low-latency generation with good factual formatting |
| Guardrails | Rule-based (regex + keywords) | LLM-based classification | Deterministic, zero-latency, no false negatives for PII patterns |
| UI | **Next.js 14** (primary) + Streamlit (legacy) | Gradio, plain React | Next.js gives production-grade SSR, Tailwind styling matching design spec; FastAPI REST API decouples backend from UI. Streamlit kept for quick local testing. |
| Corpus / citations | Groww (7 URLs: AMC + 6 schemes) | AMC PDFs / SEBI | Explicit project scope: all ingested chunks cite one of the listed Groww URLs |
| Freshness | **GitHub Actions** daily **10:00** (§2.2) | Manual only | Automated scrape + full index rebuild keeps answers aligned with latest Groww copy |

---

## 8. Guardrail Response Examples

| User Query | Guardrail Triggered | Response |
|-----------|---------------------|----------|
| "My PAN is ABCDE1234F, show my investments" | PII Detector | "I can't process personal information. Please ask a factual question about mutual fund schemes." |
| "Should I invest in Nippon India Small Cap Fund?" | Advice Classifier | "I only provide factual information. For personalized advice, consult a SEBI-registered advisor. Resource: [AMFI guide](https://www.amfiindia.com/...)" |
| "Compare returns of Nippon India Small Cap vs Multi Cap" | Performance Detector | "I can't compute custom projections or compare performance across funds..." |
| "What is the 3Y return of Nippon India Large Cap Fund?" | None (valid factual query) | *proceeds to retrieval + generation* |
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

- **Multi-AMC support:** Expand corpus to 3–5 AMCs with namespace-per-AMC in Pinecone.
- **Different scheduler:** Migrate off GitHub Actions to Cloud Scheduler, Airflow, or a VM cron if you need private-network scraping or static egress IPs.
- **Hybrid search:** Combine BM25 (keyword) with dense retrieval for better recall on exact terms like fund codes.
- **Conversation memory:** Add LangChain `ConversationBufferWindowMemory` for follow-up questions.
- **Feedback loop:** Thumbs up/down on answers → log to improve re-ranker or fine-tune prompts.
- **Deployment:** Containerize with Docker; deploy on AWS/GCP with a managed vector DB (Pinecone/Weaviate).
