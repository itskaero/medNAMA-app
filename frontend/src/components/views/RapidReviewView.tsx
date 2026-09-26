"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ArrowLeft, BookOpen, Eye, EyeOff, FileText, ListChecks, Loader2, RefreshCw, Zap } from "lucide-react";
import { API } from "@/lib/constants";
import { parseMarkdown } from "@/utils/markdown";
import { PaperYears } from "@/components/QuestionMedia";

/** What to review: a past-paper selection, or a bank category. */
export interface ReviewScope {
  label: string;
  exam?: string | null;
  years?: number[] | null;
  tags?: Record<string, string[]>;
  main?: string | null;
  sub?: string | null;
  returnTo?: string;
}

interface KeyItem {
  id: number;
  question_text: string;
  options: Record<string, string>;
  correct_option: string;
  answer: string;
  topic: string | null;
  years: number[];
  explanation_markdown: string | null;
}

interface Summary { label: string; markdown: string; citations: { book_title: string; page_number: number }[]; key_count: number; cached: boolean; created_at: string | null }

const PAGE = 100;

/** Fast study of a topic: the answer key at a glance, a one-page summary, and a 10-question drill. */
export default function RapidReviewView({
  token,
  scope,
  isAdmin,
  onDrill,
  onBack,
}: {
  token: string | null;
  scope: ReviewScope;
  isAdmin: boolean;
  onDrill: (filters: Record<string, unknown>, label: string) => void;
  onBack: () => void;
}) {
  const hasTopic = Boolean((scope.exam && Object.values(scope.tags || {}).some((v) => v.length)) || scope.sub);
  const [tab, setTab] = useState<"keys" | "summary">(hasTopic ? "summary" : "keys");
  const [items, setItems] = useState<KeyItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [order, setOrder] = useState<"most_asked" | "topic">("most_asked");
  const [onlyMissed, setOnlyMissed] = useState(false);
  const [hideAnswers, setHideAnswers] = useState(false);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [summary, setSummary] = useState<Summary | null>(null);
  const [summaryState, setSummaryState] = useState<"idle" | "loading" | "error">("idle");
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
  }, [token]);

  const scopeBody = { exam: scope.exam, years: scope.years, tags: scope.tags, main: scope.main, sub: scope.sub };
  const scopeKey = JSON.stringify(scopeBody);

  useEffect(() => {
    if (tab !== "keys") return;
    let cancelled = false;
    fetch(`${API}/api/study/keys`, {
      method: "POST", headers: headers(), credentials: "include",
      body: JSON.stringify({ ...scopeBody, order, only_missed: onlyMissed, offset: page * PAGE, limit: PAGE }),
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => {
        if (cancelled) return;
        setItems(body.items);
        setTotal(body.total);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
    // scopeKey captures the scope fields
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, scopeKey, order, onlyMissed, page, headers]);

  const loadSummary = useCallback(async (regenerate = false) => {
    setSummaryState("loading");
    setSummaryError(null);
    try {
      const r = await fetch(`${API}/api/study/topic-summary`, {
        method: "POST", headers: headers(), credentials: "include",
        body: JSON.stringify({ ...JSON.parse(scopeKey), regenerate }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      setSummary(body);
      setSummaryState("idle");
    } catch (e) {
      setSummaryError(e instanceof Error ? e.message : String(e));
      setSummaryState("error");
    }
  }, [headers, scopeKey]);

  useEffect(() => {
    if (tab === "summary" && hasTopic && !summary && summaryState === "idle") {
      const id = setTimeout(() => loadSummary(false), 0);
      return () => clearTimeout(id);
    }
  }, [tab, hasTopic, summary, summaryState, loadSummary]);

  const toggle = (set: Set<number>, id: number, setter: (s: Set<number>) => void) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  };

  const drill = () =>
    onDrill(
      { past_paper_exam: scope.exam || undefined, years: scope.years || undefined, tags: scope.tags || undefined,
        categories: scope.main ? [scope.main] : undefined, sub_categories: scope.sub ? [scope.sub] : undefined,
        num_questions: 10, prefer_unseen: true },
      `Rapid drill · ${scope.label}`
    );

  const tabBtn = (id: "keys" | "summary", label: string, icon: React.ReactNode, disabled = false) => (
    <button key={id} type="button" role="tab" aria-selected={tab === id} disabled={disabled} onClick={() => setTab(id)} className="btn-workspace"
      title={disabled ? "Pick a subject or topic to get a summary" : undefined}
      style={{ fontWeight: 700, borderColor: tab === id ? "var(--sky)" : undefined, background: tab === id ? "rgba(48,197,255,0.12)" : undefined }}>
      {icon} {label}
    </button>
  );

  return (
    <div className="dashboard-view" role="region" aria-label="Rapid review" style={{ maxWidth: "900px", margin: "0 auto" }}>
      <button className="btn-workspace" onClick={onBack} style={{ marginBottom: "var(--sp-3)" }}><ArrowLeft size={12} /> Back</button>
      <div className="dashboard-header">
        <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Zap size={20} style={{ color: "#f59e0b" }} /> Rapid review
        </h1>
        <p style={{ margin: 0, fontSize: "0.85rem", color: "var(--text-secondary)" }}>{scope.label}</p>
      </div>

      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "var(--sp-4)" }} role="tablist">
        {tabBtn("summary", "One-page summary", <FileText size={13} />, !hasTopic)}
        {tabBtn("keys", "Answer keys", <ListChecks size={13} />)}
        <button type="button" className="btn-workspace" onClick={drill} style={{ marginLeft: "auto" }}>
          <Zap size={13} /> 10-question drill
        </button>
      </div>

      {tab === "summary" ? (
        summaryState === "loading" ? (
          <div style={{ color: "var(--text-muted)", display: "flex", gap: "8px", alignItems: "center", padding: "var(--sp-5) 0" }}>
            <Loader2 size={16} className="animate-spin" /> Writing the summary from the most-asked questions and your textbooks (about 30 seconds, then it is saved)…
          </div>
        ) : summaryState === "error" ? (
          <div>
            <p style={{ color: "var(--error)" }}>Could not get the summary: {summaryError}</p>
            <button className="btn-workspace" onClick={() => loadSummary(false)}>Try again</button>
          </div>
        ) : summary ? (
          <article style={{ border: "1px solid var(--border-light)", borderRadius: "14px", padding: "16px 18px", background: "var(--surface-2)" }}>
            <div className="prose" style={{ fontSize: "0.9rem" }} dangerouslySetInnerHTML={{ __html: parseMarkdown(summary.markdown) }} />
            <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap", marginTop: "12px", fontSize: "0.72rem", color: "var(--text-muted)" }}>
              <span><BookOpen size={11} /> {summary.citations.length} textbook reference{summary.citations.length === 1 ? "" : "s"} checked against the passages</span>
              <span>· built from {summary.key_count} most-asked questions</span>
              {isAdmin ? (
                <button className="btn-workspace" style={{ padding: "2px 8px", fontSize: "0.7rem" }} onClick={() => loadSummary(true)}>
                  <RefreshCw size={11} /> Regenerate
                </button>
              ) : null}
            </div>
          </article>
        ) : null
      ) : (
        <>
          <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap", marginBottom: "10px", fontSize: "0.8rem" }}>
            <strong>{total.toLocaleString()} questions</strong>
            <select value={order} onChange={(e) => { setOrder(e.target.value as "most_asked" | "topic"); setPage(0); }} aria-label="Order"
              style={{ background: "var(--surface-3)", border: "1px solid var(--border-light)", borderRadius: "8px", color: "var(--text-primary)", padding: "3px 6px" }}>
              <option value="most_asked">Most asked first</option>
              <option value="topic">By topic</option>
            </select>
            <label style={{ display: "inline-flex", gap: "4px", alignItems: "center" }}>
              <input type="checkbox" checked={onlyMissed} onChange={(e) => { setOnlyMissed(e.target.checked); setPage(0); }} /> my missed only
            </label>
            <button className="btn-workspace" style={{ padding: "3px 10px", fontSize: "0.74rem" }}
              onClick={() => { setHideAnswers((h) => !h); setRevealed(new Set()); }}
              title="Test yourself: answers stay hidden until you tap them">
              {hideAnswers ? <Eye size={12} /> : <EyeOff size={12} />} {hideAnswers ? "Show answers" : "Hide answers"}
            </button>
          </div>
          {!items ? (
            <div style={{ color: "var(--text-muted)" }}><Loader2 size={14} className="animate-spin" /> Loading…</div>
          ) : items.length === 0 ? (
            <p style={{ color: "var(--text-secondary)" }}>{onlyMissed ? "No missed questions here yet." : "No questions in this selection."}</p>
          ) : (
            <ol start={page * PAGE + 1} style={{ margin: 0, paddingLeft: "22px", display: "flex", flexDirection: "column", gap: "6px" }}>
              {items.map((k) => {
                const shown = !hideAnswers || revealed.has(k.id);
                const expanded = open.has(k.id);
                return (
                  <li key={k.id} style={{ fontSize: "0.86rem", lineHeight: 1.5 }}>
                    <span style={{ cursor: "pointer" }} onClick={() => toggle(open, k.id, setOpen)} title="Show options and explanation">
                      {k.question_text.replace(/\s+/g, " ")}
                    </span>{" "}
                    <span aria-hidden>→</span>{" "}
                    {shown ? (
                      <strong style={{ color: "var(--sea-green)" }}>{k.answer}</strong>
                    ) : (
                      <button type="button" className="btn-workspace" style={{ padding: "0 8px", fontSize: "0.72rem" }}
                        onClick={() => toggle(revealed, k.id, setRevealed)}>reveal</button>
                    )}{" "}
                    {k.years.length > 1 ? <PaperYears years={k.years} /> : null}
                    {expanded ? (
                      <div style={{ margin: "6px 0 4px", padding: "8px 10px", borderRadius: "10px", background: "var(--surface-2)", border: "1px solid var(--border-light)" }}>
                        {Object.keys(k.options).sort().map((o) => (
                          <div key={o} style={{ fontSize: "0.8rem", color: o === k.correct_option ? "var(--sea-green)" : "var(--text-secondary)", fontWeight: o === k.correct_option ? 700 : 400 }}>
                            {o}. {k.options[o]}
                          </div>
                        ))}
                        {k.explanation_markdown ? (
                          <div className="prose" style={{ fontSize: "0.8rem", marginTop: "6px" }} dangerouslySetInnerHTML={{ __html: parseMarkdown(k.explanation_markdown) }} />
                        ) : null}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          )}
          {total > PAGE ? (
            <div style={{ display: "flex", gap: "8px", alignItems: "center", justifyContent: "center", marginTop: "var(--sp-4)", fontSize: "0.8rem" }}>
              <button className="btn-workspace" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>
              <span>Page {page + 1} of {Math.ceil(total / PAGE)}</span>
              <button className="btn-workspace" disabled={(page + 1) * PAGE >= total} onClick={() => setPage((p) => p + 1)}>Next</button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
