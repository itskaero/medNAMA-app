"use client";

import React from "react";
import { GitCompareArrows } from "lucide-react";
import { CiteLink } from "@/components/CiteLink";

export interface PairData {
  id: number;
  term_a: string;
  term_b: string;
  status: "pending" | "ready" | "failed";
  grounding: "textbook" | "ai";
  card: {
    rows: { feature: string; a: string; b: string }[];
    discriminator: string;
    quotes: { side: "a" | "b"; quote: string; book_title: string | null; page_number: number | null }[];
  } | null;
}

/** Side-by-side comparison of two look-alike concepts, with verified textbook quotes. */
export function PairCard({ pair }: { pair: PairData }) {
  if (!pair.card) return null;
  const { rows, discriminator, quotes } = pair.card;
  const cell: React.CSSProperties = { padding: "7px 9px", borderTop: "1px solid var(--border-light)", verticalAlign: "top", lineHeight: 1.45 };
  return (
    <div style={{ border: "1px solid var(--border-light)", borderRadius: "12px", padding: "12px", background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "6px", fontWeight: 700, fontSize: "0.88rem" }}>
        <GitCompareArrows size={15} style={{ color: "#d97706" }} /> {pair.term_a} <span style={{ color: "var(--text-muted)", fontWeight: 500 }}>vs</span> {pair.term_b}
      </div>
      {discriminator ? (
        <div style={{ fontSize: "0.84rem", padding: "8px 10px", borderRadius: "10px", background: "rgba(245,158,11,0.10)", color: "var(--text-primary)" }}>
          <strong>Tell them apart:</strong> {discriminator}
        </div>
      ) : null}
      <style>{`
        .pair-stack { display: none; }
        @media (max-width: 640px) { .pair-table { display: none; } .pair-stack { display: flex; } }
      `}</style>
      <div className="pair-stack" style={{ flexDirection: "column", gap: "8px" }}>
        {rows.map((r, i) => (
          <div key={i} style={{ borderTop: "1px solid var(--border-light)", paddingTop: "6px", fontSize: "0.8rem", lineHeight: 1.45 }}>
            <div style={{ color: "var(--text-muted)", fontWeight: 600, marginBottom: "3px" }}>{r.feature}</div>
            <div><b>{pair.term_a}:</b> {r.a}</div>
            <div><b>{pair.term_b}:</b> {r.b}</div>
          </div>
        ))}
      </div>
      <div className="pair-table" style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem" }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--text-muted)" }}>
              <th style={{ padding: "4px 9px", fontWeight: 600 }}></th>
              <th style={{ padding: "4px 9px", fontWeight: 700, color: "var(--text-primary)" }}>{pair.term_a}</th>
              <th style={{ padding: "4px 9px", fontWeight: 700, color: "var(--text-primary)" }}>{pair.term_b}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td style={{ ...cell, color: "var(--text-muted)", fontWeight: 600, minWidth: "110px" }}>{r.feature}</td>
                <td style={cell}>{r.a}</td>
                <td style={cell}>{r.b}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {quotes.length ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {quotes.map((q, i) => (
            <blockquote key={i} style={{ margin: 0, padding: "6px 10px", borderLeft: "3px solid var(--sky)", fontSize: "0.78rem", color: "var(--text-secondary)" }}>
              &ldquo;{q.quote}&rdquo;{" "}
              <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: "0.7rem" }}>
                {q.book_title}{q.page_number ? <>, <CiteLink bookTitle={q.book_title} page={q.page_number} excerpt={q.quote}>p.{q.page_number}</CiteLink></> : ""}
              </span>
            </blockquote>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: "0.74rem", color: "var(--text-muted)" }}>AI clinical knowledge: your textbooks were not quoted for this pair.</div>
      )}
    </div>
  );
}
