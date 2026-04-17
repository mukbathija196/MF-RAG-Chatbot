"""Phase 7 Streamlit UI for end-to-end chat testing."""

from __future__ import annotations

import streamlit as st

from src.generation.llm_client import generate_answer_struct
from src.guardrails import check_guardrails

st.set_page_config(page_title="Mutual Fund FAQ Chatbot", page_icon="💬", layout="centered")

EXAMPLE_QUESTIONS = [
    "What is the expense ratio of Nippon India Large Cap Fund?",
    "What is the exit load for Nippon India Small Cap Fund?",
    "What is the minimum SIP for Nippon India ELSS Tax Saver Fund?",
]
MAX_INPUT_CHARS = 500

st.title("Mutual Fund FAQ Assistant")
st.write(
    "Welcome! Ask factual questions about Nippon India Mutual Fund schemes "
    "using the configured Groww sources."
)
st.info("Facts-only assistant. No investment advice, projections, or personalization.")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "Ask a question to test the end-to-end flow "
                "(guardrails -> retrieval -> generation)."
            ),
        }
    ]
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None


def _add_assistant_message(text: str) -> None:
    st.session_state.messages.append({"role": "assistant", "content": text})
    with st.chat_message("assistant"):
        st.markdown(text)


def _handle_query(raw_query: str) -> None:
    query = raw_query.strip()
    if not query:
        _add_assistant_message("Please enter a question.")
        return
    if len(query) > MAX_INPUT_CHARS:
        _add_assistant_message(
            f"Your question is too long ({len(query)} chars). "
            f"Please keep it under {MAX_INPUT_CHARS} characters."
        )
        return

    guardrail_result = check_guardrails(query)
    if guardrail_result["blocked"]:
        _add_assistant_message(guardrail_result["reason"])
        return

    with st.spinner("Searching sources and generating answer..."):
        result = generate_answer_struct(query)
    answer = result.get("answer", "").strip()
    if not answer:
        answer = (
            "I don't have that information in my sources. Please check the official source pages: "
            "https://groww.in/mutual-funds/amc/nippon-india-mutual-funds"
        )
    _add_assistant_message(answer)


st.subheader("Try an example")
cols = st.columns(3)
for idx, question in enumerate(EXAMPLE_QUESTIONS):
    if cols[idx].button(f"Example {idx + 1}", use_container_width=True):
        st.session_state.pending_query = question
st.caption(
    f"Examples: {EXAMPLE_QUESTIONS[0]} | {EXAMPLE_QUESTIONS[1]} | {EXAMPLE_QUESTIONS[2]}"
)

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

user_input = st.chat_input("Ask your question...")
if st.session_state.pending_query:
    pending = st.session_state.pending_query
    st.session_state.pending_query = None
    st.session_state.messages.append({"role": "user", "content": pending})
    with st.chat_message("user"):
        st.markdown(pending)
    _handle_query(pending)
elif user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
    _handle_query(user_input)
