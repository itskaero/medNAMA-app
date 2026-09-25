"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, BookOpen, CheckCircle2, HelpCircle, Loader2, Scale, Search, XCircle } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

type Verdict = "supported" | "contradicted" | "books_conflict" | "textbooks_silent";

interface Evidence {
  chunk_id: number;
  book_title: string | null;
  page_number: number | null;
  quote: string;
}

interface RefereeResult {
  verdict: Verdict | null;
  textbook_answer: string | null;
  supported_option?: string | null;
  published?: string | null;
  agrees_with_key?: boolean | null;
  evidence: Evidence[];
  explanation: string | null;
  ai_reasoning?: string;
  error?: string;
}

interface RecallRow extends RefereeResult {
  id: number;
  page: number | null;
  chapter: string | null;
  kind: string;
  headline_no: number | null;
  question: string;
  answer: string;
  review_status: string;
  reviewer_note: string | null;
}

const VERDICT_STYLE: Record<Verdict, { label: string; color: string; icon: React.ReactNode }> = {
  supported: { label: "Textbook-supported", color: "var(--sea-green)", icon: <CheckCircle2 size={14} /> },
  contradicted: { label: "Textbooks disagree with the key", color: "var(--error)", icon: <XCircle size={14} /> },
  books_conflict: { label: "Books conflict", color: "#f59e0b", icon: <AlertTriangle size={14} /> },
  textbooks_silent: { label: "Textbooks silent", color: "var(--text-muted)", icon: <HelpCircle size={14} /> },
};

function VerdictBadge({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) return <span style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>Not refereed yet</span>;
  const v = VERDICT_STYLE[verdict];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "0.74rem", fontWeight: 700, color: v.color }}>
      {v.icon} {v.label}
    </span>
  );
}

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      {evidence.map((e, i) => (
        <blockquote key={i} style={{ margin: 0, padding: "6px 10px", borderRadius: "8px", background: "var(--surface-3)", fontSize: "0.8rem", lineHeight: 1.5 }}>
          “{e.quote}”
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "3px", display: "flex", gap: "4px", alignItems: "center" }}>
            <BookOpen size={11} /> {e.book_title}{e.page_number ? `, p.${e.page_number}` : ""}
          </div>
        </blockquote>
      ))}
    </div>
  );
}

function VerdictCard({ r }: { r: RefereeResult }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px", border: "1px solid var(--border-light)", borderRadius: "12px", padding: "12px 14px" }}>
      <VerdictBadge verdict={r.verdict} />
      {r.textbook_answer ? (
        <div style={{ fontSize: "0.86rem" }}>
          <b>The textbooks say:</b> {r.textbook_answer}
          {r.supported_option ? <> (option <b>{r.supported_option}</b>)</> : null}
          {r.agrees_with_key === false ? <span style={{ color: "var(--error)", fontWeight: 700 }}> · published key disagrees</span> : null}
          {r.agrees_with_key === true ? <span style={{ color: "var(--sea-green)", fontWeight: 700 }}> · matches the published key</span> : null}
        </div>
      ) : null}
      <EvidenceList evidence={r.evidence || []} />
      {r.explanation ? <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>{r.explanation}</div> : null}
      {r.ai_reasoning ? (
        <div style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
          <b>AI reasoning (not from the textbooks):</b> {r.ai_reasoning}
        </div>
      ) : null}
    </div>
  );
}

export default function RefereeView({ token }: { token: string | null }) {
  const [tab, setTab] = useState<"check" | "bank">("check");
  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  // ── Check an answer ───────────────────────────────────────────────────
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [optionsText, setOptionsText] = useState("");
  const [key, setKey] = useState("");
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<RefereeResult | null>(null);

  const parseOptions = (txt: string): Record<string, string> | null => {
    const out: Record<string, string> = {};
    for (const line of txt.split("\n")) {
      const m = line.trim().match(/^\(?([A-Ea-e])[).:\-]\s*(.+)$/);
      if (m) out[m[1].toUpperCase()] = m[2].trim();
    }
    return Object.keys(out).length >= 2 ? out : null;
  };

  const check = async () => {
    const options = parseOptions(optionsText);
    if (!question.trim() || (!answer.trim() && !options)) {
      toast.error("Enter the question and either the published answer or the options (A-E, one per line).");
      return;
    }
    setChecking(true);
    setCheckResult(null);
    try {
      const res = await fetch(`${API}/api/referee`, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ question, answer: answer || null, options, key: key || null }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
      setCheckResult(body);
    } catch (e) {
      toast.error(`Referee failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setChecking(false);
    }
  };

  // ── Recall bank ───────────────────────────────────────────────────────
  const [filter, setFilter] = useState("disputed");
  const [chapter, setChapter] = useState("");
  const [search, setSearch] = useState("");
  const [bank, setBank] = useState<{ total: number; items: RecallRow[]; counts: Record<string, number>; chapters: string[] } | null>(null);
  const [offset, setOffset] = useState(0);
  const [bankKey, setBankKey] = useState(0);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    if (tab !== "bank") return;
    let cancelled = false;
    const params = new URLSearchParams({ offset: String(offset), limit: "25" });
    if (filter !== "all") params.set("verdict", filter);
    if (chapter) params.set("chapter", chapter);
    if (search.trim()) params.set("q", search.trim());
    fetch(`${API}/api/recalls?${params}`, { headers: headers(), credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (!cancelled && body) setBank(body);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [tab, filter, chapter, offset, bankKey, headers, search]);

  const reload = () => setBankKey((k) => k + 1);

  const runReferee = async (id: number) => {
    setBusyId(id);
    try {
      const res = await fetch(`${API}/api/recalls/${id}/referee`, { method: "POST", headers: headers(), credentials: "include" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      reload();
    } catch (e) {
      toast.error(`Referee failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  const review = async (id: number, review_status: string) => {
    const res = await fetch(`${API}/api/recalls/${id}`, {
      method: "PATCH",
      headers: { ...headers(), "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ review_status }),
    });
    if (res.ok) reload();
    else toast.error("Could not save the review.");
  };

  const inputStyle: React.CSSProperties = {
    width: "100%", padding: "8px 10px", borderRadius: "8px", border: "1px solid var(--border-light)",
    background: "var(--surface-3)", color: "var(--text-primary)", fontSize: "0.86rem",
  };

  return (
    <div className="dashboard-view" role="region" aria-label="Answer-key referee" style={{ maxWidth: "900px", margin: "0 auto" }}>
      <div className="dashboard-header">
        <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Scale size={20} /> Answer-Key Referee
        </h1>
        <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--text-secondary)" }}>
          Checks a recall answer or MCQ key against the textbooks and shows the exact passages. Private recall sources are
          never used as evidence. Admin-only while the recall bank is private.
        </p>
      </div>

      <div style={{ display: "flex", gap: "6px", marginBottom: "var(--sp-4)" }}>
        {(["check", "bank"] as const).map((t) => (
          <button key={t} className="btn-workspace" onClick={() => setTab(t)}
            style={{ borderColor: tab === t ? "var(--sky)" : undefined, color: tab === t ? "var(--sky)" : undefined }}>
            {t === "check" ? "Check an answer" : "Recall bank"}
          </button>
        ))}
      </div>

      {tab === "check" ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          <textarea rows={3} placeholder="Question / stem, e.g. 'Pericardial cavity lies between'" value={question} onChange={(e) => setQuestion(e.target.value)} style={inputStyle} />
          <input placeholder="Published answer (recall style), e.g. 'Visceral and parietal layer of fibrous pericardium'" value={answer} onChange={(e) => setAnswer(e.target.value)} style={inputStyle} />
          <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>…or paste MCQ options, one per line (A. … to E. …), with the published key:</div>
          <textarea rows={5} placeholder={"A. ...\nB. ...\nC. ...\nD. ...\nE. ..."} value={optionsText} onChange={(e) => setOptionsText(e.target.value)} style={inputStyle} />
          <input placeholder="Published key (e.g. C) — optional" value={key} onChange={(e) => setKey(e.target.value.slice(0, 1))} style={{ ...inputStyle, width: "220px" }} />
          <button className="btn-workspace" onClick={check} disabled={checking} style={{ alignSelf: "flex-start", display: "flex", gap: "6px", alignItems: "center" }}>
            {checking ? <Loader2 size={13} className="animate-spin" /> : <Scale size={13} />} {checking ? "Checking the textbooks…" : "Referee this answer"}
          </button>
          {checkResult ? <VerdictCard r={checkResult} /> : null}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
            {["disputed", "supported", "textbooks_silent", "unrefereed", "all"].map((f) => (
              <button key={f} className="btn-workspace" onClick={() => { setFilter(f); setOffset(0); }}
                style={{ fontSize: "0.72rem", padding: "3px 10px", borderColor: filter === f ? "var(--sky)" : undefined, color: filter === f ? "var(--sky)" : undefined }}>
                {f === "disputed" ? "Disputed keys" : f === "textbooks_silent" ? "Silent" : f[0].toUpperCase() + f.slice(1)}
                {bank && f !== "all" ? ` (${f === "disputed" ? (bank.counts.contradicted || 0) + (bank.counts.books_conflict || 0) : bank.counts[f] || 0})` : ""}
              </button>
            ))}
            <select value={chapter} onChange={(e) => { setChapter(e.target.value); setOffset(0); }} style={{ ...inputStyle, width: "180px", padding: "4px 8px", fontSize: "0.76rem" }}>
              <option value="">All chapters</option>
              {(bank?.chapters || []).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <span style={{ display: "flex", alignItems: "center", gap: "4px", flex: "1 1 200px" }}>
              <Search size={13} />
              <input placeholder="Search question or answer" value={search} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} style={{ ...inputStyle, padding: "4px 8px", fontSize: "0.76rem" }} />
            </span>
          </div>

          {!bank ? (
            <div style={{ color: "var(--text-muted)", fontSize: "0.82rem" }}><Loader2 size={13} className="animate-spin" /> Loading…</div>
          ) : bank.items.length === 0 ? (
            <div style={{ color: "var(--text-muted)", fontSize: "0.82rem" }}>No recalls match.</div>
          ) : (
            bank.items.map((r) => (
              <div key={r.id} style={{ border: "1px solid var(--border)", borderRadius: "12px", padding: "10px 12px", display: "flex", flexDirection: "column", gap: "6px", background: "var(--surface-2, transparent)" }}>
                <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                  p.{r.page} · {r.chapter || "—"} · {r.kind}{r.headline_no ? ` #${r.headline_no}` : ""} · review: {r.review_status}
                </div>
                <div style={{ fontSize: "0.9rem" }}>{r.question} <b>= {r.answer}</b></div>
                <VerdictBadge verdict={r.verdict} />
                {r.verdict ? (
                  <>
                    {r.textbook_answer ? <div style={{ fontSize: "0.82rem" }}><b>Textbooks:</b> {r.textbook_answer}</div> : null}
                    <EvidenceList evidence={r.evidence || []} />
                    {r.explanation ? <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>{r.explanation}</div> : null}
                  </>
                ) : null}
                <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                  <button className="btn-workspace" disabled={busyId === r.id} onClick={() => runReferee(r.id)} style={{ fontSize: "0.72rem", padding: "3px 10px" }}>
                    {busyId === r.id ? "Checking…" : r.verdict ? "Re-run referee" : "Run referee"}
                  </button>
                  {r.verdict ? (
                    <>
                      <button className="btn-workspace" onClick={() => review(r.id, "confirmed")} style={{ fontSize: "0.72rem", padding: "3px 10px" }}>Verdict correct</button>
                      <button className="btn-workspace" onClick={() => review(r.id, "corrected")} style={{ fontSize: "0.72rem", padding: "3px 10px" }}>Verdict wrong</button>
                      <button className="btn-workspace" onClick={() => review(r.id, "rejected")} style={{ fontSize: "0.72rem", padding: "3px 10px" }}>Bad extraction</button>
                    </>
                  ) : null}
                </div>
              </div>
            ))
          )}

          {bank && bank.total > 25 ? (
            <div style={{ display: "flex", gap: "8px", alignItems: "center", fontSize: "0.78rem" }}>
              <button className="btn-workspace" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - 25))}>Previous</button>
              <span>{offset + 1}–{Math.min(offset + 25, bank.total)} of {bank.total}</span>
              <button className="btn-workspace" disabled={offset + 25 >= bank.total} onClick={() => setOffset((o) => o + 25)}>Next</button>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
