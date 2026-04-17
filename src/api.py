"""FastAPI backend for the Mutual Fund FAQ chatbot."""

import re
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.generation.llm_client import generate_answer_struct
from src.guardrails import check_guardrails

app = FastAPI(title="MF FAQ Chatbot API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_QUERY_LEN = 500


class ChatRequest(BaseModel):
    query: str


class SourceLink(BaseModel):
    url: str
    label: str


class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceLink]
    last_updated: Optional[str] = None
    blocked: bool
    guardrail_message: Optional[str] = None


_SOURCE_RE = re.compile(r"\U0001f4c4 Source:\s*(.+)")
_DATE_RE = re.compile(r"\U0001f550 Last updated from sources:\s*(.+)")


@app.get("/")
def root():
    return {
        "service": "MF FAQ Chatbot API",
        "status": "ok",
        "chat_endpoint": "/api/chat",
        "frontend_url": "http://localhost:3000",
    }


def _parse_formatted_answer(formatted):
    source_url = None
    last_updated = None
    answer_lines = []
    for line in formatted.split("\n"):
        m_src = _SOURCE_RE.match(line)
        m_date = _DATE_RE.match(line)
        if m_src:
            source_url = m_src.group(1).strip()
        elif m_date:
            last_updated = m_date.group(1).strip()
        else:
            answer_lines.append(line)
    return "\n".join(answer_lines).strip(), source_url, last_updated


def _url_to_label(url):
    match = re.search(r"groww\.in/mutual-funds/(.+?)(?:-direct-growth)?$", url)
    if match:
        slug = match.group(1).replace("-", " ").replace("amc/", "")
        return slug.title()
    if "groww.in" in url:
        return "Groww"
    return url[:60]


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    query = req.query.strip()
    if not query:
        return ChatResponse(
            answer="Please enter a question.",
            sources=[],
            last_updated=None,
            blocked=False,
            guardrail_message=None,
        )
    if len(query) > MAX_QUERY_LEN:
        return ChatResponse(
            answer=f"Question too long ({len(query)} chars). Keep under {MAX_QUERY_LEN}.",
            sources=[],
            last_updated=None,
            blocked=False,
            guardrail_message=None,
        )

    guardrail = check_guardrails(query)
    if guardrail["blocked"]:
        return ChatResponse(
            answer="",
            sources=[],
            last_updated=None,
            blocked=True,
            guardrail_message=guardrail["reason"],
        )

    result = generate_answer_struct(query)
    answer_text, source_url, last_updated = _parse_formatted_answer(
        result.get("answer", "")
    )
    sources = []
    if source_url:
        sources.append(SourceLink(url=source_url, label=_url_to_label(source_url)))
    amc_url = "https://groww.in/mutual-funds/amc/nippon-india-mutual-funds"
    if source_url and source_url != amc_url:
        sources.append(SourceLink(url=amc_url, label="Nippon India Mutual Funds"))

    return ChatResponse(
        answer=answer_text,
        sources=sources,
        last_updated=last_updated,
        blocked=False,
        guardrail_message=None,
    )
