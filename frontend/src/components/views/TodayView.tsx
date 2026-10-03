"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ArrowRight, BookOpenCheck, Flame, GraduationCap, PlayCircle, Stethoscope, Target } from "lucide-react";
import { API } from "@/lib/constants";
import PageShell from "@/components/layout/PageShell";
import { ReadinessCard } from "@/components/ReadinessCard";
import type { PracticeScope } from "@/components/PracticePicker";
import type { NavParams, ViewId } from "@/lib/nav";

interface TodayData {
  continue: { attempt_id: number; label: string; answered: number; total: number } | null;
  dose: { started: boolean; completed: boolean; done: number; total: number };
  focus: { subject: string; topic: string; answered: number; accuracy: number | null }[];
}
interface TreeSubject { subject: string; bank: number; past: number }

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

/** Today: a plan for the day instead of a menu. Daily Dose, the session to continue, the weakest topics (practise
 *  or revise them), readiness, and every subject one click from a practice session. */
export default function TodayView({ token, username, getHeaders, onNavigate, onPractise, onPractiseScope, onResume }: {
  token: string | null;
  username: string | null;
  getHeaders: () => HeadersInit;
  onNavigate: (v: ViewId, params?: NavParams) => void;
  onPractise: (filters: Record<string, unknown>, label: string) => void;
  onPractiseScope: (scope: PracticeScope) => void;
  onResume: (attemptId: number) => void;
}) {
  const [data, setData] = useState<TodayData | null>(null);
  const [tree, setTree] = useState<TreeSubject[]>([]);
  const load = useCallback(async () => {
    const [t, p] = await Promise.all([
      fetch(`${API}/api/today`, { headers: getHeaders(), credentials: "include" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch(`${API}/api/practice/tree`, { headers: getHeaders(), credentials: "include" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    if (t) setData(t);
    if (p?.subjects) setTree(p.subjects.map((s: { subject: string; bank?: number; past?: number }) => ({ subject: s.subject, bank: s.bank ?? 0, past: s.past ?? 0 })));
  }, [getHeaders]);
  useEffect(() => { load(); }, [load]);

  const dose = data?.dose;
  const reviseTopic = (topic: string) => {
    try { localStorage.setItem("mednama_revise_topic", topic); } catch { /* storage unavailable */ }
    onNavigate("revise");
  };

  return (
    <PageShell width="wide" title={<>{greeting()}, <i style={{ color: "var(--sky)" }}>Dr. {username}</i></>}
      subtitle="Your plan for today. Everything else is one click away in Learn, Practise and Review.">
      <section aria-label="Today's plan" className="tile-grid">
        <button className="tile" onClick={() => onNavigate("daily")}>
          <span className="tile-kicker"><Flame size={12} style={{ color: "#f59e0b", verticalAlign: -1 }} /> Daily Dose</span>
          <span className="tile-title">{dose?.completed ? "Done for today ✓" : dose?.started ? `Continue: ${dose.done} of ${dose.total} done` : "Start today's Dose"}</span>
          <span className="tile-text">Re-tests that are due, your weakest subject, an image and a pearl. About 10 minutes.</span>
        </button>
        {data?.continue ? (
          <button className="tile" onClick={() => onResume(data.continue!.attempt_id)}>
            <span className="tile-kicker"><PlayCircle size={12} style={{ verticalAlign: -1 }} /> Continue</span>
            <span className="tile-title">{data.continue.label}</span>
            <span className="tile-text">{data.continue.answered} of {data.continue.total} answered · pick up where you stopped</span>
          </button>
        ) : null}
        {(data?.focus || []).slice(0, 2).map((f) => (
          <div className="tile" key={`${f.subject}|${f.topic}`}>
            <span className="tile-kicker"><Target size={12} style={{ verticalAlign: -1 }} /> Weak spot</span>
            <span className="tile-title">{f.topic}</span>
            <span className="tile-text">{f.subject} · {f.accuracy ?? 0}% on {f.answered} answered</span>
            <span className="next-actions">
              <button className="btn-workspace" onClick={() => onPractise({ sub_categories: [f.subject], topics: [f.topic], num_questions: 20, prefer_unseen: true }, `Practice · ${f.topic}`)}>
                <GraduationCap size={12} /> Practise
              </button>
              <button className="btn-workspace" onClick={() => reviseTopic(f.topic)}><BookOpenCheck size={12} /> Revise</button>
            </span>
          </div>
        ))}
        <button className="tile" onClick={() => onNavigate("chat")}>
          <span className="tile-kicker"><Stethoscope size={12} style={{ color: "var(--sky)", verticalAlign: -1 }} /> Ask</span>
          <span className="tile-title">Ask Dr MedNama</span>
          <span className="tile-text">Answers from your textbooks with the page, or switch to Tutor me.</span>
        </button>
      </section>

      <ReadinessCard token={token} onOpenDailyDose={() => onNavigate("daily")} onOpenLookalikes={() => onNavigate("lookalikes")}
        onOpenSprint={() => onNavigate("sprint")} />

      {tree.length ? (
        <section aria-label="Practise by subject">
          <div className="next-actions" style={{ justifyContent: "space-between", marginBottom: 10 }}>
            <span className="lbl">Practise by subject</span>
            <button className="btn-workspace" onClick={() => onNavigate("quiz")}>Topics, difficulty and more <ArrowRight size={12} /></button>
          </div>
          <div className="tile-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))" }}>
            {tree.filter((s) => s.bank + s.past > 0).map((s) => (
              <button key={s.subject} className="tile" style={{ padding: 14 }}
                onClick={() => onPractiseScope({ sources: ["past", "bank"], subjects: [s.subject], topics: [], years: [] })}>
                <span className="tile-title" style={{ fontSize: "0.95rem" }}>{s.subject}</span>
                <span className="tile-text">{(s.bank + s.past).toLocaleString()} questions</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </PageShell>
  );
}
