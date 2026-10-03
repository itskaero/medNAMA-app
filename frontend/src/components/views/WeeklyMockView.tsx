"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { CalendarClock, CheckCircle2, Flag, Loader2, RotateCcw, Share2, Timer, Trophy, XCircle } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";
import { parseMarkdown } from "@/utils/markdown";
import { shareCard } from "@/lib/shareCard";
import { ExplainOnDemand } from "@/components/ExplainOnDemand";
import QuestionPlayer, { QuestionNavigator } from "@/components/QuestionPlayer";
import SessionSummary, { NextAction } from "@/components/SessionSummary";
import { AppLinks, askAboutQuestion } from "@/lib/nav";
import { QuestionMedia } from "@/components/QuestionMedia";

type Part = "p1" | "p2";

interface Overview {
  papers: { part: Part; label: string; tracks: string[] }[];
  mock: { id: number; part: Part; track: string; week_start: string; title: string; total: number; duration_min: number; closes_on: string; pass_line: number };
  entry: { status: "not_started" | "in_progress" | "expired" | "submitted"; started_at: string | null; deadline: string | null; score: number | null; total: number | null };
  candidates: number;
  history: { week_start: string; title: string; score: number; total: number; percentile: number | null; rank: number; candidates: number }[];
}

interface Paper {
  mock_id: number;
  title: string;
  questions: { id: number; question_text: string; options: Record<string, string>; media?: number[] }[];
  answers: Record<string, Answer>;
  deadline: string;
  server_now: string;
  duration_min: number;
}

interface Answer {
  option?: string;
  flagged?: boolean;
}

interface Result {
  title: string;
  score: number;
  total: number;
  fraction: number;
  pass_line: number;
  passed: boolean;
  overtime: boolean;
  time_taken_min: number;
  candidates: number;
  rank: number;
  percentile: number | null;
  average: number | null;
  leaderboard?: { rank: number; name: string; score: number; total: number; you: boolean }[];
  subjects: { subject: string; correct: number; total: number }[];
  review: {
    id: number; question_text: string; options: Record<string, string>; correct_option: string;
    selected: string | null; flagged: boolean; is_correct: boolean; explanation_markdown: string | null; subject: string;
  }[];
}

function fmt(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** One fixed CPSP-format paper per week: 2 hours, then rank and percentile among this week's candidates. */
export default function WeeklyMockView({
  token,
  mockId = null,
  onExit,
  links,
}: {
  token: string | null;
  /** A specific paper (e.g. a personal timed past paper) instead of this week's shared papers. */
  mockId?: number | null;
  onExit?: () => void;
  links?: AppLinks;
}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [paper, setPaper] = useState<Paper | null>(null);
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [current, setCurrent] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [reviewFilter, setReviewFilter] = useState<"wrong" | "all">("wrong");
  const [clockOffset, setClockOffset] = useState(0); // server time minus client time
  const dirty = useRef(false);
  const submittedRef = useRef(false);
  const [refreshKey, setRefreshKey] = useState(0);
  // Which paper of FCPS Part 1: Paper 1 (basic sciences, all subjects mixed) or Paper 2 (a faculty, or all faculties). Remembered per browser.
  const [part, setPart] = useState<Part>(() => {
    try {
      return (localStorage.getItem("mock_part") as Part) === "p2" ? "p2" : "p1";
    } catch {
      return "p1";
    }
  });
  const [track, setTrack] = useState<string>(() => {
    try {
      return localStorage.getItem("mock_track") || "";
    } catch {
      return "";
    }
  });
  const qs = `?part=${part}&track=${encodeURIComponent(part === "p2" ? track : "")}`;
  const choosePaper = (p: Part, t: string) => {
    try {
      localStorage.setItem("mock_part", p);
      localStorage.setItem("mock_track", t);
    } catch {
      /* per-browser convenience only */
    }
    setPart(p);
    setTrack(t);
    setOverview(null);
    setResult(null);
  };

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
  }, [token]);

  // Endpoints: a specific paper by id, or this week's shared paper chosen by part/track.
  const endpoint = useCallback(
    (action: "start" | "submit" | "progress" | "result") =>
      mockId ? `${API}/api/mocks/${mockId}/${action}` : `${API}/api/mocks/weekly/${action}${qs}`,
    [mockId, qs]
  );

  useEffect(() => {
    if (!mockId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API}/api/mocks/${mockId}/start`, { method: "POST", headers: headers(), credentials: "include" });
        if (r.status === 409) {
          const res = await fetch(`${API}/api/mocks/${mockId}/result`, { headers: headers(), credentials: "include" });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          if (!cancelled) setResult(await res.json());
          return;
        }
        const body = await r.json();
        if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`);
        if (cancelled) return;
        setClockOffset(new Date(body.server_now).getTime() - Date.now());
        submittedRef.current = false;
        setPaper(body);
        setAnswers(body.answers || {});
        setCurrent(0);
      } catch (e) {
        if (!cancelled) setError(`Could not open the paper (${e instanceof Error ? e.message : String(e)}).`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mockId, headers]);

  useEffect(() => {
    if (mockId) return;
    let cancelled = false;
    fetch(`${API}/api/mocks/weekly${qs}`, { headers: headers(), credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(async (o: Overview) => {
        if (cancelled) return;
        setOverview(o);
        if (o.entry.status === "submitted") {
          const r = await fetch(`${API}/api/mocks/weekly/result${qs}`, { headers: headers(), credentials: "include" });
          if (r.ok && !cancelled) setResult(await r.json());
        }
      })
      .catch((e) => {
        if (!cancelled) setError(`Could not load the weekly mock (${e instanceof Error ? e.message : String(e)}).`);
      });
    return () => {
      cancelled = true;
    };
  }, [headers, refreshKey, qs, mockId]);

  const start = async () => {
    setBusy(true);
    try {
      const r = await fetch(endpoint("start"), { method: "POST", headers: headers(), credentials: "include" });
      const body = await r.json();
      if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`);
      setClockOffset(new Date(body.server_now).getTime() - Date.now());
      submittedRef.current = false;
      setPaper(body);
      setAnswers(body.answers || {});
      setCurrent(0);
    } catch (e) {
      toast.error(`Could not start: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const submit = useCallback(async (auto = false) => {
    if (submittedRef.current) return;
    submittedRef.current = true;
    setBusy(true);
    try {
      const r = await fetch(endpoint("submit"), {
        method: "POST", headers: headers(), credentials: "include", body: JSON.stringify({ answers }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`);
      setResult(body);
      setPaper(null);
      if (auto) toast.message("Time is up: your paper was submitted.");
      if (!mockId) setRefreshKey((k) => k + 1);
    } catch (e) {
      submittedRef.current = false;
      toast.error(`Could not submit: ${e instanceof Error ? e.message : String(e)}. Your answers are saved; try again.`);
    } finally {
      setBusy(false);
    }
  }, [answers, headers, endpoint, mockId]);

  // Clock + auto-submit at the deadline.
  useEffect(() => {
    if (!paper) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [paper]);
  const remaining = paper ? new Date(paper.deadline).getTime() - (now + clockOffset) : 0;
  useEffect(() => {
    if (paper && remaining <= 0 && !submittedRef.current) submit(true);
  }, [paper, remaining, submit]);

  const answersRef = useRef(answers);
  useEffect(() => {
    answersRef.current = answers;
  }, [answers]);

  // Autosave every 20 s when something changed.
  useEffect(() => {
    if (!paper) return;
    const id = setInterval(() => {
      if (!dirty.current) return;
      dirty.current = false;
      fetch(endpoint("progress"), {
        method: "PUT", headers: headers(), credentials: "include", body: JSON.stringify({ answers: answersRef.current }),
      }).catch(() => {
        dirty.current = true;
      });
    }, 20000);
    return () => clearInterval(id);
  }, [paper, headers, endpoint]);

  const setAnswer = (qid: number, patch: Answer) => {
    dirty.current = true;
    setAnswers((a) => ({ ...a, [String(qid)]: { ...(a[String(qid)] || {}), ...patch } }));
  };

  const shareResult = async () => {
    if (!result) return;
    try {
      const how = await shareCard(
        {
          kicker: "FCPS weekly mock",
          headline: `${result.score} / ${result.total}`,
          accent: result.passed ? "#5fd08a" : "#f59e0b",
          subline: [
            result.percentile != null ? `Top ${Math.max(1, 100 - result.percentile)}%` : `Rank ${result.rank} of ${result.candidates}`,
            result.passed ? "above the 75% pass line" : "working towards the 75% line",
          ].join(" · "),
          body: [
            `Strongest: ${result.subjects.slice(-1)[0]?.subject ?? "-"} · To work on: ${result.subjects[0]?.subject ?? "-"}`,
            "100 CPSP-style questions in 2 hours, every week.",
          ],
          footer: "Sit this week's paper on medNAMA",
        },
        `mednama-weekly-mock-${result.score}.png`,
        `I scored ${result.score}/${result.total} on this week's FCPS mock on medNAMA`
      );
      if (how === "downloaded") toast.success("Result card saved: share it anywhere.");
    } catch (e) {
      toast.error(`Could not make the card (${e instanceof Error ? e.message : String(e)}).`);
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
  if (!overview && !paper && !result) {
    return (
      <div className="dashboard-view" style={{ padding: "var(--sp-6)", color: "var(--text-muted)", display: "flex", gap: "8px", alignItems: "center" }}>
        <Loader2 size={16} className="animate-spin" /> {mockId ? "Opening your paper…" : "Loading this week\u2019s mock…"}
      </div>
    );
  }

  // ── sitting the paper ──
  if (paper) {
    const q = paper.questions[current];
    const a = answers[String(q.id)] || {};
    const answeredCount = paper.questions.filter((x) => answers[String(x.id)]?.option).length;
    const low = remaining < 10 * 60 * 1000;
    return (
      <div className="dashboard-view" role="region" aria-label="Weekly mock paper" style={{ maxWidth: "900px", margin: "0 auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}>
          <strong style={{ fontSize: "0.9rem" }}>{paper.title}</strong>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontFamily: "var(--font-mono)", fontWeight: 700, color: low ? "var(--error)" : "var(--text-primary)" }}>
            <Timer size={15} /> {fmt(remaining)}
          </span>
        </div>
        <QuestionNavigator count={paper.questions.length} current={current} onJump={setCurrent}
          answered={(i) => !!answers[String(paper.questions[i].id)]?.option}
          flagged={(i) => !!answers[String(paper.questions[i].id)]?.flagged} />
        <QuestionPlayer mcq={q} token={token} selected={a.option} onSelect={(key) => setAnswer(q.id, { option: key })}
          kicker={<>Question {current + 1} of {paper.questions.length}</>}>
        <div className="qp-bar">
          <button className="btn-workspace" onClick={() => setAnswer(q.id, { flagged: !a.flagged })}
            style={{ color: a.flagged ? "#d97706" : undefined }} title="Flagged answers count as 'unsure' for your review schedule">
            <Flag size={12} /> {a.flagged ? "Flagged for review" : "Flag for review"}
          </button>
          <div style={{ display: "flex", gap: "8px" }}>
            <button className="btn-workspace" disabled={current === 0} onClick={() => setCurrent((c) => c - 1)}>Previous</button>
            {current + 1 < paper.questions.length ? (
              <button className="btn-workspace" onClick={() => setCurrent((c) => c + 1)}>Next</button>
            ) : null}
            <button className="btn-workspace" disabled={busy}
              onClick={() => {
                const left = paper.questions.length - answeredCount;
                if (window.confirm(left ? `${left} question${left === 1 ? " is" : "s are"} unanswered. Submit anyway?` : "Submit your paper?")) submit();
              }}
              style={{ background: "var(--sky)", color: "#0b1320", fontWeight: 700 }}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : null} Submit ({answeredCount}/{paper.questions.length})
            </button>
          </div>
        </div>
        </QuestionPlayer>
      </div>
    );
  }

  const picker = overview ? (
    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", marginBottom: "var(--sp-4)" }} role="group" aria-label="Choose paper">
      {overview.papers.map((pp) => (
        <button key={pp.part} className="btn-workspace" onClick={() => choosePaper(pp.part, pp.part === "p2" ? track : "")}
          style={{ padding: "5px 14px", fontWeight: 700, borderColor: part === pp.part ? "var(--sky)" : undefined,
            background: part === pp.part ? "rgba(48,197,255,0.12)" : undefined }}>
          {pp.label}
        </button>
      ))}
      {part === "p2" ? (
        <select value={track} onChange={(e) => choosePaper("p2", e.target.value)} aria-label="Paper 2 faculty"
          style={{ background: "var(--surface-3)", border: "1px solid var(--border-light)", borderRadius: "8px", color: "var(--text-primary)", padding: "5px 8px", fontSize: "0.8rem" }}>
          <option value="">All faculties (mixed)</option>
          {(overview.papers.find((pp) => pp.part === "p2")?.tracks || []).map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      ) : null}
    </div>
  ) : null;

  // ── result ──
  if (result) {
    const shown = result.review.filter((r) => reviewFilter === "all" || !r.is_correct);
    const missedIds = result.review.filter((r) => !r.is_correct).map((r) => r.id);
    // Weakest first; Paper 2 groups by faculty or topic, which are not practice subjects.
    const weakest = result.subjects.find((x) => x.total && x.correct / x.total < result.pass_line)?.subject
      || result.subjects[0]?.subject;
    const bySubject = mockId || part === "p1";
    const firstMiss = result.review.find((r) => !r.is_correct);
    const resultActions: NextAction[] = [];
    if (links && missedIds.length) resultActions.push({ primary: true, label: `Practise the ${Math.min(missedIds.length, 100)} I missed`,
      onClick: () => links.practise({ mcq_ids: missedIds.slice(0, 100), num_questions: Math.min(missedIds.length, 100), prefer_unseen: false }, `${result.title} · missed`) });
    if (links && weakest && bySubject) {
      resultActions.push({ label: `Practise ${weakest}`, onClick: () => links.practise({ scope: { subjects: [weakest] }, num_questions: 20 }, `Practice · ${weakest}`) });
      resultActions.push({ label: `Revise ${weakest}`, onClick: () => links.revise(weakest) });
    }
    if (links && firstMiss) resultActions.push({ label: "Ask Dr MedNama about a miss", onClick: () => links.ask(askAboutQuestion(firstMiss)) });
    resultActions.push({ label: "Share my result", icon: <Share2 size={12} />, onClick: shareResult });
    if (onExit) resultActions.push({ label: "Back", onClick: onExit });
    else if (links) resultActions.push({ label: "Back to Exams", onClick: () => links.back("exams") });
    return (
      <div className="dashboard-view" role="region" aria-label="Weekly mock result" style={{ maxWidth: "860px", margin: "0 auto" }}>
        {mockId ? null : picker}
        <SessionSummary
          kicker={<><Trophy size={12} style={{ verticalAlign: -1 }} /> {mockId ? "Timed paper" : "Weekly mock"}</>}
          title={result.title}
          right={result.score} total={result.total}
          answered={result.review.filter((r) => r.selected).length}
          note={result.passed ? "Above the 75% pass line." : `${Math.max(0, Math.ceil(result.pass_line * result.total) - result.score)} more to reach the 75% pass line.`}
          stats={[
            ...(mockId ? [] : [{ label: "Rank", value: `${result.rank} of ${result.candidates}`,
              sub: result.percentile != null ? `Percentile ${result.percentile}` : "Percentile shows from 3 candidates" }]),
            { label: "Time", value: `${result.time_taken_min} min`, sub: result.overtime ? "Submitted after time" : mockId ? "Within the time limit" : `Average score ${result.average ?? "-"}` },
          ]}
          actions={resultActions}
        />
        {!mockId && result.leaderboard && result.leaderboard.length > 1 ? (
          <div style={{ marginBottom: "var(--sp-5)" }}>
            <h2 style={{ fontSize: "0.95rem", margin: "0 0 8px" }}>This week&apos;s leaderboard</h2>
            <table className="users-table" style={{ maxWidth: "420px" }}>
              <tbody>
                {result.leaderboard.map((r) => (
                  <tr key={`${r.rank}-${r.name}`} style={r.you ? { fontWeight: 700, color: "var(--sky)" } : undefined}>
                    <td style={{ width: "48px" }}>#{r.rank}</td>
                    <td>{r.you ? `${r.name} (you)` : r.name}</td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{r.score} / {r.total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "4px" }}>Other students&apos; names are shortened.</div>
          </div>
        ) : null}

        <h2 style={{ fontSize: "0.95rem", margin: "0 0 8px" }}>By {!mockId && part === "p2" && track ? "topic" : !mockId && part === "p2" ? "faculty" : "subject"} (weakest first)</h2>
        <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginBottom: "var(--sp-5)" }}>
          {result.subjects.map((s) => {
            const pct = s.total ? s.correct / s.total : 0;
            return (
              <div key={s.subject} style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "0.8rem" }}>
                <span style={{ width: "150px" }}>{s.subject}</span>
                <div style={{ flex: 1, height: "10px", borderRadius: "999px", background: "var(--surface-3)", position: "relative" }}>
                  <div style={{ width: `${pct * 100}%`, height: "100%", borderRadius: "999px", background: pct >= result.pass_line ? "var(--sea-green)" : "#d97706" }} />
                  <div style={{ position: "absolute", left: "75%", top: "-3px", bottom: "-3px", width: "2px", background: "var(--text-muted)" }} title="75% pass line" />
                </div>
                <span style={{ width: "60px", textAlign: "right", fontFamily: "var(--font-mono)", fontSize: "0.72rem" }}>{s.correct}/{s.total}</span>
              </div>
            );
          })}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
          <h2 style={{ fontSize: "0.95rem", margin: 0 }}>Review</h2>
          {(["wrong", "all"] as const).map((f) => (
            <button key={f} className="btn-workspace" onClick={() => setReviewFilter(f)}
              style={{ padding: "3px 10px", fontSize: "0.72rem", borderColor: reviewFilter === f ? "var(--sky)" : undefined }}>
              {f === "wrong" ? `Missed (${result.review.filter((r) => !r.is_correct).length})` : "All"}
            </button>
          ))}
        </div>
        <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: 0 }}>
          Every miss is already on your review schedule: its concept comes back in your Daily Dose with a new question.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {shown.map((r) => (
            <details key={r.id} style={{ border: "1px solid var(--border-light)", borderRadius: "12px", padding: "10px 12px" }}>
              <summary style={{ cursor: "pointer", display: "flex", gap: "8px", alignItems: "flex-start", listStyle: "none" }}>
                {r.is_correct ? <CheckCircle2 size={15} style={{ color: "var(--sea-green)", flexShrink: 0, marginTop: "2px" }} /> : <XCircle size={15} style={{ color: "var(--error)", flexShrink: 0, marginTop: "2px" }} />}
                <span style={{ fontSize: "0.86rem", lineHeight: 1.5 }}>{r.question_text}</span>
              </summary>
              <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.82rem" }}>
                {Object.keys(r.options).sort().map((k) => (
                  <div key={k} style={{ color: k === r.correct_option ? "var(--sea-green)" : k === r.selected ? "var(--error)" : "var(--text-secondary)", fontWeight: k === r.correct_option ? 700 : 400 }}>
                    {k}. {r.options[k]}{k === r.selected ? "  (your answer)" : ""}
                  </div>
                ))}
                {!r.selected ? <div style={{ color: "var(--text-muted)" }}>Not answered</div> : null}
                {r.explanation_markdown ? (
                  <div className="prose" style={{ fontSize: "0.82rem", marginTop: "6px" }} dangerouslySetInnerHTML={{ __html: parseMarkdown(r.explanation_markdown) }} />
                ) : (
                  <ExplainOnDemand mcqId={r.id} token={token} />
                )}
              </div>
            </details>
          ))}
        </div>
      </div>
    );
  }

  // ── overview ──
  if (!overview) return null;
  const { mock, entry } = overview;
  return (
    <div className="dashboard-view" role="region" aria-label="Weekly mock" style={{ maxWidth: "720px", margin: "0 auto" }}>
      <div className="dashboard-header">
        <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Trophy size={20} style={{ color: "#f59e0b" }} /> Weekly mock
        </h1>
        <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--text-secondary)" }}>
          Two papers every week, like the FCPS Part 1 exam: Paper 1 (basic sciences, all subjects mixed) and Paper 2 (your
          faculty&apos;s topics mixed). Each is {mock.total} single-best-answer questions in {mock.duration_min / 60} hours, no negative marking, and
          everyone sits the same questions. Afterwards: your rank and percentile, the 75% line, and a subject breakdown.
        </p>
      </div>
      {picker}
      <div style={{ border: "1px solid var(--border-light)", borderRadius: "14px", padding: "16px", display: "flex", flexDirection: "column", gap: "10px" }}>
        <strong>{mock.title}</strong>
        <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", fontSize: "0.82rem", color: "var(--text-secondary)" }}>
          <span><CalendarClock size={13} /> Closes {new Date(mock.closes_on).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" })}</span>
          <span>{overview.candidates} candidate{overview.candidates === 1 ? "" : "s"} so far</span>
        </div>
        {mock.total < 100 ? (
          <div style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>This week&apos;s bank has {mock.total} eligible questions, so the paper is shorter than 100.</div>
        ) : null}
        {entry.status === "not_started" ? (
          <>
            <p style={{ fontSize: "0.8rem", color: "var(--text-muted)", margin: 0 }}>
              One sitting per week. The clock starts when you press start and keeps running if you close the tab; answers are saved as you go.
            </p>
            <button className="btn-workspace" disabled={busy || mock.total === 0} onClick={start} style={{ alignSelf: "flex-start", background: "var(--sky)", color: "#0b1320", fontWeight: 700 }}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Timer size={12} />} Start the paper
            </button>
          </>
        ) : entry.status === "in_progress" || entry.status === "expired" ? (
          <button className="btn-workspace" disabled={busy} onClick={start} style={{ alignSelf: "flex-start" }}>
            {entry.status === "expired" ? "Time is up: open and submit" : "Resume your paper"}
          </button>
        ) : null}
      </div>

      {overview.history.length ? (
        <div style={{ marginTop: "var(--sp-5)" }}>
          <h2 style={{ fontSize: "0.95rem" }}>Your past weeks</h2>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.82rem" }}>
            {overview.history.map((h) => (
              <div key={h.week_start} style={{ display: "flex", gap: "12px" }}>
                <span style={{ flex: 1, color: "var(--text-muted)" }}>{h.title}</span>
                <span style={{ fontWeight: 700 }}>{h.score}/{h.total}</span>
                <span style={{ color: "var(--text-secondary)" }}>rank {h.rank} of {h.candidates}{h.percentile != null ? ` · percentile ${h.percentile}` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ border: "1px solid var(--border-light)", borderRadius: "12px", padding: "10px 12px" }}>
      <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
      <div style={{ fontSize: "1.3rem", fontWeight: 800, color: color || "var(--text-primary)" }}>{value}</div>
      {sub ? <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>{sub}</div> : null}
    </div>
  );
}
