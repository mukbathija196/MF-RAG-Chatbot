"use client";

import { useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const EXAMPLES = [
  "What is the expense ratio of Nippon India Large Cap Fund?",
  "What is the exit load for Nippon India Small Cap Fund?",
  "Top holdings of Nippon India Large Cap Fund?",
];

interface Source {
  url: string;
  label: string;
}

interface Message {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
  lastUpdated?: string;
}

/* ------------------------------------------------------------------ */
/*  Icons                                                              */
/* ------------------------------------------------------------------ */

function IconSend() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

function IconExternal() {
  return (
    <svg className="w-3.5 h-3.5 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </svg>
  );
}

function IconCopy() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function IconThumbUp() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
    </svg>
  );
}

function IconThumbDown() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
    </svg>
  );
}

function IconUser() {
  return (
    <svg className="w-5 h-5 text-gray-500" fill="currentColor" viewBox="0 0 24 24">
      <path d="M12 12c2.7 0 4.8-2.1 4.8-4.8S14.7 2.4 12 2.4 7.2 4.5 7.2 7.2 9.3 12 12 12zm0 2.4c-3.2 0-9.6 1.6-9.6 4.8v2.4h19.2v-2.4c0-3.2-6.4-4.8-9.6-4.8z" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page                                                          */
/* ------------------------------------------------------------------ */

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function sendMessage(query: string) {
    const trimmed = query.trim();
    if (!trimmed || loading) return;
    setMessages((prev) => [...prev, { role: "user", text: trimmed }]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: trimmed }),
      });
      const data = await res.json();
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: data.blocked ? data.guardrail_message : data.answer,
          sources: data.sources || [],
          lastUpdated: data.last_updated || undefined,
        },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: "Something went wrong. Please try again." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleCopy(text: string, idx: number) {
    navigator.clipboard.writeText(text);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 1500);
  }

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <header className="text-center pt-6 pb-4">
        <h1 className="text-2xl font-bold text-gray-800 tracking-tight">
          Mutual Fund AI Assistant
        </h1>
      </header>

      {/* Chat area */}
      <main className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto space-y-5 pb-4">
          {messages.length === 0 && !loading && <WelcomeMessage />}

          {messages.map((msg, i) =>
            msg.role === "user" ? (
              <UserBubble key={i} text={msg.text} />
            ) : (
              <BotBubble
                key={i}
                msg={msg}
                onCopy={() => handleCopy(msg.text, i)}
                copied={copiedIdx === i}
              />
            ),
          )}

          {loading && <TypingIndicator />}

          <div ref={endRef} />
        </div>
      </main>

      {/* Bottom: examples + input */}
      <footer className="w-full max-w-3xl mx-auto px-4 pb-5 pt-2">
        {/* Example tiles */}
        <div className="flex flex-wrap justify-center gap-2.5 mb-3">
          {EXAMPLES.map((q, i) => (
            <button
              key={i}
              onClick={() => sendMessage(q)}
              disabled={loading}
              className="bg-white border border-gray-200 rounded-full px-4 py-2 text-xs text-gray-500 hover:border-teal-500 hover:text-teal-700 transition-colors shadow-sm disabled:opacity-50"
            >
              {q}
            </button>
          ))}
        </div>

        {/* Input bar */}
        <div className="flex items-center gap-2 bg-white rounded-full shadow-lg px-4 py-1.5">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") sendMessage(input);
            }}
            placeholder="Ask about Nippon India Mutual Funds..."
            className="flex-1 outline-none text-sm text-gray-700 placeholder-gray-400 bg-transparent py-2"
            disabled={loading}
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={loading || !input.trim()}
            className="bg-teal-600 hover:bg-teal-700 disabled:opacity-40 text-white text-sm font-medium pl-4 pr-3 py-2 rounded-full transition-colors flex items-center gap-1.5"
          >
            Send
            <IconSend />
          </button>
        </div>
      </footer>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end items-start gap-2.5">
      <div className="bg-teal-600 text-white px-5 py-3 rounded-2xl rounded-tr-sm max-w-md text-sm leading-relaxed shadow">
        {text}
      </div>
      <div className="w-8 h-8 rounded-full bg-gray-200 flex items-center justify-center flex-shrink-0">
        <IconUser />
      </div>
    </div>
  );
}

function BotBubble({
  msg,
  onCopy,
  copied,
}: {
  msg: Message;
  onCopy: () => void;
  copied: boolean;
}) {
  return (
    <div className="flex items-start gap-2.5">
      {/* Robot icon */}
      <div className="text-2xl flex-shrink-0 mt-0.5 select-none" aria-hidden>
        <img src="/bot-icon.svg" alt="" className="w-8 h-8" onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; (e.target as HTMLImageElement).insertAdjacentText("afterend", "\u{1F916}"); }} />
      </div>

      <div className="bg-gradient-to-br from-[#e6f5ef] to-[#dff0ec] border border-emerald-100 rounded-2xl px-5 py-4 max-w-xl shadow-sm">
        {/* Answer text */}
        <p className="text-[13px] text-gray-700 whitespace-pre-line leading-relaxed">
          {msg.text}
        </p>

        {/* Source link cards */}
        {msg.sources && msg.sources.length > 0 && (
          <div className="flex flex-wrap gap-2.5 mt-4">
            {msg.sources.map((src, j) => (
              <a
                key={j}
                href={src.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 bg-white border border-gray-200 rounded-xl px-4 py-2.5 text-xs text-gray-500 hover:border-teal-400 hover:text-teal-700 transition-colors shadow-sm"
              >
                <span className="truncate max-w-[170px]">{src.label}</span>
                <IconExternal />
              </a>
            ))}
          </div>
        )}

        {/* Last updated line */}
        {msg.lastUpdated && (
          <p className="text-[11px] text-gray-400 mt-2.5">
            Last updated from sources: {msg.lastUpdated}
          </p>
        )}

        {/* Action bar */}
        <div className="flex items-center justify-between mt-3 pt-2.5 border-t border-emerald-100/60">
          <button
            onClick={onCopy}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            title="Copy to clipboard"
          >
            {copied ? (
              <span className="text-[11px] text-teal-600 font-medium">Copied!</span>
            ) : (
              <IconCopy />
            )}
          </button>
          <div className="flex gap-3">
            <button
              className="text-gray-400 hover:text-teal-600 transition-colors"
              title="Helpful"
            >
              <IconThumbUp />
            </button>
            <button
              className="text-gray-400 hover:text-red-400 transition-colors"
              title="Not helpful"
            >
              <IconThumbDown />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function WelcomeMessage() {
  return (
    <div className="flex items-start gap-2.5 pt-2">
      <div className="flex-shrink-0 mt-0.5 select-none" aria-hidden>
        <img
          src="/welcome-icon.svg"
          alt=""
          className="w-8 h-8"
          onError={(e) => {
            (e.target as HTMLImageElement).style.display = "none";
          }}
        />
      </div>
      <div className="bg-gradient-to-br from-[#e6f5ef] to-[#dff0ec] border border-emerald-100 rounded-2xl px-5 py-4 max-w-xl shadow-sm">
        <p className="text-[13px] text-gray-700 leading-relaxed">
          <span className="font-semibold text-gray-800">
            Welcome! I&apos;m your Mutual Fund Assistant.
          </span>{" "}
          Ask me about Nippon India mutual fund schemes, including fund
          details, AUM, Current NAV, holdings, sector allocation, expense
          ratio, exit load, benchmark, and historical returns. I answer using
          source-based information and do not provide investment advice or
          future predictions.
        </p>
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-start gap-2.5">
      <div className="text-2xl flex-shrink-0 select-none" aria-hidden>
        &#x1F916;
      </div>
      <div className="bg-gradient-to-br from-[#e6f5ef] to-[#dff0ec] border border-emerald-100 rounded-2xl px-5 py-4 shadow-sm">
        <div className="flex gap-1.5">
          <span className="w-2 h-2 bg-teal-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
          <span className="w-2 h-2 bg-teal-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
          <span className="w-2 h-2 bg-teal-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
        </div>
      </div>
    </div>
  );
}
