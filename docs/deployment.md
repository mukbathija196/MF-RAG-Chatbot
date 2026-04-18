# Deployment Plan

This document describes how the Mutual Fund FAQ Chatbot is deployed across three managed
services. It complements `docs/architecture.md` and assumes the app has already been
built and tested locally.

## 1. Overview

| Component | Runtime | Host | Trigger |
|---|---|---|---|
| Scheduler (ingestion) | Python 3.11 | **GitHub Actions** | Daily cron + manual `workflow_dispatch` |
| Backend API (FastAPI) | Python 3.11 + Uvicorn | **Render** (Web Service) | Push to `main` → auto-deploy |
| Frontend (Next.js 14) | Node 20 | **Vercel** | Push to `main` → auto-deploy |
| Vector DB | Pinecone (managed) | Pinecone Cloud | Updated by scheduler |
| LLM | Groq API | Groq Cloud | Called by backend at request time |

Data flow at runtime:

```
Browser ──HTTPS──▶ Vercel (Next.js) ──HTTPS──▶ Render (FastAPI)
                                                   │
                                        ┌──────────┴──────────┐
                                        ▼                     ▼
                                    Pinecone                 Groq
                                (vector search)           (generation)

GitHub Actions (cron) ──▶ Scrape Groww ──▶ Parse/Chunk ──▶ Embed + Upsert ──▶ Pinecone
```

Nothing is deployed to our own servers. Processed artefacts in `data/processed/`
are used only during ingestion on GitHub Actions and by local evaluation; they are
not required on Render at runtime because the backend reads chunks from Pinecone.

## 2. Prerequisites

Accounts/API keys required:

- GitHub repository with Actions enabled
- Render account (free or starter tier)
- Vercel account (Hobby tier is sufficient)
- Pinecone project with a serverless index matching `PINECONE_INDEX`
- Groq API key with access to `GROQ_MODEL` (default `llama-3.1-8b-instant`)

Local requirements for deployment work:

- `git` with push access to the repository
- `render` CLI (optional) and `vercel` CLI (optional) — the web UIs work fine

## 3. Environment variables

Single source of truth: `.env.example`. The same variables are used across all three
environments, with environment-specific differences noted below.

### 3.1 Shared (all environments)

| Variable | Example | Notes |
|---|---|---|
| `GROQ_API_KEY` | `gsk_…` | Required for backend. Not needed on Vercel. |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Backend only. |
| `PINECONE_API_KEY` | `pcsk_…` | Required for backend + scheduler. |
| `PINECONE_INDEX` | `mf-faq-chunks` | Must exist before first deploy. |
| `PINECONE_NAMESPACE` | `faq-chatbot-active` | Active namespace served by the API. |
| `PINECONE_CLOUD` | `aws` | |
| `PINECONE_REGION` | `us-east-1` | |
| `PINECONE_HOST` | `https://<index>-<id>.svc.<env>.pinecone.io` | Optional but recommended. |
| `PINECONE_REPLACE_NAMESPACE` | `1` | **Scheduler / ingestion:** clear the namespace before upsert so stale chunk IDs (from prior HTML hashes) cannot pollute retrieval. Set to `0` only for local experiments on a shared namespace. |
| `EMBEDDING_MODEL` | `llama-text-embed-v2` | Required for scheduler + backend. |

### 3.2 Frontend (Vercel) only

| Variable | Example | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://mf-faq-api.onrender.com` | Public. Consumed in `frontend/src/app/page.tsx`. |

### 3.3 Scheduler (GitHub Actions) only

No extra variables. `PLAYWRIGHT=0` (default) is used so the scheduler relies on the
plain HTTP scraper. Enable Playwright only if Groww starts returning JS-only HTML.

## 4. Scheduler — GitHub Actions

### 4.1 Workflow

Already in place: `.github/workflows/daily-ingestion.yml`.

- Schedule: `cron: "30 4 * * *"` → 10:00 Asia/Kolkata (IST)
- Manual run: Actions tab → **Daily MF corpus refresh** → **Run workflow**
- Steps: checkout → setup Python 3.11 → `pip install -r requirements.txt` →
  `python scripts/run_full_ingestion.py` → upload `data/raw` and `data/processed`
  as an artifact for auditing (artifact retention: **1 day** — only the latest run matters)

### 4.1.1 “Only the latest” data per component

Each scheduled run is a **full replace** for the parts that would otherwise accumulate stale rows:

| Component | Behaviour |
|-----------|-----------|
| **Pinecone** (`PINECONE_NAMESPACE`) | Before upsert, all vectors in the namespace are deleted (`PINECONE_REPLACE_NAMESPACE=1`, default in code and workflow). Then the current chunks are embedded and upserted. |
| **`data/raw/*.html`** | After scrape, any HTML file not referenced by the new manifest is removed (URLs removed from `sources.yaml` or failed fetches no longer leave old files behind). |
| **`data/processed/*.jsonl`** | `parse_documents` / `build_chunks` **overwrite** `parsed_documents.jsonl`, `chunks.jsonl`, `holdings_records.jsonl`, and `returns_records.jsonl` each run — no merge with prior runs. |
| **GitHub Actions artifact** | `retention-days: 1` on `raw-and-processed` so old workflow bundles expire automatically. |

If the embed/upsert step fails **after** the namespace delete, Pinecone may be empty or partially filled until you re-run the workflow successfully.

### 4.2 One-time setup

In GitHub → **Settings → Secrets and variables → Actions**, add repository secrets
matching every variable in §3.1. Name them exactly as listed (case-sensitive).

### 4.3 Verification

1. Trigger **Run workflow** manually.
2. Confirm the job finishes with `Pipeline complete in …s`.
3. Download the `raw-and-processed` artifact and sanity-check
   `data/processed/returns_records.jsonl` and `data/processed/holdings_records.jsonl`.
4. Make sure Pinecone shows a fresh upsert count in the active namespace.

### 4.4 Failure handling

- Failures **before** the embed step (scrape / parse / chunk): Pinecone is **not**
  cleared yet, so production keeps the last successful vector snapshot.
- Failures **during or after** namespace delete / upsert: the namespace may be empty
  or incomplete until you fix the issue and **re-run the workflow** (or temporarily
  point the Render service at a backup namespace if you maintain one).
- Pinecone auth errors: rotate `PINECONE_API_KEY` and re-run the workflow.
- To pause ingestion, disable the workflow in the Actions UI. The frontend/backend
  continue to serve whatever vectors were last fully upserted.

## 5. Backend — Render

### 5.0 Deploy with Blueprint (recommended)

The repo includes [`render.yaml`](../render.yaml) at the root so you can provision the
API without re-typing build/start commands. The Blueprint uses **`plan: free`** so you
can run the API on Render’s [free web tier](https://render.com/docs/free) (idle spin-down,
monthly free instance hours, and outbound limits apply — fine for demos and light use).

If the UI still asks for a **payment method**, that is often workspace-level verification
or an upgrade prompt; try picking **Free** explicitly in the instance-type step, or create
the service via **New → Web Service** (not Blueprint) and choose **Free** there.

1. In [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**.
2. Connect GitHub (if needed) and select the **repository** that contains this `render.yaml`.
3. Confirm the detected **`render.yaml`** and click **Apply** (or **Connect** / **Deploy**
   depending on UI wording).
4. When prompted, set **secret** environment variables: `GROQ_API_KEY` and
   `PINECONE_API_KEY`. Add **`PINECONE_HOST`** in the service Environment tab if you use
   host-based Pinecone (recommended for serverless).
5. Adjust **non-secret** defaults in the service **Environment** tab if your index or
   namespace names differ from `render.yaml` (for example `PINECONE_NAMESPACE` must match
   the namespace your GitHub Actions ingest job upserts into).
6. Wait for the first deploy, then run the checks in §5.5.

**Python version on Render:** New Render Python services default to **3.14.x** unless
overridden, which breaks building **`greenlet`** and **`tiktoken`** from source. This
repo pins **`PYTHON_VERSION=3.11.11`** in `render.yaml` and adds `.python-version` /
`runtime.txt` so the build matches GitHub Actions. If you create the service **manually**
(not Blueprint), set **`PYTHON_VERSION`** to **`3.11.11`** in the service Environment tab
([Render Python version docs](https://render.com/docs/python-version)).

### 5.1 Service definition (manual alternative)

Create a new **Web Service** in Render pointing at the repository root (same settings as
the Blueprint).

| Setting | Value |
|---|---|
| Environment | Python 3 |
| Region | Oregon (or nearest to Pinecone region) |
| Branch | `main` |
| Root directory | *(leave empty — repo root)* |
| Build command | `pip install --upgrade pip setuptools wheel && pip install -r requirements.txt` |
| Start command | `uvicorn src.api:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/` |
| Instance type | **Free** (hobby) or **Starter** (paid) — Blueprint defaults to **Free**; upgrade if you hit limits ([free tier docs](https://render.com/docs/free)) |
| Auto-deploy | On for `main` |

### 5.2 Environment variables

Add every variable from §3.1 in Render → **Environment**, except ingestion-only
flags: **`PINECONE_REPLACE_NAMESPACE` is not used by the API** (only by
`scripts/run_full_ingestion.py` / GitHub Actions). Do **not** add
`NEXT_PUBLIC_API_URL` here — that is a frontend-only variable.

Also set **`PYTHON_VERSION=3.11.11`** here if you did not use the Blueprint (or if the
service was created before this repo pinned Python — see §5.0).

### 5.2.1 GitHub Actions artifacts and the API

**No — Render (or any host) does not read workflow artifacts.** Artifacts are stored on
GitHub for download/audit only; they are not attached to your web service.

What the backend **does** use after each scheduled ingest:

| Data | Updated by scheduler? | How the API sees it |
|------|------------------------|---------------------|
| **Vectors (chunks)** | Yes — upserted to Pinecone | **Always current** — retrieval queries Pinecone (`src/retrieval/retriever.py`). |
| **`holdings_records.jsonl` / `returns_records.jsonl`** | Regenerated in the runner, then uploaded as an artifact | **Only what was deployed from Git** — the API loads these from `data/processed/` on disk (`RETURNS_PATH`, `HOLDINGS_PATH` in the same module). They **do not** refresh on Render when a new artifact appears unless you **redeploy** or copy those files onto the server another way. |

So: **vector RAG answers track the scheduler**; **deterministic holdings/returns summaries** can lag the corpus until the app image includes fresh JSONL (e.g. trigger a Render deploy after ingest, or move structured fields into Pinecone/metadata in a future change).

### 5.3 CORS

`src/api.py` already permits `allow_origins=["*"]`. Once the Vercel URL is known,
tighten this to the Vercel production domain (plus preview pattern) for safety:

```python
allow_origins=[
    "https://<your-app>.vercel.app",
    "https://*.vercel.app",  # previews
]
```

### 5.4 Cold starts

Render Starter spins down on idle. The first request after a pause can take
10–20s. Options:
- Upgrade to a paid plan that doesn't spin down.
- Keep a lightweight external pinger hitting `/` every 10 minutes.

### 5.5 Verification

```
curl -s https://<service>.onrender.com/
curl -s -X POST https://<service>.onrender.com/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the expense ratio of Nippon India Large Cap Fund?"}'
```

The first call must return a JSON payload with `answer`, `sources`, and
`last_updated`.

## 6. Frontend — Vercel

### 6.1 Project definition

Create a new Vercel project from the same GitHub repository.

| Setting | Value |
|---|---|
| Framework preset | Next.js |
| Root directory | `frontend` |
| Build command | `next build` (default) |
| Output directory | `.next` (default) |
| Install command | `npm install` (default) |
| Node version | 20.x |

### 6.2 Environment variables

In Vercel → **Settings → Environment Variables**, add:

- `NEXT_PUBLIC_API_URL=https://<render-service>.onrender.com`

Scope it to all three environments (Production, Preview, Development) so preview
deploys work too.

### 6.3 Deploy

Pushing to `main` auto-deploys production; every PR creates a preview URL pointing
at the same backend. Vercel assigns a permanent
`https://<project>.vercel.app` domain.

### 6.4 Verification

1. Load `https://<project>.vercel.app`.
2. Click an example question tile.
3. Confirm the response renders with source links and a last-updated date.
4. Open browser devtools → Network and confirm the POST goes to the Render URL.

## 7. End-to-end release checklist

Follow this order when setting up from scratch or after major schema changes.

1. Pinecone: create/verify index `PINECONE_INDEX` matches the embedding model's
   dimensionality.
2. GitHub Actions: add secrets, run **Daily MF corpus refresh** manually, confirm
   Pinecone has fresh vectors in `PINECONE_NAMESPACE`.
3. Render: create the web service with env vars from §3.1, wait for first deploy,
   run the `/api/chat` smoke test in §5.5.
4. Vercel: create the project with `NEXT_PUBLIC_API_URL` pointing at Render, wait
   for the first deploy, run the UI smoke test in §6.4.
5. Render: tighten `allow_origins` to the Vercel domain and redeploy.
6. GitHub Actions: confirm the next scheduled daily run completes successfully.

## 8. Operations

### 8.1 Rollback

- Vercel: use the **Deployments** tab → **Promote to Production** on a previous
  working build.
- Render: use **Manual Deploy → Deploy previous commit** or redeploy any passing
  commit from history.
- GitHub Actions: revert the ingestion-relevant commit and re-run the workflow; the
  previous Pinecone namespace state remains live if you skipped the upsert.

### 8.2 Secrets rotation

For each key, update in:
1. GitHub → Actions secrets
2. Render → Environment variables (triggers automatic redeploy)
3. Vercel → Environment variables (only `NEXT_PUBLIC_API_URL` lives here)

### 8.3 Blue/green namespaces (optional)

To avoid serving partial data during re-ingestion, maintain two namespaces, e.g.
`faq-chatbot-active` and `faq-chatbot-staging`. The workflow upserts into
`staging`; a follow-up manual step flips the backend's `PINECONE_NAMESPACE` env
var on Render to `staging` and renames the old one to `active`. This is an
opt-in enhancement and is not required for day-one deployment.

## 9. Monitoring and alerting

Minimum viable observability (can be bolted on without code changes):

- GitHub Actions emails committer on workflow failure.
- Render has built-in log streaming and failed-deploy notifications.
- Vercel has built-in deploy and runtime logs.
- Optional: add an uptime monitor (UptimeRobot, BetterStack) hitting both
  `https://<vercel>.vercel.app` and `https://<render>.onrender.com/`.

## 10. Cost at this scale

All three platforms have free tiers that accommodate the current traffic
pattern (single scheduled ingestion per day, single-digit concurrent chat
users). Expect to move to paid tiers only if:

- Render cold starts become a UX problem → Render Starter/Standard (~$7/mo).
- Vercel bandwidth exceeds Hobby limits → Pro tier.
- Pinecone storage/queries exceed starter quota → pay-as-you-go.

Groq usage is billed per token; the backend already uses deterministic extraction
for most factual queries, so LLM calls are minimal.
