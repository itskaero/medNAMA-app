"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ArrowRight, BookOpen, Check, Loader2, RotateCcw, X } from "lucide-react";
import { api } from "~/lib/api";
import type { StageId } from "~/lib/types";

interface Section { book_title: string; chapter: string | null; page: number | null; text: string; questions: string[] }
interface Pack { case: { stem: string; question: string }; sections: Section[] }
interface Graded { correct: boolean; feedback: string; expected: string; quote: string; book_title: string; page: number | null; card_added: boolean }
interface Explained { score: number; points_hit: string[]; points_missed: string[]; errors: string[]; feedback: string; finished: boolean }

type Step = { kind: "case" } | { kind: "read"; s: number } | { kind: "recall"; s: number; q: number } | { kind: "explain"; s: number } | { kind: "done" };

/** Read it so it sticks: commit to a case first, read one short section, answer from memory without rereading,
 *  explain it in your own words, then the next section. Every miss becomes a review card. */
export default function ReadLoop({ stage, topicId, onFinished }: { stage: StageId; topicId: string; onFinished?: () => void }) {
  const [pack, setPack] = useState<Pack | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<Step>({ kind: "case" });
  const [text, setText] = useState("");
  const [confidence, setConfidence] = useState<"sure" | "unsure" | "guess">("sure");
  const [busy, setBusy] = useState(false);
  const [caseAnswer, setCaseAnswer] = useState<string | null>(null);
  const [graded, setGraded] = useState<Graded | null>(null);
  const [explained, setExplained] = useState<Explained | null>(null);
  const [tally, setTally] = useState({ right: 0, total: 0, cards: 0, explain: [] as number[] });

  const load = useCallback(() => {
    setError(null);
    api<Pack>(`/api/paeds/read?stage=${stage}&topic_id=${encodeURIComponent(topicId)}`).then(setPack).catch((e) => setError(e.message));
  }, [stage, topicId]);
  useEffect(() => { load(); }, [load]);

  const body = { stage, topic_id: topicId };
  const call = async <T,>(path: string, payload: object): Promise<T | null> => {
    setBusy(true);
    setError(null);
    try {
      return await api<T>(path, { method: "POST", body: JSON.stringify({ ...body, ...payload }) });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      return null;
    } finally {
      setBusy(false);
    }
  };
  const afterRead = (s: number): Step => (pack!.sections[s].questions.length ? { kind: "recall", s, q: 0 } : { kind: "explain", s });
  const next = (from: Step) => {
    setText(""); setGraded(null); setExplained(null); setConfidence("sure");
    if (from.kind === "recall") {
      const qs = pack!.sections[from.s].questions;
      setStep(from.q + 1 < qs.length ? { kind: "recall", s: from.s, q: from.q + 1 } : { kind: "explain", s: from.s });
    } else if (from.kind === "explain") {
      if (from.s + 1 < pack!.sections.length) setStep({ kind: "read", s: from.s + 1 });
      else { setStep({ kind: "done" }); onFinished?.(); }
    }
  };

  if (error && !pack) return <div className="pn-panel"><p className="tile-text">{error}</p><button type="button" className="btn-workspace" onClick={load}>Try again</button></div>;
  if (!pack) return <div className="pn-panel pn-wait"><Loader2 size={16} className="animate-spin" /> Preparing your reading session from the books. The first time takes up to a minute.</div>;

  const total = pack.sections.length;
  const sIdx = step.kind === "case" ? -1 : step.kind === "done" ? total : step.s;

  return (
    <div className="pn-loop">
      <ol className="pn-steps" aria-label="Progress">
        <li className={step.kind === "case" ? "on" : "done"}>Case</li>
        {pack.sections.map((_, i) => <li key={i} className={i === sIdx ? "on" : i < sIdx ? "done" : ""}>Section {i + 1}</li>)}
        <li className={step.kind === "done" ? "on" : ""}>Done</li>
      </ol>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}

      {step.kind === "case" ? (
        <section className="pn-panel">
          <span className="pn-kicker">Before you read · commit to an answer</span>
          <p className="pn-case">{pack.case.stem}</p>
          <h3 className="pn-q">{pack.case.question}</h3>
          {caseAnswer === null ? (
            <>
              <label className="pn-label" htmlFor="pn-case-a">Your answer</label>
              <textarea id="pn-case-a" className="pn-text" rows={3} value={text} onChange={(e) => setText(e.target.value)}
                placeholder="A guess is fine: reading with a question in mind is what makes it stick." />
              <button type="button" className="btn-primary" disabled={busy} onClick={async () => {
                const d = await call<{ answer: string }>("/api/paeds/read/case", { answer: text });
                if (d) setCaseAnswer(d.answer);
              }}>{busy ? "…" : "Commit and show the answer"}</button>
            </>
          ) : (
            <>
              <div className="pn-answer"><b>From the books:</b> {caseAnswer}</div>
              <button type="button" className="btn-primary" onClick={() => { setText(""); setStep({ kind: "read", s: 0 }); }}>
                Start reading <ArrowRight size={14} /></button>
            </>
          )}
        </section>
      ) : null}

      {step.kind === "read" ? (
        <section className="pn-panel">
          <span className="pn-kicker"><BookOpen size={13} /> Section {step.s + 1} of {total} · {pack.sections[step.s].book_title}
            {pack.sections[step.s].page ? `, p.${pack.sections[step.s].page}` : ""}</span>
          <div className="pn-reading">{pack.sections[step.s].text.split(/\n{2,}/).map((p, i) => <p key={i}>{p}</p>)}</div>
          <button type="button" className="btn-primary" onClick={() => setStep(afterRead(step.s))}>
            I&apos;ve read it: test me <ArrowRight size={14} /></button>
          <span className="pn-src">The text hides while you answer. No rereading: recalling is what makes it stay.</span>
        </section>
      ) : null}

      {step.kind === "recall" ? (
        <section className="pn-panel">
          <span className="pn-kicker">Section {step.s + 1} · question {step.q + 1} of {pack.sections[step.s].questions.length} · from memory</span>
          <h3 className="pn-q">{pack.sections[step.s].questions[step.q]}</h3>
          {!graded ? (
            <>
              <input className="field-input-new" value={text} onChange={(e) => setText(e.target.value)} aria-label="Your answer" />
              <div className="pn-row" role="radiogroup" aria-label="How sure are you">
                {(["sure", "unsure", "guess"] as const).map((c) => (
                  <button key={c} type="button" role="radio" aria-checked={confidence === c}
                    className={`practice-chip ${confidence === c ? "active" : ""}`} onClick={() => setConfidence(c)}>
                    {c === "sure" ? "Sure" : c === "unsure" ? "Not sure" : "Guessing"}
                  </button>
                ))}
              </div>
              <button type="button" className="btn-primary" disabled={busy} onClick={async () => {
                const d = await call<Graded>("/api/paeds/read/answer", { section: step.s, index: step.q, answer: text, confidence });
                if (d) {
                  setGraded(d);
                  setTally((t) => ({ ...t, right: t.right + (d.correct ? 1 : 0), total: t.total + 1, cards: t.cards + (d.card_added ? 1 : 0) }));
                }
              }}>{busy ? "Marking…" : "Check"}</button>
            </>
          ) : (
            <>
              <div className={`pn-verdict ${graded.correct ? "ok" : "bad"}`}>
                {graded.correct ? <Check size={16} /> : <X size={16} />}
                <span><b>{graded.correct ? "Right." : "Not quite."}</b> {graded.feedback}</span>
              </div>
              <div className="pn-answer"><b>Answer:</b> {graded.expected}
                <blockquote>“{graded.quote}” <cite>{graded.book_title}{graded.page ? `, p.${graded.page}` : ""}</cite></blockquote>
              </div>
              {graded.card_added ? <span className="pn-src"><RotateCcw size={11} /> Added to your reviews: it comes back before you forget it.</span> : null}
              <button type="button" className="btn-primary" onClick={() => next(step)}>Next <ArrowRight size={14} /></button>
            </>
          )}
        </section>
      ) : null}

      {step.kind === "explain" ? (
        <section className="pn-panel">
          <span className="pn-kicker">Section {step.s + 1} · explain it back</span>
          <h3 className="pn-q">Explain what this section said, in your own words, as you would to an examiner.</h3>
          {!explained ? (
            <>
              <textarea className="pn-text" rows={5} value={text} onChange={(e) => setText(e.target.value)} aria-label="Your explanation" />
              <button type="button" className="btn-primary" disabled={busy || text.trim().length < 20} onClick={async () => {
                const d = await call<Explained>("/api/paeds/read/explain", { section: step.s, text });
                if (d) { setExplained(d); setTally((t) => ({ ...t, explain: [...t.explain, d.score] })); }
              }}>{busy ? "Marking…" : "Mark my explanation"}</button>
            </>
          ) : (
            <>
              <div className="pn-score"><b>{explained.score}</b><span>/ 100</span>
                <div className="pn-bar" style={{ gridTemplateColumns: "1fr" }}><div><span style={{ width: `${explained.score}%` }} /></div></div></div>
              <p className="tile-text">{explained.feedback}</p>
              <div className="pn-facts">
                {explained.points_hit.length ? <div className="pn-fact"><h3>You covered</h3><ul>{explained.points_hit.map((x) => <li key={x}>{x}</li>)}</ul></div> : null}
                {explained.points_missed.length ? <div className="pn-fact"><h3>You missed</h3><ul>{explained.points_missed.map((x) => <li key={x}>{x}</li>)}</ul></div> : null}
                {explained.errors.length ? <div className="pn-fact"><h3>Not right</h3><ul>{explained.errors.map((x) => <li key={x}>{x}</li>)}</ul></div> : null}
              </div>
              <button type="button" className="btn-primary" onClick={() => next(step)}>
                {step.s + 1 < total ? "Next section" : "Finish"} <ArrowRight size={14} /></button>
            </>
          )}
        </section>
      ) : null}

      {step.kind === "done" ? (
        <section className="pn-panel">
          <span className="pn-kicker">Topic read</span>
          <div className="pn-stats" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
            <div className="pn-stat"><span className="lbl">Recall</span><b>{tally.right}/{tally.total}</b><small>answered from memory</small></div>
            <div className="pn-stat"><span className="lbl">Explained</span><b>{tally.explain.length ? Math.round(tally.explain.reduce((a, b) => a + b, 0) / tally.explain.length) : "—"}</b><small>average score</small></div>
            <div className="pn-stat"><span className="lbl">Reviews</span><b>{tally.cards}</b><small>cards added for what you missed</small></div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
