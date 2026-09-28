"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { API } from "@/lib/constants";
import { Figure } from "@/types";
import { parseMarkdown } from "@/utils/markdown";
import { ConceptCard, ConceptCardData } from "@/components/ConceptCard";
import { ExplainOnDemand } from "@/components/ExplainOnDemand";
import { QuestionMedia } from "@/components/QuestionMedia";

export type Confidence = "sure" | "unsure" | "guess";
export type MistakeType = "confusion" | "misconception" | "gap";

export interface StudyMCQ {
  id: number;
  question_text: string;
  options: Record<string, string>;
  correct_option: string;
  explanation_markdown: string | null;
  sub_category: string | null;
  figure_id: number | null;
  media?: number[];
}

export interface AnswerResult {
  is_correct: boolean;
  correct_option: string;
  explanation_markdown: string | null;
  concept_status: string;
  concept: ConceptCardData | null;
  mistake_type?: MistakeType | null;
}

const CONFIDENCE_LABEL: Record<Confidence, string> = { sure: "Sure", unsure: "Unsure", guess: "Guess" };

export const MISTAKE_NOTE: Record<MistakeType, { label: string; text: string; color: string }> = {
  confusion: {
    label: "Look-alike mix-up",
    text: "You picked something that resembles the answer. A side-by-side comparison is being written; it will show up under Look-alikes and in your next Daily Dose.",
    color: "#d97706",
  },
  misconception: {
    label: "Confident, but wrong",
    text: "The most important kind of mistake to fix before the exam. This concept comes back tomorrow with a new question.",
    color: "var(--error)",
  },
  gap: {
    label: "Not known yet",
    text: "Now on your review schedule: it comes back as a new question in a day.",
    color: "var(--text-secondary)",
  },
};

/** One question with the confidence tap, the explanation, the concept card and the mistake type. */
export function StudyQuestion({
  mcq,
  token,
  onFigureClick,
  answerBody,
  onAnswered,
  onNext,
  nextLabel = "Next",
  afterResult,
}: {
  mcq: StudyMCQ;
  token: string | null;
  onFigureClick: (f: Figure) => void;
  answerBody?: Record<string, unknown>;
  onAnswered?: (r: AnswerResult) => void;
  onNext?: () => void;
  nextLabel?: string;
  afterResult?: React.ReactNode;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [concept, setConcept] = useState<ConceptCardData | null>(null);
  const [conceptPending, setConceptPending] = useState(false);
  // Incremented to cancel an in-flight concept poll (unmount).
  const pollGeneration = useRef(0);

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  useEffect(() => {
    const gen = pollGeneration;
    return () => {
      gen.current += 1;
    };
  }, []);

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

  const submit = async (confidence: Confidence) => {
    if (!selected || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${API}/api/study/answer`, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ mcq_id: mcq.id, selected_option: selected, confidence, ...(answerBody || {}) }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body: AnswerResult = await res.json();
      setResult(body);
      onAnswered?.(body);
      if (body.concept) setConcept(body.concept);
      else if (!body.is_correct || confidence === "guess") {
        setConceptPending(true);
        pollConcept(mcq.id);
      }
    } catch (e) {
      setError(`Could not save your answer (${e instanceof Error ? e.message : String(e)}).`);
    } finally {
      setSubmitting(false);
    }
  };

  const note = result && !result.is_correct && result.mistake_type ? MISTAKE_NOTE[result.mistake_type] : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      {mcq.figure_id ? (
        <button
          type="button"
          onClick={() => onFigureClick({ id: mcq.figure_id!, figure_label: "Textbook figure" })}
          style={{ border: "1px solid var(--border-light)", borderRadius: "12px", padding: 0, background: "#0d0f13", cursor: "zoom-in", overflow: "hidden" }}
          aria-label="Enlarge figure"
        >
          <img
            src={`${API}/api/figures/${mcq.figure_id}`}
            alt="Identify what this textbook figure shows"
            style={{ width: "100%", maxHeight: "360px", objectFit: "contain", display: "block" }}
          />
        </button>
      ) : null}
      <p style={{ fontSize: "1rem", lineHeight: 1.6, color: "var(--text-primary)", fontWeight: 500, margin: 0, whiteSpace: "pre-line" }}>{mcq.question_text}</p>
      <QuestionMedia ids={mcq.media} token={token} />
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {Object.keys(mcq.options).sort().map((key) => {
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
              <span style={{ lineHeight: 1.4 }}>{mcq.options[key]}</span>
            </button>
          );
        })}
      </div>

      {error ? <p style={{ color: "var(--error)", margin: 0, fontSize: "0.82rem" }}>{error}</p> : null}

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
              onClick={() => submit(c)}
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
          {note ? (
            <div style={{ fontSize: "0.8rem", lineHeight: 1.5, padding: "8px 10px", borderRadius: "10px", border: "1px solid var(--border-light)", background: "var(--surface-2)" }}>
              <strong style={{ color: note.color }}>{note.label}.</strong> <span style={{ color: "var(--text-secondary)" }}>{note.text}</span>
            </div>
          ) : null}
          {result.explanation_markdown ? (
            <div className="prose" style={{ fontSize: "0.86rem" }} dangerouslySetInnerHTML={{ __html: parseMarkdown(result.explanation_markdown) }} />
          ) : (
            <ExplainOnDemand mcqId={mcq.id} token={token} />
          )}
          {afterResult}
          {concept ? (
            <ConceptCard card={concept} token={token} onFigureClick={onFigureClick} />
          ) : conceptPending ? (
            <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", display: "flex", gap: "6px", alignItems: "center" }}>
              <Loader2 size={13} className="animate-spin" /> Writing your concept card from the textbooks… it will come back as a new question in a day.
            </div>
          ) : null}
          {onNext ? (
            <button className="btn-workspace" onClick={onNext} style={{ alignSelf: "flex-end" }}>
              {nextLabel}
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}
