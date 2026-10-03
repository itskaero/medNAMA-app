"use client";

import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Flame, GitCompareArrows, Image as ImageIcon, Loader2, Lock, RotateCcw, Share2, Sparkles, Snowflake, TrendingUp, Zap } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";
import { Figure } from "@/types";
import { ConceptCard, ConceptCardData } from "@/components/ConceptCard";
import { PairCard, PairData } from "@/components/PairCard";
import { StudyMCQ, StudyQuestion } from "@/components/StudyQuestion";
import { shareCard } from "@/lib/shareCard";
import SessionSummary, { NextAction } from "@/components/SessionSummary";
import { AppLinks, askAboutQuestion } from "@/lib/nav";

type ItemType = "review" | "new" | "image" | "pearl" | "pair" | "flash" | "sprint";

interface DoseItem {
  index: number;
  type: ItemType;
  done: boolean;
  correct?: boolean;
  high_yield?: boolean;
  times_asked?: number;
  mcq?: StudyMCQ | null;
  concept?: ConceptCardData | null;
  pair?: PairData | null;
}

interface DoseData {
  day: string;
  items: DoseItem[];
  completed: boolean;
  streak?: { current: number; best: number; done_today: boolean };
  streak_freezes?: number;
  // sprint only
  unlocked?: boolean;
  preview?: boolean;
  days_left?: number | null;
  unlocks_days_before?: number;
}

const TYPE_LABEL: Record<ItemType, string> = {
  review: "Re-test: a concept you missed",
  new: "New question",
  image: "Spot the diagnosis",
  pearl: "Pearl of the day",
  pair: "Look-alikes: tell them apart",
  flash: "Your weak spot: read it once more",
  sprint: "Rapid re-test",
};

export type StudyMode = "dose" | "sprint";

export default function DailyDoseView({
  token,
  onFigureClick,
  mode = "dose",
  links,
}: {
  token: string | null;
  onFigureClick: (f: Figure) => void;
  mode?: StudyMode;
  links?: AppLinks;
}) {
  const [dose, setDose] = useState<DoseData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [current, setCurrent] = useState(0);
  const [answered, setAnswered] = useState(false);
  const [examDate, setExamDate] = useState("");

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  const [refreshKey, setRefreshKey] = useState(0);
  const load = () => {
    setError(null);
    setRefreshKey((k) => k + 1);
  };

  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/study/${mode === "sprint" ? "sprint" : "daily"}`, { headers: headers(), credentials: "include" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: DoseData) => {
        if (cancelled) return;
        setDose(data);
        const firstOpen = data.items.findIndex((i) => !i.done);
        setCurrent(firstOpen === -1 ? data.items.length : firstOpen);
      })
      .catch((e) => {
        if (!cancelled) setError(`Could not load ${mode === "sprint" ? "the final sprint" : "today's Daily Dose"} (${e instanceof Error ? e.message : String(e)}).`);
      });
    return () => {
      cancelled = true;
    };
  }, [headers, refreshKey, mode]);

  const markDone = async (item: DoseItem) => {
    await fetch(`${API}/api/study/dose/${item.index}/done${mode === "sprint" ? "?kind=sprint" : ""}`, {
      method: "POST", headers: headers(), credentials: "include",
    });
    setDose((d) => (d ? { ...d, items: d.items.map((i) => (i.index === item.index ? { ...i, done: true } : i)) } : d));
    next();
  };

  const next = () => {
    setAnswered(false);
    setCurrent((c) => c + 1);
    if (dose && current + 1 >= dose.items.length) load(); // refresh streak on completion
  };

  const shareStreak = async () => {
    if (!dose?.streak) return;
    const n = dose.streak.current;
    try {
      const how = await shareCard(
        {
          kicker: "Daily Dose streak",
          headline: `${n} day${n === 1 ? "" : "s"} in a row`,
          accent: "#f59e0b",
          subline: `Best streak: ${dose.streak.best} days`,
          body: ["Ten minutes a day: re-test what I missed, new questions, one image and one pearl, all from the textbooks."],
        },
        `mednama-streak-${n}.png`,
        `${n}-day FCPS study streak on medNAMA`
      );
      if (how === "downloaded") toast.success("Streak card saved: share it anywhere.");
    } catch (e) {
      toast.error(`Could not make the card (${e instanceof Error ? e.message : String(e)}).`);
    }
  };

  const title = mode === "sprint" ? "Final sprint" : "Daily Dose";
  if (error) {
    return (
      <div className="dashboard-view" style={{ padding: "var(--sp-6)" }}>
        <p style={{ color: "var(--error)" }}>{error}</p>
        <button className="btn-workspace" onClick={load}><RotateCcw size={12} /> Try again</button>
      </div>
    );
  }
  if (!dose) {
    return (
      <div className="dashboard-view" style={{ padding: "var(--sp-6)", color: "var(--text-muted)", display: "flex", gap: "8px", alignItems: "center" }}>
        <Loader2 size={16} className="animate-spin" /> Preparing {mode === "sprint" ? "your final sprint" : "today's Daily Dose"}…
      </div>
    );
  }

  if (mode === "sprint" && !dose.unlocked && !dose.preview) {
    return (
      <div className="dashboard-view" style={{ maxWidth: "640px", margin: "0 auto", textAlign: "center", padding: "var(--sp-6)" }}>
        <Lock size={32} style={{ color: "var(--text-muted)" }} />
        <h1 className="dashboard-title" style={{ marginTop: "10px" }}>Final sprint</h1>
        <p style={{ color: "var(--text-secondary)" }}>
          Opens in the last {dose.unlocks_days_before ?? 7} days before your exam: your weakest concepts, rapid re-tests and the look-alikes you still mix up, in about 30 minutes a day.
        </p>
        <p style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
          {dose.days_left == null ? "Set your exam date so it opens on time." : `Your exam is in ${dose.days_left} days.`}
        </p>
        <form style={{ display: "flex", gap: "8px", justifyContent: "center", flexWrap: "wrap", marginTop: "var(--sp-3)" }}
          onSubmit={async (e) => {
            e.preventDefault();
            const res = await fetch(`${API}/api/study/exam-date`, {
              method: "PUT", headers: { ...headers(), "Content-Type": "application/json" }, credentials: "include",
              body: JSON.stringify({ exam_date: examDate || null }),
            }).catch(() => null);
            if (res && res.ok) { toast.success("Exam date saved."); load(); } else toast.error("Could not save the exam date.");
          }}>
          <label style={{ display: "flex", gap: "8px", alignItems: "center", fontSize: "0.85rem", color: "var(--text-secondary)" }}>
            Exam date <input type="date" value={examDate} onChange={(e) => setExamDate(e.target.value)} required style={{ background: "var(--surface-3)", border: "1px solid var(--border-light)", borderRadius: "6px", color: "var(--text-primary)", padding: "4px 8px" }} />
          </label>
          <button className="btn-workspace" type="submit" disabled={!examDate}>Save</button>
        </form>
        {links ? (
          <div className="next-actions" style={{ justifyContent: "center", marginTop: "var(--sp-4)" }}>
            <button className="btn-workspace" onClick={() => links.go("daily")}>Today&apos;s Daily Dose</button>
            <button className="btn-workspace" onClick={() => links.go("exams")}>A timed mock</button>
          </div>
        ) : null}
      </div>
    );
  }

  const total = dose.items.length;
  const doneCount = dose.items.filter((i) => i.done).length;
  const item = dose.items[current];
  const questions = dose.items.filter((i) => i.mcq);
  const misses = questions.filter((i) => i.done && !i.correct && i.mcq);
  const doneActions: NextAction[] = [];
  if (links) {
    const m = misses[0]?.mcq;
    if (m) doneActions.push({ primary: true, label: "Ask Dr MedNama about a miss", onClick: () => links.ask(askAboutQuestion(m)) });
    if (m?.sub_category) doneActions.push({ label: `Revise ${m.sub_category}`, onClick: () => links.revise(m.sub_category!) });
    if (misses.some((i) => i.type === "pair")) doneActions.push({ label: "Look-alikes", onClick: () => links.go("lookalikes") });
    doneActions.push({ primary: !m, label: "Practise more", onClick: () => links.go("quiz") });
    doneActions.push({ label: "All my mistakes", onClick: () => links.go("mistakes") });
    doneActions.push({ label: "Back to Today", onClick: () => links.go("dashboard") });
  }
  if (dose.streak && dose.streak.current > 0) doneActions.push({ label: "Share my streak", icon: <Share2 size={12} />, onClick: shareStreak });

  return (
    <div className="dashboard-view" role="region" aria-label={title} style={{ maxWidth: "760px", margin: "0 auto" }}>
      <div className="dashboard-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
        <div>
          <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {mode === "sprint" ? <Zap size={20} style={{ color: "#f59e0b" }} /> : null}
            {title}
          </h1>
          <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--text-secondary)" }}>
            {mode === "sprint"
              ? dose.preview
                ? "Admin preview: students see this in the last 7 days before their exam."
                : `${dose.days_left} day${dose.days_left === 1 ? "" : "s"} to go: your weakest concepts, each followed by a rapid re-test, and the look-alikes you still mix up.`
              : "About 10 minutes: re-test what you missed, a few new questions, one image and one pearl."}
          </p>
        </div>
        {dose.streak ? (
          <div style={{ display: "flex", alignItems: "center", gap: "12px", fontSize: "0.82rem" }}>
            <span title="Days in a row you completed your Daily Dose" style={{ display: "flex", alignItems: "center", gap: "4px", color: "var(--text-primary)", fontWeight: 700 }}>
              <Flame size={16} style={{ color: "#f59e0b" }} /> {dose.streak.current} day{dose.streak.current === 1 ? "" : "s"}
            </span>
            <span title="Streak freezes: a missed day is covered automatically" style={{ display: "flex", alignItems: "center", gap: "4px", color: "var(--text-muted)" }}>
              <Snowflake size={14} /> {dose.streak_freezes}
            </span>
          </div>
        ) : null}
      </div>

      <div style={{ height: "6px", borderRadius: "999px", background: "var(--surface-3)", margin: "0 0 var(--sp-4)" }}>
        <div style={{ width: `${total ? (doneCount / total) * 100 : 0}%`, height: "100%", borderRadius: "999px", background: "var(--sky)", transition: "width 0.3s" }} />
      </div>

      {total === 0 ? (
        <p style={{ color: "var(--text-secondary)" }}>
          {mode === "sprint"
            ? "Nothing to sprint through yet: answer some questions first so medNAMA knows your weak spots."
            : "Nothing to study yet: generate or practise a few MCQs first, then come back."}
        </p>
      ) : !item ? (
        <SessionSummary
          kicker={<><CheckCircle2 size={12} style={{ verticalAlign: -1 }} /> {title}</>}
          title={`${title} complete`}
          right={questions.filter((i) => i.correct).length}
          total={questions.length || undefined}
          note={dose.streak
            ? <><Flame size={13} style={{ color: "#f59e0b", verticalAlign: -2 }} /> Streak: {dose.streak.current} day{dose.streak.current === 1 ? "" : "s"} (best {dose.streak.best}). Concepts you missed come back as new questions in a day.</>
            : "Come back tomorrow for a fresh set built from what is still weak."}
          cells={questions.map((i) => ({ key: i.index, state: !i.done ? "skipped" : i.correct ? "right" : "wrong" }))}
          actions={doneActions}
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
          <div style={{ fontSize: "0.7rem", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-muted)", display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
            {item.type === "image" ? <ImageIcon size={12} /> : item.type === "pearl" ? <Sparkles size={12} /> : item.type === "pair" ? <GitCompareArrows size={12} /> : null}
            {TYPE_LABEL[item.type]} · {current + 1} of {total}
            {item.high_yield ? (
              <span
                title="A topic past FCPS papers ask often. The question is written from your textbooks."
                style={{ display: "inline-flex", alignItems: "center", gap: "3px", marginLeft: "6px", padding: "1px 7px", borderRadius: "999px", background: "rgba(245, 158, 11, 0.14)", color: "#d97706", letterSpacing: "0.02em", textTransform: "none" }}
              >
                <TrendingUp size={11} /> Frequently asked{item.times_asked && item.times_asked > 1 ? ` · ${item.times_asked}×` : ""}
              </span>
            ) : null}
          </div>

          {(item.type === "pearl" || item.type === "flash") && item.concept ? (
            <>
              <ConceptCard card={item.concept} token={token} onFigureClick={onFigureClick} heading={item.type === "pearl" ? "Pearl of the day" : undefined} />
              <button className="btn-workspace" onClick={() => markDone(item)} style={{ alignSelf: "flex-end" }}>
                Got it
              </button>
            </>
          ) : item.mcq ? (
            <>
              {item.type === "pair" && item.pair && !answered ? (
                <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                  You once mixed up <strong>{item.pair.term_a}</strong> and <strong>{item.pair.term_b}</strong>. Answer first; the comparison follows.
                </div>
              ) : null}
              <StudyQuestion
                key={`${mode}-${item.index}`}
                mcq={item.mcq}
                token={token}
                onFigureClick={onFigureClick}
                answerBody={{ dose_index: item.index, ...(mode === "sprint" ? { session_kind: "sprint" } : {}) }}
                onAnswered={(r) => {
                  setAnswered(true);
                  setDose((d) => (d ? { ...d, items: d.items.map((i) => (i.index === item.index ? { ...i, done: true, correct: r.is_correct } : i)) } : d));
                }}
                onNext={next}
                nextLabel={current + 1 >= total ? "Finish" : "Next"}
                keys
                afterResult={item.type === "pair" && item.pair ? <PairCard pair={item.pair} /> : null}
              />
            </>
          ) : (
            <button className="btn-workspace" onClick={next}>Skip (item unavailable)</button>
          )}
        </div>
      )}
    </div>
  );
}
