"use client";

import React, { useCallback, useEffect, useState } from "react";
import { History, Loader2, Lock, PlayCircle, RotateCcw, Shuffle, Timer, X, Zap } from "lucide-react";
import type { ReviewScope } from "@/components/views/RapidReviewView";
import { toast } from "sonner";
import { API } from "@/lib/constants";

type Axis = "subject" | "topic" | "specialty" | "system" | "source";
const AXES: { axis: Axis; label: string; hint: string }[] = [
  { axis: "subject", label: "Subject", hint: "FCPS Part 1 subject" },
  { axis: "topic", label: "Topic", hint: "Topic within the subjects" },
  { axis: "specialty", label: "Faculty paper", hint: "Which faculty's paper (Medicine, Surgery, Gynae & Obs...) the question was asked in" },
  { axis: "system", label: "Body system", hint: "Organ system (MediVerse labels)" },
  { axis: "source", label: "Archive", hint: "Which archive recalled it. A question both archives recalled counts once and is shown once." },
];
const EMPTY_TAGS: Record<Axis, string[]> = { subject: [], topic: [], specialty: [], system: [], source: [] };

interface Stats { total: number; answered: number; correct: number }
interface Paper extends Stats { id: number; year: number | null; title: string; source?: string }
interface Year extends Stats { year: number | null; papers: Paper[] }
interface Exam extends Stats { exam: string; years?: Year[]; papers: Paper[] }
interface Scope { count: number; answered: number; missed: number; facets: Record<Axis, { label: string; count: number }[]> }

const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);
/** "FCPS Part 1 · Surgery · 18 Sep 2019 (M+E)" -> "Surgery · 18 Sep 2019 (M+E)" */
const sittingName = (p: Paper, exam: string) => {
  const t = p.title.startsWith(`${exam} · `) ? p.title.slice(exam.length + 3) : p.title;
  return p.source === "Radiant" && p.year != null && t === String(p.year) ? "Whole year (Radiant)" : t;
};
/** Exams from before sittings existed carry papers only: one year per paper. */
const yearsOf = (e: Exam): Year[] =>
  e.years ?? e.papers.map((p) => ({ year: p.year, total: p.total, answered: p.answered, correct: p.correct, papers: [p] }));

/** Past papers by exam and year, filtered by subject / topic / specialty; practice or sit a timed paper. */
export default function PastPapersView({
  token,
  onPractice,
  onTimedPaper,
  onRapidReview,
}: {
  token: string | null;
  onPractice: (filters: Record<string, unknown>, label: string) => void;
  onTimedPaper: (mockId: number) => void;
  onRapidReview?: (scope: ReviewScope) => void;
}) {
  const [exams, setExams] = useState<Exam[] | null>(null);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exam, setExam] = useState<string | null>(null);
  const [years, setYears] = useState<number[]>([]);
  const [sittings, setSittings] = useState<number[]>([]);
  const [showCollections, setShowCollections] = useState(false);
  const [tags, setTags] = useState<Record<Axis, string[]>>(EMPTY_TAGS);
  const [scope, setScope] = useState<Scope | null>(null);
  const [scopeLoading, setScopeLoading] = useState(false);
  const [count, setCount] = useState<number | "all">(20);
  const [missedOnly, setMissedOnly] = useState(false);
  const [timed, setTimed] = useState({ count: 100, minutes: 120 });
  const [creating, setCreating] = useState(false);
  const [showAll, setShowAll] = useState<Record<Axis, boolean>>({ subject: false, topic: false, specialty: false, system: false, source: false });
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

  // Picked sittings replace the year filter (collections have no year); they travel as tags.paper.
  const activeTags = {
    ...Object.fromEntries(Object.entries(tags).filter(([, v]) => v.length)),
    ...(sittings.length ? { paper: sittings.map(String) } : {}),
  } as Record<string, string[]>;
  const yearsParam = sittings.length || !years.length ? null : years;
  const filterKey = JSON.stringify({ exam, yearsParam, activeTags });

  useEffect(() => {
    if (!exam) return;
    let cancelled = false;
    const id = setTimeout(() => {
      setScopeLoading(true);
      fetch(`${API}/api/past-papers/scope`, {
        method: "POST", headers: headers(), credentials: "include",
        body: JSON.stringify({ exam, years: yearsParam, tags: activeTags }),
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

  const toggleYear = (y: number) => {
    // A sitting belongs to its year: dropping a year drops its picked sittings.
    if (years.includes(y)) {
      const cur = exams?.find((e) => e.exam === exam);
      const gone = new Set(cur ? yearsOf(cur).filter((yr) => yr.year === y).flatMap((yr) => yr.papers.map((p) => p.id)) : []);
      setSittings((s) => s.filter((id) => !gone.has(id)));
    }
    setYears((ys) => (ys.includes(y) ? ys.filter((x) => x !== y) : [...ys, y].sort()));
  };
  const toggleSitting = (id: number) => setSittings((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const toggleTag = (axis: Axis, label: string) =>
    setTags((t) => ({ ...t, [axis]: t[axis].includes(label) ? t[axis].filter((x) => x !== label) : [...t[axis], label] }));
  const clearFilters = () => {
    setYears([]);
    setSittings([]);
    setShowCollections(false);
    setTags(EMPTY_TAGS);
  };

  const describe = () => {
    const labels = Object.entries(activeTags).filter(([a]) => a !== "paper").flatMap(([, v]) => v);
    const when = sittings.length ? `${sittings.length} sitting${sittings.length > 1 ? "s" : ""}`
      : years.length ? years.join(", ") : "all years";
    return [exam, when, ...labels].filter(Boolean).join(" · ");
  };

  const practice = (twists = false) => {
    if (!exam) return;
    onPractice(
      {
        past_paper_exam: exam,
        years: yearsParam,
        tags: activeTags,
        // "All": work through the whole selection 50 at a time (unseen first, Continue after each batch).
        num_questions: twists ? 20 : count === "all" ? 50 : count,
        prefer_unseen: true,
        drill_wrong: !twists && missedOnly,
        twists,
      },
      twists ? `Twists · ${describe()}` : `Past papers · ${describe()}${count === "all" ? " · all" : ""}`
    );
  };

  const startTimed = async () => {
    if (!exam) return;
    setCreating(true);
    try {
      const r = await fetch(`${API}/api/past-papers/timed`, {
        method: "POST", headers: headers(), credentials: "include",
        body: JSON.stringify({ exam, years: yearsParam, tags: activeTags, count: timed.count, minutes: timed.minutes }),
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
  const allYears = yearsOf(current);
  // Sittings of the selected years (and the undated pools when "Collections" is open).
  const openPapers = allYears
    .filter((y) => (y.year != null ? years.includes(y.year) : showCollections))
    .flatMap((y) => y.papers);
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
          Recalled questions from past sittings, by year; pick a year to choose single sittings. Filter by subject, topic, faculty
          paper, body system or archive, then practise at your own pace or sit a timed paper. A question both archives recalled is
          shown once. Answers feed your review schedule like any other question.
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
          {allYears.map((y) => {
            const on = y.year != null ? years.includes(y.year) : showCollections;
            const dated = y.papers.filter((p) => p.source !== "Radiant").length;
            return (
              <button key={y.year ?? "collections"} type="button" aria-pressed={on}
                onClick={() => (y.year != null ? toggleYear(y.year) : setShowCollections((v) => !v))}
                style={{ textAlign: "left", padding: "10px 12px", borderRadius: "12px", cursor: "pointer",
                  display: "flex", flexDirection: "column", justifyContent: "flex-start",   // same top line when text wraps
                  border: `1px solid ${on ? "var(--sky)" : "var(--border-light)"}`, background: on ? "rgba(48,197,255,0.10)" : "var(--surface-2)", color: "var(--text-primary)" }}>
                <div style={{ fontWeight: 800, fontSize: "1.05rem" }}>{y.year ?? "Collections"}</div>
                <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                  {y.total.toLocaleString()} questions{dated ? ` · ${dated} ${y.year != null ? "sittings" : "pools"}` : ""}
                </div>
                <div style={{ height: "5px", borderRadius: "999px", background: "var(--surface-3)", marginTop: "6px" }}>
                  <div style={{ width: `${pct(y.answered, y.total)}%`, height: "100%", borderRadius: "999px", background: "var(--sky)" }} />
                </div>
                <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", marginTop: "3px" }}>
                  {y.answered ? `${pct(y.answered, y.total)}% done · ${pct(y.correct, y.answered)}% right` : "Not started"}
                </div>
              </button>
            );
          })}
        </div>
        {openPapers.length > 1 || (showCollections && openPapers.length) ? (
          <div style={{ marginTop: "10px" }}>
            <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "6px" }}
              title="Practise single sittings. With none picked, the whole of each selected year is used.">
              Sittings{sittings.length ? ` · ${sittings.length} picked` : " · optional"}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", maxHeight: "220px", overflowY: "auto" }}>
              {openPapers.map((p) => (
                <button key={p.id} type="button" onClick={() => toggleSitting(p.id)} aria-pressed={sittings.includes(p.id)}
                  style={chip(sittings.includes(p.id))} title={`${p.title} · ${p.source ?? ""}`}>
                  {sittingName(p, current.exam)} <span style={{ color: "var(--text-muted)" }}>{p.total.toLocaleString()}</span>
                  {p.answered ? <span style={{ color: "var(--sky)" }}> · {pct(p.answered, p.total)}%</span> : null}
                </button>
              ))}
            </div>
          </div>
        ) : null}
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
          {years.length || sittings.length || Object.keys(activeTags).length ? (
            <button type="button" className="btn-workspace" onClick={clearFilters} style={{ padding: "2px 8px", fontSize: "0.72rem" }}>
              <X size={11} /> Clear filters
            </button>
          ) : null}
        </div>
        <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>Practice</span>
            {[10, 20, 50].map((n) => (
              <button key={n} type="button" style={chip(count === n)} onClick={() => setCount(n)}>{n}</button>
            ))}
            <button type="button" style={chip(count === "all")} onClick={() => setCount("all")}
              title="Work through every question in this selection, 50 at a time; unanswered ones first">
              All ({(scope?.count ?? 0).toLocaleString()})
            </button>
            <label style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "0.76rem", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={missedOnly} onChange={(e) => setMissedOnly(e.target.checked)} /> missed only
            </label>
            <button className="btn-workspace" disabled={!scope?.count || (missedOnly && !scope?.missed)} onClick={() => practice()}
              style={{ background: "var(--sky)", color: "#0b1320", fontWeight: 700 }}>
              <PlayCircle size={13} /> Practise
            </button>
          </div>
          <button className="btn-workspace" disabled={!scope?.count} onClick={() => practice(true)}
            title="Questions written from these past-paper questions that ask something different (next step, mechanism, a changed finding...). Checked against your textbooks. Generate them with 'Twist it' after answering a question.">
            <Shuffle size={13} /> Twists
          </button>
          {onRapidReview ? (
            <button className="btn-workspace" disabled={!scope?.count}
              title="Answer keys at a glance, a one-page summary (pick a subject or topic) and a 10-question drill"
              onClick={() => exam && onRapidReview({ label: describe(), exam, years: yearsParam, tags: activeTags })}>
              <Zap size={13} /> Rapid review
            </button>
          ) : null}
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
