"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Loader2, PlayCircle, RotateCcw, TrendingUp } from "lucide-react";
import { API } from "@/lib/constants";

interface Pct { accuracy: number | null }
interface Feature extends Pct { feature: string; answered: number; last_at: string }
interface Week extends Pct { week_start: string; answered: number }
interface Subject extends Pct { subject: string; answered: number }
interface SessionRow extends Pct {
  ref: string; feature: string; label: string; answered: number; correct: number; started_at: string;
  review: { quiz_attempt_id?: number; mock_id?: number } | null;
}
interface Overview {
  totals: { answered: number; correct: number; accuracy: number | null; active_days: number; last7_answered: number;
    last7_accuracy: number | null; streak: { current: number; best: number }; pass_line: number };
  features: Feature[]; weeks: Week[]; subjects: Subject[]; weakest: Subject[];
  sessions: { total: number; offset: number; items: SessionRow[] };
}

const card: React.CSSProperties = {
  background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--r-xl)", padding: "var(--sp-5)",
};
const h2: React.CSSProperties = { fontSize: "0.95rem", fontWeight: 600, margin: "0 0 12px", color: "var(--text-primary)" };
const muted: React.CSSProperties = { fontSize: "0.75rem", color: "var(--text-muted)" };
const pct = (v: number | null) => (v == null ? "–" : `${Math.round(v)}%`);
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** The last 12 weeks, missing weeks filled with zero so the bars keep their place. */
function twelveWeeks(weeks: Week[]): Week[] {
  const byStart = new Map(weeks.map((w) => [w.week_start, w]));
  const monday = new Date();
  monday.setHours(0, 0, 0, 0);
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  const out: Week[] = [];
  for (let i = 11; i >= 0; i--) {
    const d = new Date(monday);
    d.setDate(d.getDate() - 7 * i);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    out.push(byStart.get(key) ?? { week_start: key, answered: 0, accuracy: null });
  }
  return out;
}

function Tile({ value, label, sub }: { value: string; label: string; sub?: string }) {
  return (
    <div style={{ ...card, padding: "14px 16px" }}>
      <div style={{ fontSize: "1.6rem", fontWeight: 800, color: "var(--text-primary)", lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", marginTop: "4px" }}>{label}</div>
      {sub ? <div style={{ ...muted, marginTop: "2px" }}>{sub}</div> : null}
    </div>
  );
}

/** Questions answered per week: one series, so one hue and no legend; accuracy is in the tooltip, not a second axis. */
function WeeklyBars({ weeks }: { weeks: Week[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...weeks.map((w) => w.answered));
  const H = 140;
  return (
    <div style={{ position: "relative" }}>
      <div role="img" aria-label="Questions answered per week, last 12 weeks"
        style={{ display: "grid", gridTemplateColumns: `repeat(${weeks.length}, 1fr)`, gap: "2px", alignItems: "end", height: H,
          borderBottom: "1px solid var(--border)" }}>
        {weeks.map((w, i) => (
          <div key={w.week_start} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
            style={{ height: "100%", display: "flex", alignItems: "flex-end", justifyContent: "center", cursor: "default" }}>
            <div style={{ width: "min(28px, 70%)", height: w.answered ? Math.max(3, (w.answered / max) * (H - 18)) : 0,
              background: "var(--sky)", borderRadius: "4px 4px 0 0", opacity: hover === null || hover === i ? 1 : 0.55 }} />
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${weeks.length}, 1fr)`, gap: "2px", marginTop: "4px" }}>
        {weeks.map((w, i) => (
          <div key={w.week_start} style={{ ...muted, fontSize: "0.66rem", textAlign: "center" }}>{i % 2 === weeks.length % 2 ? "" : day(w.week_start)}</div>
        ))}
      </div>
      {hover !== null ? (
        <div role="status" style={{ position: "absolute", top: 0, left: `${((hover + 0.5) / weeks.length) * 100}%`, transform: "translateX(-50%)",
          background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: "8px", padding: "6px 10px", fontSize: "0.75rem",
          pointerEvents: "none", whiteSpace: "nowrap", boxShadow: "0 4px 12px rgba(0,0,0,0.12)", color: "var(--text-primary)" }}>
          Week of {day(weeks[hover].week_start)}: <b>{weeks[hover].answered}</b> answered · {pct(weeks[hover].accuracy)} right
        </div>
      ) : null}
    </div>
  );
}

/** Stats from every answer, whatever feature it came from: totals, by feature, by week, by subject, and each session. */
export default function StatsView({
  token,
  onReviewQuiz,
  onOpenPaper,
  onPractise,
}: {
  token: string | null;
  onReviewQuiz: (attemptId: number) => void;
  onOpenPaper: (mockId: number) => void;
  onPractise: (filters: Record<string, unknown>, label: string) => void;
}) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [moreBusy, setMoreBusy] = useState(false);

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  const load = useCallback((offset = 0) => fetch(`${API}/api/stats/overview?sessions_offset=${offset}`, {
    headers: headers(), credentials: "include",
  }).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json() as Promise<Overview>;
  }), [headers]);

  useEffect(() => {
    let cancelled = false;
    load().then((d) => !cancelled && setData(d)).catch((e) => !cancelled && setError(String(e.message || e)));
    return () => { cancelled = true; };
  }, [load]);

  const loadMore = () => {
    if (!data) return;
    setMoreBusy(true);
    load(data.sessions.items.length)
      .then((d) => setData({ ...data, sessions: { ...d.sessions, items: [...data.sessions.items, ...d.sessions.items] } }))
      .finally(() => setMoreBusy(false));
  };

  const header = (
    <div className="dashboard-header">
      <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <TrendingUp size={20} style={{ color: "var(--sky)" }} /> Stats
      </h1>
      <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--text-secondary)" }}>
        Every answer counts here, whichever feature it came from: Daily Dose, practice, past papers, twists, mocks and challenges.
      </p>
    </div>
  );

  if (error) {
    return (
      <div className="dashboard-view" style={{ maxWidth: "980px", margin: "0 auto" }}>
        {header}
        <p style={{ color: "var(--error)" }}>Could not load your stats ({error}).</p>
        <button className="btn-workspace" onClick={() => { setError(null); load().then(setData).catch((e) => setError(String(e.message || e))); }}>
          <RotateCcw size={12} /> Try again
        </button>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="dashboard-view" style={{ maxWidth: "980px", margin: "0 auto" }}>
        {header}
        <div style={{ ...muted, display: "flex", gap: "8px", alignItems: "center" }}><Loader2 size={14} className="animate-spin" /> Loading your stats…</div>
      </div>
    );
  }

  const t = data.totals;
  if (t.answered === 0) {
    return (
      <div className="dashboard-view" style={{ maxWidth: "980px", margin: "0 auto" }}>
        {header}
        <div className="empty-state-card">
          <TrendingUp size={24} className="empty-icon" />
          <span className="empty-title">No answers yet</span>
          <span className="empty-desc">Answer questions anywhere (today&apos;s Daily Dose is a good start) and your stats appear here.</span>
        </div>
      </div>
    );
  }

  const weeks = twelveWeeks(data.weeks);
  const maxFeature = Math.max(1, ...data.features.map((f) => f.answered));

  return (
    <div className="dashboard-view" role="region" aria-label="Stats" style={{ maxWidth: "980px", margin: "0 auto", paddingBottom: "var(--sp-8)" }}>
      {header}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: "10px", marginBottom: "var(--sp-5)" }}>
        <Tile value={t.answered.toLocaleString()} label="Questions answered" sub={`${t.last7_answered.toLocaleString()} in the last 7 days`} />
        <Tile value={pct(t.accuracy)} label="Accuracy" sub={`Last 7 days: ${pct(t.last7_accuracy)} · pass line ${t.pass_line}%`} />
        <Tile value={t.active_days.toLocaleString()} label="Days studied" />
        <Tile value={`${t.streak.current}`} label="Day streak" sub={`Best ${t.streak.best}`} />
      </div>

      <section style={{ ...card, marginBottom: "var(--sp-5)" }}>
        <h2 style={h2}>Questions per week</h2>
        <WeeklyBars weeks={weeks} />
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: "var(--sp-5)", marginBottom: "var(--sp-5)" }}>
        <section style={card}>
          <h2 style={h2}>By feature</h2>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
            <thead>
              <tr style={{ ...muted, textAlign: "left" }}>
                <th style={{ padding: "4px 0", fontWeight: 600 }}>Feature</th>
                <th style={{ padding: "4px 8px", fontWeight: 600 }}>Answered</th>
                <th style={{ padding: "4px 0", fontWeight: 600, textAlign: "right" }}>Right</th>
              </tr>
            </thead>
            <tbody>
              {data.features.map((f) => (
                <tr key={f.feature} style={{ borderTop: "1px solid var(--border-light)" }}>
                  <td style={{ padding: "7px 0", color: "var(--text-primary)" }}>
                    {f.feature}
                    <div style={muted}>last {day(f.last_at)}</div>
                  </td>
                  <td style={{ padding: "7px 8px", width: "45%" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <div style={{ flex: 1, height: "8px", background: "var(--surface-3)", borderRadius: "4px" }}>
                        <div style={{ width: `${(f.answered / maxFeature) * 100}%`, height: "100%", background: "var(--sky)", borderRadius: "4px" }} />
                      </div>
                      <span style={{ minWidth: "3ch", textAlign: "right", color: "var(--text-secondary)" }}>{f.answered.toLocaleString()}</span>
                    </div>
                  </td>
                  <td style={{ padding: "7px 0", textAlign: "right", fontWeight: 600, color: "var(--text-primary)" }}>{pct(f.accuracy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section style={card}>
          <h2 style={h2}>By subject</h2>
          {data.subjects.length === 0 ? <p style={muted}>No subject answers yet.</p> : null}
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            {data.subjects.map((s) => (
              <div key={s.subject} title={`${s.subject}: ${s.answered} answered, ${pct(s.accuracy)} right`}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", color: "var(--text-primary)" }}>
                  <span>{s.subject}</span>
                  <span style={{ color: "var(--text-secondary)" }}>{pct(s.accuracy)} <span style={muted}>· {s.answered}</span></span>
                </div>
                <div style={{ position: "relative", height: "8px", background: "var(--surface-3)", borderRadius: "4px", marginTop: "3px" }}>
                  <div style={{ width: `${s.accuracy ?? 0}%`, height: "100%", background: "var(--sky)", borderRadius: "4px" }} />
                  <div aria-hidden style={{ position: "absolute", left: `${t.pass_line}%`, top: "-3px", bottom: "-3px", width: "2px", background: "var(--text-muted)" }} />
                </div>
              </div>
            ))}
          </div>
          {data.subjects.length ? <p style={{ ...muted, margin: "8px 0 0" }}>The mark on each bar is the {t.pass_line}% pass line.</p> : null}
          {data.weakest.length ? (
            <div style={{ marginTop: "12px", borderTop: "1px solid var(--border-light)", paddingTop: "10px" }}>
              <div style={{ ...muted, fontWeight: 600, marginBottom: "6px" }}>Weakest (10+ answers)</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                {data.weakest.map((s) => (
                  <button key={s.subject} className="btn-workspace" style={{ padding: "4px 10px", fontSize: "0.76rem" }}
                    onClick={() => onPractise({ sub_categories: [s.subject], num_questions: 20, prefer_unseen: true }, `Practice · ${s.subject}`)}>
                    <PlayCircle size={12} /> {s.subject} · {pct(s.accuracy)}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </section>
      </div>

      <section style={card}>
        <h2 style={h2}>Sessions <span style={{ ...muted, fontWeight: 400 }}>· {data.sessions.total.toLocaleString()}</span></h2>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
            <thead>
              <tr style={{ ...muted, textAlign: "left" }}>
                <th style={{ padding: "4px 8px 4px 0", fontWeight: 600 }}>When</th>
                <th style={{ padding: "4px 8px", fontWeight: 600 }}>Session</th>
                <th style={{ padding: "4px 8px", fontWeight: 600, textAlign: "right" }}>Score</th>
                <th style={{ padding: "4px 0", fontWeight: 600 }} />
              </tr>
            </thead>
            <tbody>
              {data.sessions.items.map((s) => (
                <tr key={s.ref} style={{ borderTop: "1px solid var(--border-light)" }}>
                  <td style={{ padding: "8px 8px 8px 0", whiteSpace: "nowrap", color: "var(--text-secondary)" }}>
                    {new Date(s.started_at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                  </td>
                  <td style={{ padding: "8px", color: "var(--text-primary)" }}>
                    {s.label}
                    {s.label !== s.feature ? <div style={muted}>{s.feature}</div> : null}
                  </td>
                  <td style={{ padding: "8px", textAlign: "right", whiteSpace: "nowrap" }}>
                    <b>{s.correct}/{s.answered}</b> <span style={muted}>{pct(s.accuracy)}</span>
                  </td>
                  <td style={{ padding: "8px 0", textAlign: "right" }}>
                    {s.review?.quiz_attempt_id ? (
                      <button className="btn-workspace" style={{ padding: "3px 8px", fontSize: "0.72rem" }} onClick={() => onReviewQuiz(s.review!.quiz_attempt_id!)}>Review</button>
                    ) : s.review?.mock_id ? (
                      <button className="btn-workspace" style={{ padding: "3px 8px", fontSize: "0.72rem" }} onClick={() => onOpenPaper(s.review!.mock_id!)}>Result</button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.sessions.items.length < data.sessions.total ? (
          <button className="btn-workspace" style={{ marginTop: "10px" }} onClick={loadMore} disabled={moreBusy}>
            {moreBusy ? <Loader2 size={12} className="animate-spin" /> : null} Show more sessions
          </button>
        ) : null}
      </section>
    </div>
  );
}
