# MF-RAG-Chatbot

RAG-based AI chatbot for mutual fund FAQs (Nippon India schemes on Groww).

This project is a RAG-based chatbot for Nippon India Mutual Fund scheme FAQs, using Groww URLs as the only ingestion/citation sources.

## Stack

- **Backend**: Python 3.11+, FastAPI, Pinecone, Groq (`llama-3.1-8b-instant`)
- **Frontend**: Next.js 14, React 18, Tailwind CSS
- **Legacy UI**: Streamlit (still available)

## Setup

### 1. Python backend

```bash
python3 -m pip install -r requirements.txt
```

### 2. Environment

```bash
cp .env.example .env
```

Required keys:

- `PINECONE_API_KEY`
- `PINECONE_INDEX`
- `PINECONE_NAMESPACE`
- `GROQ_API_KEY`

Recommended:

- `EMBEDDING_MODEL=llama-text-embed-v2`
- `GROQ_MODEL=llama-3.1-8b-instant`

### 3. Frontend

```bash
cd frontend
npm install
```

## Run Pipeline

### Phase 2: Scrape source pages

```bash
python3 -m src.ingestion.scraper
```

### Phase 3: Parse, chunk, embed, upsert

```bash
python3 -m src.ingestion.embedder
```

## Run App (Next.js + FastAPI)

Start the API server (from project root):

```bash
uvicorn src.api:app --reload --port 8000
```

Start the Next.js frontend (in a separate terminal):

```bash
cd frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

To preview the **production static bundle** locally (same output Vercel serves):

```bash
cd frontend
npm run build
npm start
```

### Legacy Streamlit UI

```bash
streamlit run src/app.py
```

## Phase 8 Evaluation

Evaluation dataset:

- `tests/test_queries.yaml`

Runner:

- `scripts/run_evaluation.py`

Run:

```bash
python3 scripts/run_evaluation.py
```

Output report:

- `data/processed/evaluation_report.json`

The report includes:

- retrieval hit rate
- answer correctness (strict + semantic)
- citation presence rate
- guardrail pass rate
- latency summary (avg + p95)

## Notes

- Guardrails block PII, advice, and return-projection/comparison asks.
- Holdings/sector answers are generated from structured records in `data/processed/holdings_records.jsonl`.
- Scheduler workflow is in `.github/workflows/daily-ingestion.yml`.

## Repository

https://github.com/mukbathija196/MF-RAG-Chatbot
