import React, { useState, useEffect } from "react";
import { Stethoscope, AlertCircle, Check, Copy, Bookmark, Lightbulb, ChevronDown, ChevronRight, Layers } from "lucide-react";
import { Message, Figure, Grounding } from "../types";
import { CitationsDrawer } from "./CitationsDrawer";
import { FiguresDrawer } from "./FiguresDrawer";
import { SourcesPanel } from "./SourcesPanel";
import { ReportButton } from "./ReportButton";
import { parseMarkdown } from "../utils/markdown";
import { CHAT_STAGE_TEXT } from "../hooks/useChat";
import { API } from "@/lib/constants";
import { toast } from "sonner";

const GROUNDING_LABELS: Record<Exclude<Grounding, "none">, { text: string; title: string; color: string }> = {
  textbook: {
    text: "Textbook-backed",
    title: "Every fact in this answer is cited from your ingested textbooks.",
    color: "var(--sea-green)",
  },
  partial: {
    text: "Partly textbook-backed",
    title: "Cited textbook facts, plus a labelled section of AI clinical knowledge the books don't state.",
    color: "var(--sky)",
  },
  ai_only: {
    text: "AI knowledge only",
    title: "The textbooks didn't cover this; the answer is from AI clinical knowledge and has no citations. Verify before relying on it.",
    color: "var(--warning, #d9a441)",
  },
};

/** Small label showing how much of the answer comes from the textbooks. */
function GroundingBadge({ grounding }: { grounding?: Grounding }) {
  if (!grounding || grounding === "none") return null;
  const g = GROUNDING_LABELS[grounding];
  return (
    <span
      title={g.title}
      style={{
        display: "inline-block",
        marginBottom: "var(--sp-2)",
        padding: "2px 8px",
        borderRadius: "999px",
        border: `1px solid ${g.color}`,
        color: g.color,
        fontSize: "0.68rem",
        fontFamily: "var(--font-mono)",
        letterSpacing: "0.02em",
      }}
    >
      {g.text}
    </span>
  );
}

/** Collapsible exam buzzwords / mnemonics, with one-click save to flashcards. */
function BuzzwordsSection({ markdown, token, topic }: { markdown: string; token: string | null; topic?: string }) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const lines = markdown
    .split("\n")
    .map((l) => l.replace(/^\s*[-*•]\s*/, "").trim())
    .filter(Boolean);

  const saveAsFlashcards = async () => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    const headers: HeadersInit = { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
    let ok = 0;
    for (const line of lines) {
      // "cue -> meaning" becomes front/back; otherwise the whole line is the back of a topic card.
      const [front, ...rest] = line.split(/\s*(?:->|→|=>)\s*/);
      const body = rest.length
        ? { front: front, back: rest.join(" → "), topic: topic || "Buzzwords" }
        : { front: `Buzzword: ${topic || "exam topic"}`, back: line, topic: topic || "Buzzwords" };
      try {
        const res = await fetch(`${API}/api/flashcards`, { method: "POST", headers, credentials: "include", body: JSON.stringify(body) });
        if (res.ok) ok++;
      } catch {
        /* counted below */
      }
    }
    if (ok) {
      setSaved(true);
      toast.success(`Saved ${ok} buzzword flashcard${ok === 1 ? "" : "s"} (Study Corner).`);
    } else {
      toast.error("Could not save flashcards.");
    }
  };

  if (!lines.length) return null;
  return (
    <div style={{ marginTop: "var(--sp-3)", border: "1px solid var(--border)", borderRadius: "10px", overflow: "hidden" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: "6px", padding: "8px 10px",
          background: "var(--surface-3)", border: "none", cursor: "pointer", color: "var(--text-primary)",
          fontSize: "0.78rem", fontWeight: 600, textAlign: "left",
        }}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Lightbulb size={12} style={{ color: "var(--sky)" }} />
        Exam buzzwords & mnemonics ({lines.length})
      </button>
      {open ? (
        <div style={{ padding: "8px 12px" }}>
          <div className="prose" dangerouslySetInnerHTML={{ __html: parseMarkdown(lines.map((l) => `- ${l}`).join("\n")) }} />
          <button
            type="button"
            className="btn-workspace"
            disabled={saved}
            onClick={saveAsFlashcards}
            style={{ marginTop: "6px", display: "flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.72rem" }}
          >
            <Layers size={10} />
            {saved ? "Saved to flashcards" : "Save as flashcards"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function AIMessage({
  msg,
  token,
  onFigureClick,
  onBookmarkConcept,
}: {
  msg: Message;
  token: string | null;
  onFigureClick: (f: Figure) => void;
  onBookmarkConcept?: (content: string) => void;
}) {
  const [thinkingStage, setThinkingStage] = useState(0);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (msg.type !== "thinking") return;
    const interval = setInterval(() => {
      setThinkingStage((prev) => (prev < 3 ? prev + 1 : prev));
    }, 2500);
    return () => clearInterval(interval);
  }, [msg.type]);

  const handleCopy = () => {
    if (!msg.answer) return;
    navigator.clipboard.writeText(msg.answer.answer_markdown);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const stagesText = [
    "Consulting medical reference library...",
    "Cross-referencing textbook chapters...",
    "Reranking candidate passages with Cross-Encoder...",
    "Synthesizing RAG-grounded clinical explanation..."
  ];

  return (
    <div className="ai-message" role="article" aria-label="AI response">
      <div className="ai-body">
        {/* Editorial Brand Header */}
        <div className="ai-editorial-header">
          <Stethoscope size={13} className="ai-editorial-icon" />
          <span className="ai-editorial-name">Dr. MedNama</span>
        </div>
        
        {msg.type === "thinking" ? (
          <div className="thinking" aria-live="polite" aria-label="Generating answer">
            <div className="thinking-dots" aria-hidden>
              <span className="thinking-dot" />
              <span className="thinking-dot" />
              <span className="thinking-dot" />
            </div>
            <span style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
              {(msg.stage && CHAT_STAGE_TEXT[msg.stage]) || stagesText[thinkingStage]}
            </span>
          </div>
        ) : null}
        {msg.type === "error" ? (
          <div className="error-banner" role="alert">
            <AlertCircle size={16} />
            <span>{msg.errorMsg}</span>
          </div>
        ) : null}
        {msg.type === "ai" && msg.answer ? (
          <>
            <GroundingBadge grounding={msg.answer.grounding} />
            <div
              className="prose"
              dangerouslySetInnerHTML={{ __html: parseMarkdown(msg.answer.answer_markdown) }}
            />
            
            <FiguresDrawer
              figures={msg.answer.figures}
              token={token}
              onFigureClick={onFigureClick}
            />

            {msg.answer.buzzwords_markdown ? (
              <BuzzwordsSection markdown={msg.answer.buzzwords_markdown} token={token} topic={msg.query} />
            ) : null}

            {msg.answer.also_in && msg.answer.also_in.length ? (
              <div
                style={{ marginTop: "var(--sp-3)", display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px", fontSize: "0.72rem", color: "var(--text-muted)" }}
                title="These books also have a strongly matching passage; see the sources list below"
              >
                <span>Also covered in:</span>
                {msg.answer.also_in.map((b) => (
                  <span key={`${b.book_title}-${b.page_number}`} className="model-chip-pill" style={{ fontSize: "0.7rem" }}>
                    {b.book_title}
                    {b.page_number ? `, p.${b.page_number}` : ""}
                  </span>
                ))}
              </div>
            ) : null}

            {/* F1 — hybrid sources panel (reranked candidates before merge) */}
            <SourcesPanel sources={msg.answer.sources || []} token={token} />

            <div className="answer-footer" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%", marginTop: "var(--sp-4)" }}>
              <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
                <CitationsDrawer citations={msg.answer.citations} token={token} />
              </div>
              
              <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)" }}>
                {msg.timestamp ? (
                  <span style={{ fontSize: "0.68rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {msg.timestamp}
                  </span>
                ) : null}
                <button 
                  className="btn-workspace" 
                  style={{ display: "flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.72rem" }}
                  onClick={handleCopy}
                  title="Copy answer to clipboard"
                >
                  {copied ? <Check size={10} style={{ color: "var(--sea-green)" }} /> : <Copy size={10} />}
                  <span>{copied ? "Copied!" : "Copy"}</span>
                </button>
                {onBookmarkConcept ? (
                  <button 
                    className="btn-workspace" 
                    style={{ display: "flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.72rem" }}
                    onClick={() => msg.answer && onBookmarkConcept(msg.answer.answer_markdown)}
                    title="Bookmark this concept to Bookmarks tab"
                  >
                    <Bookmark size={10} />
                    <span>Save</span>
                  </button>
                ) : null}
                <ReportButton kind="chat" token={token} question={msg.query} answerExcerpt={msg.answer.answer_markdown} />
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
