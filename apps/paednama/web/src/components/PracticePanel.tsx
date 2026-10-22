"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { api } from "~/lib/api";
import type { StageId } from "~/lib/types";
import { StudyQuestion, type StudyMCQ } from "@/components/StudyQuestion";

interface Practice { total: number; answered: number; correct: number; questions: (StudyMCQ & { grounding?: string | null })[] }

/** Practise a topic: its MCQs one at a time (answers, explanations and review cards are medNAMA's), and a new set of
 *  ten written from the Paediatrics books when the learner has done them all. */
export default function PracticePanel({ stage, topicId }: { stage: StageId; topicId: string }) {
  const [data, setData] = useState<Practice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queue, setQueue] = useState<StudyMCQ[]>([]);
  const [index, setIndex] = useState(0);
  const [session, setSession] = useState({ done: 0, right: 0 });
  const [writing, setWriting] = useState<string | null>(null);
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);

  const load = useCallback(() => {
    setError(null);
    api<Practice>(`/api/paeds/practice?stage=${stage}&topic_id=${encodeURIComponent(topicId)}`)
      .then((d) => {
        if (!live.current) return;
        setData(d);
        setQueue(d.questions.slice(0, d.total - d.answered));     // unanswered come first
        setIndex(0);
      })
      .catch((e) => live.current && setError(e.message));
  }, [stage, topicId]);
  useEffect(() => { load(); }, [load]);

  const writeSet = async () => {
    setError(null);
    setWriting("Choosing passages from the Paediatrics books…");
    try {
      const { job_id } = await api<{ job_id: string }>("/api/paeds/practice/start", {
        method: "POST", body: JSON.stringify({ stage, topic_id: topicId }),
      });
      const started = Date.now();
      for (;;) {
        await new Promise((r) => setTimeout(r, 3000));
        if (!live.current) return;
        const j = await api<{ status: string; detail?: string; added?: number }>(
          `/api/paeds/practice/job/${encodeURIComponent(job_id)}?stage=${stage}&topic_id=${encodeURIComponent(topicId)}`);
        if (j.status === "done") break;
        if (j.status === "failed") throw new Error(j.detail || "The questions could not be written. Try again.");
        const s = Math.round((Date.now() - started) / 1000);
        setWriting(`Writing ten questions from the books… ${s}s (usually about a minute)`);
      }
      setSession({ done: 0, right: 0 });
      load();
    } catch (e) {
      if (live.current) setError((e as Error).message);
    } finally {
      if (live.current) setWriting(null);
    }
  };

  if (!data) return <section className="pn-panel">{error ? <p className="tile-text">{error}</p> :
    <p className="pn-wait"><Loader2 size={16} className="animate-spin" /> Loading…</p>}</section>;

  const current = queue[index];
  const stats = data.total
    ? `${data.answered} of ${data.total} answered · ${data.correct} right first time`
    : "No questions on this topic yet.";

  return (
    <section className="pn-panel">
      <div className="pn-panel-head">
        <div>
          <div className="pn-kicker">Single best answer · written from the Paediatrics books</div>
          <p className="tile-text" style={{ margin: "4px 0 0" }}>{stats}{session.done ? ` · this sitting ${session.right}/${session.done}` : ""}</p>
        </div>
      </div>

      {writing ? (
        <p className="pn-wait"><Loader2 size={16} className="animate-spin" /> {writing}</p>
      ) : current ? (
        <StudyQuestion
          key={current.id}
          mcq={current}
          token={null}
          onFigureClick={() => {}}
          onAnswered={(r) => setSession((s) => ({ done: s.done + 1, right: s.right + (r.is_correct ? 1 : 0) }))}
          onNext={() => (index + 1 < queue.length ? setIndex(index + 1) : load())}
          nextLabel={index + 1 < queue.length ? "Next question" : "Finish"}
          keys
        />
      ) : (
        <div className="pn-practice-empty">
          <p className="tile-text">{data.total ? "You've answered every question on this topic." : "Be the first to practise this topic."}
            {" "}A new set of ten is written from the Paediatrics books, pitched at the exam, and kept for everyone.</p>
          <button type="button" className="btn-primary" onClick={writeSet}><Sparkles size={15} /> Write 10 questions</button>
        </div>
      )}
      {error ? <p className="tile-text" role="alert">{error}</p> : null}
      <p className="pn-fine">Questions are written by AI from the cited books and can be wrong: use Report on any you doubt. Misses come back as review cards.</p>
    </section>
  );
}
