"use client";

import React, { useState } from "react";
import { BookOpen, Lightbulb, Loader2, MessageSquareText, Quote, Sparkles } from "lucide-react";
import { API } from "@/lib/constants";
import { Figure } from "@/types";
import { FiguresDrawer } from "./FiguresDrawer";

export interface ConceptCardData {
  id: number;
  title: string;
  summary: string;
  quote: string | null;
  chunk_id: number | null;
  book_title: string | null;
  page_number: number | null;
  figure: (Figure & { caption?: string | null; book_title?: string | null }) | null;
  mnemonic: string | null;
  subject: string | null;
  source: string;
  grounding: "textbook" | "ai";
}

/**
 * A concept card: the idea behind a question in 2-3 lines, the exact textbook
 * sentence it comes from (book + page, expandable to the full passage), the
 * textbook figure when there is one, and a memory aid. Cards without a
 * verifiable quote are labelled as AI knowledge.
 */
interface ExplainResult {
  score: number;
  points_hit: string[];
  points_missed: string[];
  errors: string[];
  feedback: string;
}

export function ConceptCard({
  card,
  token,
  onFigureClick,
  heading = "Concept to remember",
  explainBack = true,
}: {
  card: ConceptCardData;
  token: string | null;
  onFigureClick?: (f: Figure) => void;
  heading?: string;
  /** Show the "Explain it back" self-test (Feynman check). */
  explainBack?: boolean;
}) {
  const [passage, setPassage] = useState<string | null>(null);
  const [loadingPassage, setLoadingPassage] = useState(false);
  const [explainOpen, setExplainOpen] = useState(false);
  const [explainText, setExplainText] = useState("");
  const [explainBusy, setExplainBusy] = useState(false);
  const [explainResult, setExplainResult] = useState<ExplainResult | null>(null);
  const [explainError, setExplainError] = useState<string | null>(null);

  const submitExplain = async () => {
    setExplainBusy(true);
    setExplainError(null);
    try {
      const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
      const res = await fetch(`${API}/api/concepts/${card.id}/explain-back`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
        credentials: "include",
        body: JSON.stringify({ explanation: explainText }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
      setExplainResult(body);
    } catch (e) {
      setExplainError(e instanceof Error ? e.message : String(e));
    } finally {
      setExplainBusy(false);
    }
  };

  const togglePassage = async () => {
    if (passage !== null) {
      setPassage(null);
      return;
    }
    if (!card.chunk_id) return;
    setLoadingPassage(true);
    try {
      const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
      const res = await fetch(`${API}/api/chat/source/${card.chunk_id}`, {
        headers: t ? { Authorization: `Bearer ${t}` } : {},
        credentials: "include",
      });
      if (res.ok) setPassage((await res.json()).content || "");
    } finally {
      setLoadingPassage(false);
    }
  };

  return (
    <div
      style={{
        border: "1px solid var(--border-light)",
        borderLeft: "3px solid var(--sky)",
        borderRadius: "12px",
        padding: "14px 16px",
        background: "var(--surface-2, var(--surface-3))",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
        <span style={{ fontSize: "0.66rem", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--sky)", display: "flex", alignItems: "center", gap: "5px" }}>
          <Lightbulb size={12} /> {heading}
        </span>
        <span style={{ fontSize: "0.66rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
          {card.grounding === "textbook" ? "Textbook-backed" : "AI knowledge (no textbook quote found)"}
          {card.subject ? ` · ${card.subject}` : ""}
        </span>
      </div>

      <div style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>{card.title}</div>
      <div style={{ fontSize: "0.86rem", lineHeight: 1.55, color: "var(--text-secondary)" }}>{card.summary}</div>

      {card.quote ? (
        <blockquote
          style={{
            margin: 0,
            padding: "8px 12px",
            borderRadius: "8px",
            background: "var(--surface-3)",
            fontSize: "0.82rem",
            lineHeight: 1.5,
            color: "var(--text-primary)",
          }}
        >
          <Quote size={11} style={{ color: "var(--text-muted)", marginRight: "4px" }} />
          {card.quote}
          <div style={{ marginTop: "6px", display: "flex", alignItems: "center", gap: "8px", fontSize: "0.7rem", color: "var(--text-muted)" }}>
            <BookOpen size={11} />
            {card.book_title}
            {card.page_number ? `, p.${card.page_number}` : ""}
            {card.chunk_id ? (
              <button
                type="button"
                onClick={togglePassage}
                style={{ background: "none", border: "none", padding: 0, color: "var(--sky)", cursor: "pointer", fontSize: "0.7rem" }}
              >
                {loadingPassage ? "Loading…" : passage !== null ? "Hide passage" : "Read the full passage"}
              </button>
            ) : null}
          </div>
          {passage !== null ? (
            <div style={{ marginTop: "8px", whiteSpace: "pre-wrap", fontSize: "0.78rem", color: "var(--text-secondary)" }}>{passage}</div>
          ) : null}
        </blockquote>
      ) : null}

      {card.figure && onFigureClick ? (
        <FiguresDrawer figures={[card.figure]} token={token} onFigureClick={onFigureClick} />
      ) : null}

      {card.mnemonic ? (
        <div style={{ fontSize: "0.8rem", color: "var(--text-primary)", display: "flex", gap: "6px", alignItems: "flex-start" }}>
          <Sparkles size={13} style={{ color: "var(--sky)", marginTop: "2px", flexShrink: 0 }} />
          <span>{card.mnemonic}</span>
        </div>
      ) : null}

      {explainBack ? (
        <div style={{ borderTop: "1px dashed var(--border-light)", paddingTop: "8px" }}>
          {!explainOpen ? (
            <button
              type="button"
              className="btn-workspace"
              onClick={() => setExplainOpen(true)}
              style={{ display: "flex", alignItems: "center", gap: "5px", padding: "4px 10px", fontSize: "0.74rem" }}
              title="Explaining a concept in your own words is one of the strongest ways to remember it"
            >
              <MessageSquareText size={12} /> Explain it back in your own words
            </button>
          ) : explainResult ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "0.8rem" }}>
              <div style={{ fontWeight: 700, color: explainResult.score >= 80 ? "var(--sea-green)" : explainResult.score >= 50 ? "var(--sky)" : "var(--error)" }}>
                {explainResult.score}/100 · {explainResult.score >= 80 ? "You've got it" : explainResult.score >= 50 ? "Nearly there" : "Worth another look"}
              </div>
              {explainResult.points_hit.length ? <div>✓ {explainResult.points_hit.join(" · ")}</div> : null}
              {explainResult.points_missed.length ? <div style={{ color: "var(--text-secondary)" }}>Missing: {explainResult.points_missed.join(" · ")}</div> : null}
              {explainResult.errors.length ? <div style={{ color: "var(--error)" }}>Not quite right: {explainResult.errors.join(" · ")}</div> : null}
              <div style={{ color: "var(--text-secondary)" }}>{explainResult.feedback}</div>
              <button type="button" className="btn-workspace" onClick={() => { setExplainResult(null); setExplainText(""); }} style={{ alignSelf: "flex-start", padding: "3px 10px", fontSize: "0.72rem" }}>
                Try again
              </button>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <textarea
                rows={3}
                value={explainText}
                onChange={(e) => setExplainText(e.target.value)}
                placeholder="Without looking back: explain this concept in 1-3 sentences, as if teaching a junior."
                style={{ width: "100%", padding: "6px 8px", borderRadius: "8px", border: "1px solid var(--border-light)", background: "var(--surface-3)", color: "var(--text-primary)", fontSize: "0.8rem" }}
              />
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <button type="button" className="btn-workspace" disabled={explainBusy || explainText.trim().length < 15} onClick={submitExplain} style={{ padding: "4px 10px", fontSize: "0.74rem", display: "flex", gap: "5px", alignItems: "center" }}>
                  {explainBusy ? <Loader2 size={12} className="animate-spin" /> : null} Check my explanation
                </button>
                {explainError ? <span style={{ color: "var(--error)", fontSize: "0.72rem" }}>{explainError}</span> : null}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
