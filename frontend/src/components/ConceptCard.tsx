"use client";

import React, { useState } from "react";
import { BookOpen, Lightbulb, Quote, Sparkles } from "lucide-react";
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
export function ConceptCard({
  card,
  token,
  onFigureClick,
  heading = "Concept to remember",
}: {
  card: ConceptCardData;
  token: string | null;
  onFigureClick?: (f: Figure) => void;
  heading?: string;
}) {
  const [passage, setPassage] = useState<string | null>(null);
  const [loadingPassage, setLoadingPassage] = useState(false);

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
    </div>
  );
}
