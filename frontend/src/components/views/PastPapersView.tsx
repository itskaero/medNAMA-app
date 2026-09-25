"use client";

import React, { useCallback, useEffect, useState } from "react";
import { History, Loader2, Lock, PlayCircle, RotateCcw, Timer, X } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

type Axis = "subject" | "topic" | "specialty";
const AXES: { axis: Axis; label: string; hint: string }[] = [
  { axis: "subject", label: "Subject", hint: "The paper's subject sections" },
  { axis: "topic", label: "Topic", hint: "System / topic within the subjects" },
  { axis: "specialty", label: "Specialty group", hint: "Which specialty's paper the question was asked in" },
];

interface Paper { id: number; year: number | null; title: string; total: number; answered: number; correct: number }
interface Exam { exam: string; papers: Paper[]; total: number; answered: number; correct: number }
interface Scope { count: number; answered: number; missed: number; facets: Record<Axis, { label: string; count: number }[]> }

const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);

/** Past papers by exam and year, filtered by subject / topic / specialty; practice or sit a timed paper. */
export default function PastPapersView({
  token,
  onPractice,
  onTimedPaper,
}: {
  token: string | null;
  onPractice: (filters: Record<string, unknown>, label: string) => void;
  onTimedPaper: (mockId: number) => void;
}) {
  const [exams, setExams] = useState<Exam[] | null>(null);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exam, setExam] = useState<string | null>(null);
  const [years, setYears] = useState<number[]>([]);
  const [tags, setTags] = useState<Record<Axis, string[]>>({ subject: [], topic: [], specialty: [] });
  const [scope, setScope] = useState<Scope | null>(null);
  const [scopeLoading, setScopeLoading] = useState(false);
  const [count, setCount] = useState(20);
  const [missedOnly, setMissedOnly] = useState(false);
  const [timed, setTimed] = useState({ count: 100, minutes: 120 });
  const [creating, setCreating] = useState(false);
  const [showAll, setShowAll] = useState<Record<Axis, boolean>>({ subject: false, topic: false, specialty: false });
  const [refreshKey, setRefreshKey] = useState(0);

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/past-papers`, { headers: headers(), credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((body) => {
        if (cancelled) return;
        setLocked(Boolean(body.locked));
        setExams(body.exams || []);
        setExam((cur) => cur ?? body.exams?.[0]?.exam ?? null);
      })
      .catch((e) => {
        if (!cancelled) setError(`Could not load past papers (${e instanceof Error ? e.message : String(e)}).`);
      });
    return () => {
      cancelled = true;
    };
  }, [headers, refreshKey]);

  const activeTags = Object.fromEntries(Object.entries(tags).filter(([, v]) => v.length)) as Record<string, string[]>;
  const filterKey = JSON.stringify({ exam, years, activeTags });

  useEffect(() => {
    if (!exam) return;
    let cancelled = false;
    const id = setTimeout(() => {
      setScopeLoading(true);
      fetch(`${API}/api/past-papers/scope`, {
        method: "POST", headers: headers(), credentials: "include",
        body: JSON.stringify({ exam, years: years.length ? years : null, tags: activeTags }),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((body) => {
          if (!cancelled && body) setScope(body);
        })
        .finally(() => {
          if (!cancelled) setScopeLoading(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(id);
    };
    // filterKey captures exam/years/tags
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, headers, refreshKey]);

  const toggleYear = (y: number) => setYears((ys) => (ys.includes(y) ? ys.filter((x) => x !== y) : [...ys, y].sort()));
  const toggleTag = (axis: Axis, label: string) =>
    setTags((t) => ({ ...t, [axis]: t[axis].includes(label) ? t[axis].filter((x) => x !== label) : [...t[axis], label] }));
  const clearFilters = () => {
    setYears([]);
    setTags({ subject: [], topic: [], specialty: [] });
  };

  const describe = () => {
    const bits = [exam, years.length ? years.join(", ") : "all years", ...Object.values(activeTags).flat()];
    return bits.filter(Boolean).join(" · ");
  };

  const practice = () => {
    if (!exam) return;
    onPractice(
      {
        past_paper_exam: exam,
        years: years.length ? years : null,
        tags: activeTags,
        num_questions: count,
        prefer_unseen: true,
        drill_wrong: missedOnly,
      },
      `Past papers · ${describe()}`
    );
  };

  const startTimed = async () => {
    if (!exam) return;
    setCreating(true);
    try {
      const r = await fetch(`${API}/api/past-papers/timed`, {
        method: "POST", headers: headers(), credentials: "include",
        body: JSON.stringify({ exam, years: years.length ? years : null, tags: activeTags, count: timed.count, minutes: timed.minutes }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      onTimedPaper(body.mock_id);
    } catch (e) {
      toast.error(`Could not create the paper: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCreating(false);
    }
  };

  if (error) {
    return (
      <div className="dashboard-view" style={{ padding: "var(--sp-6)" }}>
        <p style={{ color: "var(--error)" }}>{error}</p>
        <button className="btn-workspace" onClick={() => { setError(null); setRefreshKey((k) => k + 1); }}><RotateCcw size={12} /> Try again</button>
      </div>
    );
  }
  if (!exams) {
    return (
      <div className="dashboard-view" style={{ padding: "var(--sp-6)", color: "var(--text-muted)", display: "flex", gap: "8px", alignItems: "center" }}>
        <Loader2 size={16} className="animate-spin" /> Loading past papers…
      </div>
    );
  }
  if (locked || exams.length === 0) {
    return (
      <div className="dashboard-view" style={{ maxWidth: "640px", margin: "0 auto", textAlign: "center", padding: "var(--sp-6)" }}>
        <Lock size={30} style={{ color: "var(--text-muted)" }} />
        <h1 className="dashboard-title" style={{ marginTop: "10px" }}>Past papers</h1>
        <p style={{ color: "var(--text-secondary)" }}>
          {locked ? "Past papers are not available on this account yet." : "No past papers have been imported yet."}
        </p>
      </div>
    );
  }

  const current = exams.find((e) => e.exam === exam) || exams[0];
  const chip = (active: boolean): React.CSSProperties => ({
    padding: "4px 10px", borderRadius: "999px", fontSize: "0.76rem", cursor: "pointer",
    border: `1px solid ${active ? "var(--sky)" : "var(--border-light)"}`,
    background: active ? "rgba(48,197,255,0.14)" : "var(--surface-3)", color: "var(--text-primary)",
  });

  return (
    <div className="dashboard-view" role="region" aria-label="Past papers" style={{ maxWidth: "960px", margin: "0 auto" }}>
      <div className="dashboard-header">
        <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <History size={20} style={{ color: "var(--sky)" }} /> Past papers
        </h1>
        <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--text-secondary)" }}>
          Recalled questions from past sittings, by year. Filter by subject, topic or specialty group, then practise at your own pace or
          sit a timed paper. Answers feed your review schedule like any other question.
        </p>
      </div>

      {exams.length > 1 ? (
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "var(--sp-4)" }} role="tablist" aria-label="Exam">
          {exams.map((e) => (
            <button key={e.exam} role="tab" aria-selected={e.exam === current.exam} className="btn-workspace"
              onClick={() => { setExam(e.exam); clearFilters(); }}
              style={{ fontWeight: 700, borderColor: e.exam === current.exam ? "var(--sky)" : undefined }}>
              {e.exam} <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>· {e.total.toLocaleString()}</span>
            </button>
          ))}
        </div>
      ) : null}

      <section style={{ marginBottom: "var(--sp-4)" }}>
        <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "8px" }}>
          Years
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: "8px" }}>
          {current.papers.map((p) => {
            const on = p.year != null && years.includes(p.year);
            return (
              <button key={p.id} type="button" onClick={() => p.year != null && toggleYear(p.year)} aria-pressed={on}
                style={{ textAlign: "left", padding: "10px 12px", borderRadius: "12px", cursor: "pointer",
                  border: `1px solid ${on ? "var(--sky)" : "var(--border-light)"}`, background: on ? "rgba(48,197,255,0.10)" : "var(--surface-2)", color: "var(--text-primary)" }}>
                <div style={{ fontWeight: 800, fontSize: "1.05rem" }}>{p.year ?? p.title}</div>
                <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>{p.total.toLocaleString()} questions</div>
                <div style={{ height: "5px", borderRadius: "999px", background: "var(--surface-3)", marginTop: "6px" }}>
                  <div style={{ width: `${pct(p.answered, p.total)}%`, height: "100%", borderRadius: "999px", background: "var(--sky)" }} />
                </div>
                <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", marginTop: "3px" }}>
                  {p.answered ? `${pct(p.answered, p.total)}% done · ${pct(p.correct, p.answered)}% right` : "Not started"}
                </div>
              </button>
            );
          })}
        </div>
      </section>

      {AXES.map(({ axis, label, hint }) => {
        const all = scope?.facets?.[axis] || [];
        const selected = tags[axis];
        const shown = showAll[axis] ? all : all.slice(0, 14);
        if (!all.length && !selected.length) return null;
        return (
          <section key={axis} style={{ marginBottom: "var(--sp-3)" }}>
            <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "6px" }} title={hint}>
              {label}{selected.length ? ` · ${selected.length} selected` : ""}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
              {shown.map((f) => (
                <button key={f.label} type="button" onClick={() => toggleTag(axis, f.label)} aria-pressed={selected.includes(f.label)} style={chip(selected.includes(f.label))}>
                  {f.label} <span style={{ color: "var(--text-muted)" }}>{f.count.toLocaleString()}</span>
                </button>
              ))}
              {all.length > 14 ? (
                <button type="button" className="btn-workspace" style={{ padding: "3px 10px", fontSize: "0.72rem" }}
                  onClick={() => setShowAll((s) => ({ ...s, [axis]: !s[axis] }))}>
                  {showAll[axis] ? "Fewer" : `All ${all.length}`}
                </button>
              ) : null}
            </div>
          </section>
        );
      })}

      <div style={{ position: "sticky", bottom: 0, marginTop: "var(--sp-4)", padding: "14px", borderRadius: "14px", border: "1px solid var(--border-light)", background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: "10px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", fontSize: "0.85rem" }}>
          {scopeLoading ? <Loader2 size={14} className="animate-spin" /> : null}
          <strong>{(scope?.count ?? 0).toLocaleString()} questions</strong>
          <span style={{ color: "var(--text-secondary)" }}>
            {describe()} · answered {(scope?.answered ?? 0).toLocaleString()} · missed {(scope?.missed ?? 0).toLocaleString()}
          </span>
          {years.length || Object.keys(activeTags).length ? (
            <button type="button" className="btn-workspace" onClick={clearFilters} style={{ padding: "2px 8px", fontSize: "0.72rem" }}>
              <X size={11} /> Clear filters
            </button>
          ) : null}
        </div>
        <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>Practice</span>
            {[10, 20, 50, 100].map((n) => (
              <button key={n} type="button" style={chip(count === n)} onClick={() => setCount(n)}>{n}</button>
            ))}
            <label style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "0.76rem", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={missedOnly} onChange={(e) => setMissedOnly(e.target.checked)} /> missed only
            </label>
            <button className="btn-workspace" disabled={!scope?.count || (missedOnly && !scope?.missed)} onClick={practice}
              style={{ background: "var(--sky)", color: "#0b1320", fontWeight: 700 }}>
              <PlayCircle size={13} /> Practise
            </button>
          </div>
          <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>Timed paper</span>
            {[{ count: 50, minutes: 60 }, { count: 100, minutes: 120 }, { count: 200, minutes: 240 }].map((t) => (
              <button key={t.count} type="button" style={chip(timed.count === t.count)} onClick={() => setTimed(t)}>
                {t.count} in {t.minutes / 60}h
              </button>
            ))}
            <button className="btn-workspace" disabled={!scope?.count || creating} onClick={startTimed}>
              {creating ? <Loader2 size={13} className="animate-spin" /> : <Timer size={13} />} Start timed paper
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
