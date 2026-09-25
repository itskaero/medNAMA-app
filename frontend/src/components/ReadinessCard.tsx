"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CalendarClock, Flame, GitCompareArrows, Target, Zap } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

interface Readiness {
  predicted_score: number | null;
  pass_line: number;
  subjects: { subject: string; answered: number; mastery: number; enough_data: boolean }[];
  missing_part1_subjects: string[];
  concepts: { tracked: number; mastered: number; due_now: number; due_this_week: number };
  exam_date: string | null;
  days_left: number | null;
  daily_target: number | null;
  streak: { current: number; best: number; done_today: boolean };
  note: string;
  mistakes?: {
    window_days: number;
    total_wrong: number;
    types: Record<"confusion" | "misconception" | "gap", { count: number; share: number }>;
    confident_errors: { concept_id: number; title: string; count: number }[];
    pairs: { open: number; cleared: number };
  };
  sprint?: { unlocked: boolean; preview: boolean; days_left: number | null; unlocks_days_before: number };
}

const MISTAKE_LABEL = {
  confusion: { label: "Look-alike mix-ups", color: "#d97706" },
  misconception: { label: "Confident but wrong", color: "var(--error)" },
  gap: { label: "Not known yet", color: "var(--sky)" },
} as const;

const pct = (x: number) => `${Math.round(x * 100)}%`;

/** Readiness vs the FCPS 75% line, concept mastery, streak and exam countdown. */
export function ReadinessCard({
  token,
  onOpenDailyDose,
  onOpenLookalikes,
  onOpenSprint,
}: {
  token: string | null;
  onOpenDailyDose?: () => void;
  onOpenLookalikes?: () => void;
  onOpenSprint?: () => void;
}) {
  const [data, setData] = useState<Readiness | null>(null);
  const [dateInput, setDateInput] = useState("");

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  const [refreshKey, setRefreshKey] = useState(0);
  const load = () => setRefreshKey((k) => k + 1);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/study/readiness`, { headers: headers(), credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((body: Readiness | null) => {
        if (!cancelled && body) {
          setData(body);
          setDateInput(body.exam_date || "");
        }
      })
      .catch(() => {
        /* dashboard still renders without it */
      });
    return () => {
      cancelled = true;
    };
  }, [headers, refreshKey]);

  const saveDate = async () => {
    const res = await fetch(`${API}/api/study/exam-date`, {
      method: "PUT",
      headers: { ...headers(), "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ exam_date: dateInput || null }),
    });
    if (res.ok) {
      toast.success(dateInput ? "Exam date saved." : "Exam date cleared.");
      load();
    } else toast.error("Could not save the exam date.");
  };

  if (!data) return null;
  const predicted = data.predicted_score;

  return (
    <section
      aria-label="Exam readiness"
      style={{
        margin: "0 0 var(--sp-5, 20px)",
        padding: "16px",
        borderRadius: "14px",
        border: "1px solid var(--border-light)",
        background: "var(--surface-2, var(--surface-3))",
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
        gap: "16px",
      }}
    >
      <div>
        <div style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", display: "flex", gap: "5px", alignItems: "center" }}>
          <Target size={12} /> Readiness (FCPS pass line {pct(data.pass_line)})
        </div>
        <div style={{ fontSize: "1.8rem", fontWeight: 800, color: predicted === null ? "var(--text-muted)" : predicted >= data.pass_line ? "var(--sea-green)" : "var(--text-primary)" }}>
          {predicted === null ? "—" : pct(predicted)}
        </div>
        <div style={{ position: "relative", height: "8px", borderRadius: "999px", background: "var(--surface-3)", marginTop: "4px" }}>
          <div style={{ width: `${(predicted ?? 0) * 100}%`, height: "100%", borderRadius: "999px", background: "var(--sky)" }} />
          <div title="75% pass line" style={{ position: "absolute", left: `${data.pass_line * 100}%`, top: "-3px", width: "2px", height: "14px", background: "var(--text-primary)" }} />
        </div>
        <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "6px" }}>
          {predicted === null ? "Answer 10+ questions in a subject to get an estimate." : data.note}
        </div>
      </div>

      <div>
        <div style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)" }}>By subject</div>
        {data.subjects.length === 0 ? (
          <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "6px" }}>No answers yet.</div>
        ) : (
          data.subjects.slice(0, 6).map((s) => (
            <div key={s.subject} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.76rem", marginTop: "5px" }} title={`${s.answered} answered`}>
              <span style={{ width: "92px", color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.subject}</span>
              <div style={{ flex: 1, height: "6px", borderRadius: "999px", background: "var(--surface-3)" }}>
                <div style={{ width: `${s.mastery * 100}%`, height: "100%", borderRadius: "999px", background: s.mastery >= data.pass_line ? "var(--sea-green)" : "var(--sky)", opacity: s.enough_data ? 1 : 0.4 }} />
              </div>
              <span style={{ width: "34px", textAlign: "right", color: "var(--text-muted)" }}>{pct(s.mastery)}</span>
            </div>
          ))
        )}
        {data.missing_part1_subjects.length ? (
          <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", marginTop: "6px" }}>
            Not practised yet: {data.missing_part1_subjects.join(", ")}
          </div>
        ) : null}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        <div style={{ fontSize: "0.82rem", color: "var(--text-primary)", display: "flex", alignItems: "center", gap: "6px" }}>
          <Flame size={15} style={{ color: "#f59e0b" }} /> <b>{data.streak.current}</b>-day streak
          <span style={{ color: "var(--text-muted)" }}>(best {data.streak.best})</span>
        </div>
        <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
          Concepts: <b>{data.concepts.mastered}</b> mastered of {data.concepts.tracked} tracked · <b>{data.concepts.due_now}</b> due now
        </div>
        {onOpenDailyDose ? (
          <button className="btn-workspace" onClick={onOpenDailyDose} style={{ alignSelf: "flex-start", padding: "5px 12px", fontSize: "0.78rem" }}>
            {data.streak.done_today ? "Daily Dose done ✓ — review again" : "Start today's Daily Dose"}
          </button>
        ) : null}
        <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "0.76rem", color: "var(--text-secondary)", flexWrap: "wrap" }}>
          <CalendarClock size={13} />
          <input
            type="date"
            value={dateInput}
            onChange={(e) => setDateInput(e.target.value)}
            aria-label="Exam date"
            style={{ background: "var(--surface-3)", border: "1px solid var(--border-light)", borderRadius: "6px", color: "var(--text-primary)", padding: "2px 6px", fontSize: "0.74rem" }}
          />
          <button className="btn-workspace" onClick={saveDate} style={{ padding: "2px 8px", fontSize: "0.72rem" }}>Save</button>
        </div>
        {data.days_left !== null ? (
          <div style={{ fontSize: "0.76rem", color: "var(--text-secondary)" }}>
            <b>{data.days_left}</b> days to the exam{data.daily_target ? <> · aim for <b>{data.daily_target}</b> questions a day</> : null}
          </div>
        ) : null}
        {data.sprint && (data.sprint.unlocked || data.sprint.preview) && onOpenSprint ? (
          <button className="btn-workspace" onClick={onOpenSprint} style={{ alignSelf: "flex-start", padding: "5px 12px", fontSize: "0.78rem", borderColor: "#f59e0b" }}>
            <Zap size={12} style={{ color: "#f59e0b" }} /> {data.sprint.unlocked ? "Final sprint is open" : "Preview the final sprint"}
          </button>
        ) : null}
      </div>

      {data.mistakes && data.mistakes.total_wrong > 0 ? (
        <div>
          <div style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", display: "flex", gap: "5px", alignItems: "center" }}>
            <AlertTriangle size={12} /> Your mistakes ({data.mistakes.window_days} days)
          </div>
          <div style={{ display: "flex", height: "10px", borderRadius: "999px", overflow: "hidden", margin: "8px 0 6px", background: "var(--surface-3)" }}>
            {(Object.keys(MISTAKE_LABEL) as (keyof typeof MISTAKE_LABEL)[]).map((t) => (
              <div key={t} title={`${MISTAKE_LABEL[t].label}: ${data.mistakes!.types[t].count}`}
                style={{ width: `${data.mistakes!.types[t].share * 100}%`, background: MISTAKE_LABEL[t].color }} />
            ))}
          </div>
          {(Object.keys(MISTAKE_LABEL) as (keyof typeof MISTAKE_LABEL)[]).map((t) => (
            <div key={t} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "0.76rem", marginTop: "3px" }}>
              <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: MISTAKE_LABEL[t].color }} />
              <span style={{ color: "var(--text-secondary)", flex: 1 }}>{MISTAKE_LABEL[t].label}</span>
              <b>{pct(data.mistakes!.types[t].share)}</b>
            </div>
          ))}
          {data.mistakes.confident_errors.length ? (
            <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "6px" }}>
              Confidently wrong on: {data.mistakes.confident_errors.slice(0, 3).map((c) => c.title).join("; ")}
            </div>
          ) : null}
          {onOpenLookalikes && data.mistakes.pairs.open + data.mistakes.pairs.cleared > 0 ? (
            <button className="btn-workspace" onClick={onOpenLookalikes} style={{ marginTop: "8px", padding: "4px 10px", fontSize: "0.74rem" }}>
              <GitCompareArrows size={12} /> {data.mistakes.pairs.open} look-alike pair{data.mistakes.pairs.open === 1 ? "" : "s"} to clear
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
