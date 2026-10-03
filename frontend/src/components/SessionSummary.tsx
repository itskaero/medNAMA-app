"use client";

import React from "react";

export interface NextAction {
  label: string;
  onClick: () => void;
  primary?: boolean;
  icon?: React.ReactNode;
  hint?: string;
}

export interface SummaryCell {
  key: string | number;
  state: "right" | "wrong" | "skipped";
  active?: boolean;
  onClick?: () => void;
}

/**
 * The end of every session (practice, Daily Dose, sprint, mock, timed paper, challenge, look-alikes, offline pack):
 * the score, a grid of the questions, and what to do next, so no session ends in a dead end.
 * Anything session-specific (rank, leaderboard, the reviewed question) goes in `children`.
 */
export default function SessionSummary({ kicker, title, right, total, answered, stats, cells, actions, note, children }: {
  kicker?: React.ReactNode;
  title: string;
  right?: number;
  total?: number;
  answered?: number;
  stats?: { label: string; value: React.ReactNode; sub?: React.ReactNode }[];
  cells?: SummaryCell[];
  actions: NextAction[];
  note?: React.ReactNode;
  children?: React.ReactNode;
}) {
  const pct = total ? Math.round(((right ?? 0) / total) * 100) : null;
  return (
    <section className="summary" aria-label={title}>
      <header className="summary-head">
        {pct !== null ? (
          <div className="summary-score" style={{ borderColor: pct >= 75 ? "var(--sea-green)" : pct >= 50 ? "var(--teal)" : "var(--error)" }}>
            <b>{pct}%</b><span>{right} / {total}</span>
          </div>
        ) : null}
        <div className="summary-title">
          {kicker ? <span className="tile-kicker">{kicker}</span> : null}
          <h2>{title}</h2>
          {answered !== undefined && total !== undefined && answered < total ? (
            <span className="tile-text">{answered} of {total} answered; unanswered questions count as wrong.</span>
          ) : null}
          {note ? <span className="tile-text">{note}</span> : null}
        </div>
      </header>
      {stats?.length ? (
        <div className="summary-stats">
          {stats.map((s) => (
            <div key={s.label} className="summary-stat">
              <span className="lbl">{s.label}</span>
              <b>{s.value}</b>
              {s.sub ? <span className="tile-text">{s.sub}</span> : null}
            </div>
          ))}
        </div>
      ) : null}
      {actions.length ? (
        <div className="summary-actions">
          <span className="lbl">Next</span>
          {actions.map((a) => (
            <button key={a.label} type="button" className={a.primary ? "btn-primary" : "btn-workspace"} onClick={a.onClick} title={a.hint}>
              {a.icon}{a.icon ? " " : ""}{a.label}
            </button>
          ))}
        </div>
      ) : null}
      {cells?.length ? (
        <div className="review-grid" role="list">
          {cells.map((c, i) => (
            <button key={c.key} type="button" role="listitem" onClick={c.onClick}
              className={`review-circle-btn ${c.state === "right" ? "correct" : c.state === "wrong" ? "incorrect" : "skipped"} ${c.active ? "active" : ""}`}>
              {i + 1}
            </button>
          ))}
        </div>
      ) : null}
      {children}
    </section>
  );
}
