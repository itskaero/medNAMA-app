"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Flame, Image as ImageIcon, Loader2, RotateCcw, Sparkles, Snowflake, TrendingUp, XCircle } from "lucide-react";
import { API } from "@/lib/constants";
import { Figure } from "@/types";
import { parseMarkdown } from "@/utils/markdown";
import { ConceptCard, ConceptCardData } from "@/components/ConceptCard";

type Confidence = "sure" | "unsure" | "guess";

interface DoseMCQ {
  id: number;
  question_text: string;
  options: Record<string, string>;
  correct_option: string;
  explanation_markdown: string | null;
  sub_category: string | null;
  figure_id: number | null;
}

interface DoseItem {
  index: number;
  type: "review" | "new" | "image" | "pearl";
  done: boolean;
  correct?: boolean;
  high_yield?: boolean;
  times_asked?: number;
  mcq?: DoseMCQ | null;
  concept?: ConceptCardData | null;
}

interface DoseData {
  day: string;
  items: DoseItem[];
  completed: boolean;
  streak: { current: number; best: number; done_today: boolean };
  streak_freezes: number;
}

interface AnswerResult {
  is_correct: boolean;
  correct_option: string;
  explanation_markdown: string | null;
  concept_status: string;
  concept: ConceptCardData | null;
}

const TYPE_LABEL: Record<DoseItem["type"], string> = {
  review: "Re-test: a concept you missed",
  new: "New question",
  image: "Spot the diagnosis",
  pearl: "Pearl of the day",
};

const CONFIDENCE_LABEL: Record<Confidence, string> = { sure: "Sure", unsure: "Unsure", guess: "Guess" };

export default function DailyDoseView({ token, onFigureClick }: { token: string | null; onFigureClick: (f: Figure) => void }) {
  const [dose, setDose] = useState<DoseData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [current, setCurrent] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [concept, setConcept] = useState<ConceptCardData | null>(null);
  const [conceptPending, setConceptPending] = useState(false);
  // Incremented to cancel an in-flight concept poll (next item / unmount).
  const pollGeneration = useRef(0);

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
    fetch(`${API}/api/study/daily`, { headers: headers(), credentials: "include" })
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
        if (!cancelled) setError(`Could not load today's Daily Dose (${e instanceof Error ? e.message : String(e)}).`);
      });
    return () => {
      cancelled = true;
    };
  }, [headers, refreshKey]);

  useEffect(() => {
    const gen = pollGeneration;
    return () => {
      gen.current += 1; // stop any poll when leaving the view
    };
  }, []);

  const resetItemState = () => {
    setSelected(null);
    setResult(null);
    setConcept(null);
    setConceptPending(false);
    pollGeneration.current += 1;
  };

  // The concept card is written in the background; poll until it is ready.
  const pollConcept = async (mcqId: number) => {
    const myGeneration = ++pollGeneration.current;
    for (let attempt = 0; attempt < 40; attempt++) {
      await new Promise((r) => setTimeout(r, attempt === 0 ? 1500 : 3000));
      if (pollGeneration.current !== myGeneration) return;
      try {
        const res = await fetch(`${API}/api/concepts/by-mcq/${mcqId}`, { headers: headers(), credentials: "include" });
        if (res.status === 200) {
          const body = await res.json();
          if (pollGeneration.current !== myGeneration) return;
          setConcept(body.concept);
          setConceptPending(false);
          return;
        }
      } catch {
        /* retry */
      }
    }
    if (pollGeneration.current === myGeneration) setConceptPending(false);
  };

  const submit = async (item: DoseItem, confidence: Confidence) => {
    if (!item.mcq || !selected || submitting) return;
    setSubmitting(true);
    try {
      const res = await fetch(`${API}/api/study/answer`, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ mcq_id: item.mcq.id, selected_option: selected, confidence, dose_index: item.index }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body: AnswerResult = await res.json();
      setResult(body);
      setDose((d) =>
        d ? { ...d, items: d.items.map((i) => (i.index === item.index ? { ...i, done: true, correct: body.is_correct } : i)) } : d
      );
      const needsConcept = !body.is_correct || confidence === "guess";
      if (body.concept) setConcept(body.concept);
      else if (needsConcept) {
        setConceptPending(true);
        pollConcept(item.mcq.id);
      }
    } catch (e) {
      setError(`Could not save your answer (${e instanceof Error ? e.message : String(e)}).`);
    } finally {
      setSubmitting(false);
    }
  };

  const finishPearl = async (item: DoseItem) => {
    await fetch(`${API}/api/study/dose/${item.index}/done`, { method: "POST", headers: headers(), credentials: "include" });
    setDose((d) => (d ? { ...d, items: d.items.map((i) => (i.index === item.index ? { ...i, done: true } : i)) } : d));
    next();
  };

  const next = () => {
    resetItemState();
    setCurrent((c) => c + 1);
    if (dose && current + 1 >= dose.items.length) load(); // refresh streak on completion
  };

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
        <Loader2 size={16} className="animate-spin" /> Preparing today&apos;s Daily Dose…
      </div>
    );
  }

  const total = dose.items.length;
  const doneCount = dose.items.filter((i) => i.done).length;
  const item = dose.items[current];

  return (
    <div className="dashboard-view" role="region" aria-label="Daily Dose" style={{ maxWidth: "760px", margin: "0 auto" }}>
      <div className="dashboard-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
        <div>
          <h1 className="dashboard-title">Daily Dose</h1>
          <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--text-secondary)" }}>
            About 10 minutes: re-test what you missed, a few new questions, one image and one pearl.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", fontSize: "0.82rem" }}>
          <span title="Days in a row you completed your Daily Dose" style={{ display: "flex", alignItems: "center", gap: "4px", color: "var(--text-primary)", fontWeight: 700 }}>
            <Flame size={16} style={{ color: "#f59e0b" }} /> {dose.streak.current} day{dose.streak.current === 1 ? "" : "s"}
          </span>
          <span title="Streak freezes: a missed day is covered automatically" style={{ display: "flex", alignItems: "center", gap: "4px", color: "var(--text-muted)" }}>
            <Snowflake size={14} /> {dose.streak_freezes}
          </span>
        </div>
      </div>

      <div style={{ height: "6px", borderRadius: "999px", background: "var(--surface-3)", margin: "0 0 var(--sp-4)" }}>
        <div style={{ width: `${total ? (doneCount / total) * 100 : 0}%`, height: "100%", borderRadius: "999px", background: "var(--sky)", transition: "width 0.3s" }} />
      </div>

      {total === 0 ? (
        <p style={{ color: "var(--text-secondary)" }}>Nothing to study yet: generate or practise a few MCQs first, then come back.</p>
      ) : !item ? (
        <div style={{ textAlign: "center", padding: "var(--sp-6)", border: "1px solid var(--border-light)", borderRadius: "14px" }}>
          <CheckCircle2 size={36} style={{ color: "var(--sea-green)" }} />
          <h2 style={{ margin: "10px 0 4px" }}>Daily Dose complete</h2>
          <p style={{ color: "var(--text-secondary)", margin: 0 }}>
            <Flame size={14} style={{ color: "#f59e0b" }} /> Streak: {dose.streak.current} day{dose.streak.current === 1 ? "" : "s"} (best {dose.streak.best}).
            Concepts you missed today will come back as new questions in a day.
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
          <div style={{ fontSize: "0.7rem", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-muted)", display: "flex", alignItems: "center", gap: "6px" }}>
            {item.type === "image" ? <ImageIcon size={12} /> : item.type === "pearl" ? <Sparkles size={12} /> : null}
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

          {item.type === "pearl" && item.concept ? (
            <>
              <ConceptCard card={item.concept} token={token} onFigureClick={onFigureClick} heading="Pearl of the day" />
              <button className="btn-workspace" onClick={() => finishPearl(item)} style={{ alignSelf: "flex-end" }}>
                Got it
              </button>
            </>
          ) : item.mcq ? (
            <>
              {item.mcq.figure_id ? (
                <button
                  type="button"
                  onClick={() => onFigureClick({ id: item.mcq!.figure_id!, figure_label: "Textbook figure" })}
                  style={{ border: "1px solid var(--border-light)", borderRadius: "12px", padding: 0, background: "#0d0f13", cursor: "zoom-in", overflow: "hidden" }}
                  aria-label="Enlarge figure"
                >
                  <img
                    src={`${API}/api/figures/${item.mcq.figure_id}?token=${token ?? ""}`}
                    alt="Identify what this textbook figure shows"
                    style={{ width: "100%", maxHeight: "360px", objectFit: "contain", display: "block" }}
                  />
                </button>
              ) : null}
              <p style={{ fontSize: "1rem", lineHeight: 1.6, color: "var(--text-primary)", fontWeight: 500, margin: 0 }}>{item.mcq.question_text}</p>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {Object.keys(item.mcq.options).sort().map((key) => {
                  const isCorrect = result && key === result.correct_option;
                  const isWrongPick = result && key === selected && !result.is_correct;
                  return (
                    <button
                      key={key}
                      type="button"
                      disabled={!!result}
                      onClick={() => setSelected(key)}
                      style={{
                        display: "flex", gap: "10px", alignItems: "flex-start", textAlign: "left", padding: "10px 12px",
                        borderRadius: "10px", cursor: result ? "default" : "pointer",
                        border: `1px solid ${isCorrect ? "var(--sea-green)" : isWrongPick ? "var(--error)" : selected === key ? "var(--sky)" : "var(--border-light)"}`,
                        background: isCorrect ? "rgba(92,148,110,0.12)" : isWrongPick ? "rgba(248,113,113,0.1)" : selected === key ? "rgba(48,197,255,0.08)" : "var(--surface-3)",
                        color: "var(--text-primary)",
                      }}
                    >
                      <span className="option-badge">{key}</span>
                      <span style={{ lineHeight: 1.4 }}>{item.mcq!.options[key]}</span>
                    </button>
                  );
                })}
              </div>

              {!result ? (
                <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                    {selected ? "How sure are you?" : "Pick an answer, then tell us how sure you are."}
                  </span>
                  {(Object.keys(CONFIDENCE_LABEL) as Confidence[]).map((c) => (
                    <button
                      key={c}
                      type="button"
                      className="btn-workspace"
                      disabled={!selected || submitting}
                      onClick={() => submit(item, c)}
                      title={c === "guess" ? "A lucky guess is treated as not known yet, so it comes back for review" : undefined}
                      style={{ padding: "5px 12px", fontSize: "0.78rem" }}
                    >
                      {CONFIDENCE_LABEL[c]}
                    </button>
                  ))}
                  {submitting ? <Loader2 size={14} className="animate-spin" /> : null}
                </div>
              ) : (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px", fontWeight: 700, color: result.is_correct ? "var(--sea-green)" : "var(--error)" }}>
                    {result.is_correct ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
                    {result.is_correct ? "Correct" : `Not quite: the answer is ${result.correct_option}`}
                  </div>
                  {result.explanation_markdown ? (
                    <div className="prose" style={{ fontSize: "0.86rem" }} dangerouslySetInnerHTML={{ __html: parseMarkdown(result.explanation_markdown) }} />
                  ) : null}
                  {concept ? (
                    <ConceptCard card={concept} token={token} onFigureClick={onFigureClick} />
                  ) : conceptPending ? (
                    <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", display: "flex", gap: "6px", alignItems: "center" }}>
                      <Loader2 size={13} className="animate-spin" /> Writing your concept card from the textbooks… it will come back as a new question in a day.
                    </div>
                  ) : null}
                  <button className="btn-workspace" onClick={next} style={{ alignSelf: "flex-end" }}>
                    {current + 1 >= total ? "Finish" : "Next"}
                  </button>
                </>
              )}
            </>
          ) : (
            <button className="btn-workspace" onClick={next}>Skip (item unavailable)</button>
          )}
        </div>
      )}
    </div>
  );
}
