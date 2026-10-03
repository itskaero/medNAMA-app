"use client";

import React, { useEffect } from "react";
import { Check, X } from "lucide-react";
import { API } from "@/lib/constants";
import { Figure } from "@/types";
import { QuestionMedia } from "@/components/QuestionMedia";

export interface PlayerMCQ {
  id: number;
  question_text: string;
  options: Record<string, string>;
  media?: number[];
  figure_id?: number | null;
}

export type Confidence = "sure" | "unsure" | "guess";
export const CONFIDENCE_LABEL: Record<Confidence, string> = { sure: "Sure", unsure: "Unsure", guess: "Guess" };

/**
 * One question, drawn the same way in every session: practice, Daily Dose, look-alikes, twists, mocks, timed
 * papers, challenges and offline packs. It shows the figure, stem, media and options and takes the choice
 * (click, A–E or 1–5). The session decides what happens around it:
 *  - `correct` set → the key is revealed (right option green, a wrong pick red);
 *  - `correct` unset → only the pick is shown (board/exam mode, where keys come at the end);
 *  - `locked` → no more changes (answered, or the paper was submitted).
 * Feedback, explanation and next buttons go in `children`, under the options.
 */
export default function QuestionPlayer({
  mcq, token, selected, onSelect, correct, locked = false, kicker, aside, onFigureClick, keys = true, animate = false, children,
}: {
  mcq: PlayerMCQ;
  token: string | null;
  selected: string | null | undefined;
  onSelect: (key: string) => void;
  correct?: string | null;
  locked?: boolean;
  kicker?: React.ReactNode;
  aside?: React.ReactNode;
  onFigureClick?: (f: Figure) => void;
  /** Listen for A–E / 1–5. Off where the session already has its own shortcuts. */
  keys?: boolean;
  animate?: boolean;
  children?: React.ReactNode;
}) {
  const optionKeys = Object.keys(mcq.options).sort();
  const revealed = !!correct;

  useEffect(() => {
    if (!keys || locked) return;
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || (el as HTMLElement).isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toUpperCase();
      if (optionKeys.includes(k)) onSelect(k);
      else if (/^[1-5]$/.test(k) && optionKeys[Number(k) - 1]) onSelect(optionKeys[Number(k) - 1]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keys, locked, onSelect, optionKeys.join("")]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="qp">
      {kicker || aside ? (
        <div className="qp-head">
          <span className="qp-kicker">{kicker}</span>
          {aside}
        </div>
      ) : null}
      {mcq.figure_id ? (
        <button type="button" className="qp-figure" aria-label="Enlarge figure"
          onClick={() => onFigureClick?.({ id: mcq.figure_id!, figure_label: "Textbook figure" })}>
          <img src={`${API}/api/figures/${mcq.figure_id}`} alt="Identify what this textbook figure shows" />
        </button>
      ) : null}
      <p className="qp-stem">{mcq.question_text}</p>
      <QuestionMedia ids={mcq.media} token={token} />
      <div className="quiz-options-list" role="radiogroup">
        {optionKeys.map((key, i) => {
          const isPick = key === selected;
          const isKey = revealed && key === correct;
          let cls = "option-button";
          if (animate) cls += " option-cascade-item";
          if (revealed) {
            if (isKey) cls += " correct" + (animate && isPick ? " pop-correct" : "");
            else if (isPick) cls += " selected-wrong" + (animate ? " shake-incorrect" : "");
          } else if (isPick) cls += " selected-board-mode";
          return (
            <button key={`${mcq.id}-${key}`} type="button" className={cls} role="radio" aria-checked={isPick}
              disabled={locked} onClick={() => onSelect(key)} style={animate ? { animationDelay: `${i * 50}ms` } : undefined}>
              <span className="option-badge">{key}</span>
              <span className="qp-option-text">{mcq.options[key]}</span>
              {revealed && isKey ? <Check size={14} className="qp-mark" style={{ color: "var(--success)" }} /> : null}
              {revealed && isPick && !isKey ? <X size={14} className="qp-mark" style={{ color: "var(--error)" }} /> : null}
            </button>
          );
        })}
      </div>
      {children}
    </div>
  );
}

/** The confidence row. `onPick` sets it before answering (practice); `onSubmit` makes each a submit button (Dose). */
export function ConfidenceRow({ value, onPick, onSubmit, disabled, label }: {
  value?: Confidence;
  onPick?: (c: Confidence) => void;
  onSubmit?: (c: Confidence) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <div className="qp-confidence">
      <span className="lbl">{label ?? "How sure are you?"}</span>
      {(Object.keys(CONFIDENCE_LABEL) as Confidence[]).map((c) => (
        <button key={c} type="button" disabled={disabled}
          className={onSubmit ? "btn-workspace" : `qp-pill ${value === c ? "active" : ""}`}
          onClick={() => (onSubmit ? onSubmit(c) : onPick?.(c))}
          title={c === "guess" ? "A lucky guess counts as not known yet, so it comes back for review" : undefined}>
          {CONFIDENCE_LABEL[c]}
        </button>
      ))}
    </div>
  );
}

/** Exam-mode navigator: a numbered square per question, filled when answered, a dot when flagged. */
export function QuestionNavigator({ count, current, answered, flagged, onJump }: {
  count: number;
  current: number;
  answered: (i: number) => boolean;
  flagged?: (i: number) => boolean;
  onJump: (i: number) => void;
}) {
  return (
    <div className="qp-nav" role="list">
      {Array.from({ length: count }, (_, i) => (
        <button key={i} type="button" role="listitem" aria-label={`Question ${i + 1}`} onClick={() => onJump(i)}
          className={`qp-nav-cell ${i === current ? "current" : ""} ${answered(i) ? "answered" : ""}`}>
          {i + 1}
          {flagged?.(i) ? <span className="qp-nav-flag" /> : null}
        </button>
      ))}
    </div>
  );
}
