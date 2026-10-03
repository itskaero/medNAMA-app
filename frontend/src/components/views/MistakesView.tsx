"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, BookOpenCheck, GitCompareArrows, GraduationCap, Lightbulb, Loader2, Stethoscope } from "lucide-react";
import { ConceptCard, ConceptCardData } from "@/components/ConceptCard";
import type { Figure } from "@/types";
import { API } from "@/lib/constants";
import PageShell from "@/components/layout/PageShell";
import { askAboutQuestion, type ViewId } from "@/lib/nav";

interface Miss {
  id: number; question_text: string; options: Record<string, string>; correct_option: string; selected: string | null;
  source: string; at: string | null; subject: string; topic: string | null;
}
interface Data {
  items: Miss[]; total: number; subjects: { subject: string; count: number }[];
  types?: { total_wrong: number; types: Record<string, { count: number; share: number }>; pairs: { open: number } };
}
const TYPE_LABEL: Record<string, string> = { confusion: "Look-alike mix-ups", misconception: "Confident but wrong", gap: "Not known yet" };
const SOURCE: Record<string, string> = { quiz: "Practice", dose: "Daily Dose", retest: "Re-test", mock: "Mock", duel: "Challenge", sprint: "Sprint", offline: "Offline" };

/** Review > Mistakes: every question still missed (its latest answer was wrong), from every feature, in one list.
 *  From here: practise them again, ask Dr MedNama, revise the topic, or open the look-alike pairs. */
export default function MistakesView({ getHeaders, token, onFigureClick, onPractise, onAsk, onRevise, onNavigate }: {
  token?: string | null;
  onFigureClick?: (f: Figure) => void;
  getHeaders: () => HeadersInit;
  onPractise: (filters: Record<string, unknown>, label: string) => void;
  onAsk: (question: string) => void;
  onRevise: (topic: string) => void;
  onNavigate: (v: ViewId) => void;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [subject, setSubject] = useState<string | null>(null);
  // The concept card written for a missed question (open: the mcq id; null card = none yet)
  const [cards, setCards] = useState<Record<number, ConceptCardData | null | "loading">>({});
  const toggleCard = async (id: number) => {
    if (id in cards) { setCards((c) => { const n = { ...c }; delete n[id]; return n; }); return; }
    setCards((c) => ({ ...c, [id]: "loading" }));
    const r = await fetch(`${API}/api/concepts/by-mcq/${id}`, { headers: getHeaders(), credentials: "include" }).catch(() => null);
    const body = r && r.status === 200 ? await r.json().catch(() => null) : null;
    setCards((c) => ({ ...c, [id]: body?.concept ?? null }));
  };
  const load = useCallback(async () => {
    setError(null);
    try {
      const r = await fetch(`${API}/api/review/mistakes`, { headers: getHeaders(), credentials: "include" });
      if (!r.ok) throw new Error(`Couldn't load your mistakes (${r.status}).`);
      setData(await r.json());
    } catch (e) { setError(e instanceof Error ? e.message : "Couldn't load your mistakes."); }
  }, [getHeaders]);
  useEffect(() => { load(); }, [load]);

  const shown = useMemo(() => (data?.items || []).filter((m) => !subject || m.subject === subject), [data, subject]);
  const practise = () => onPractise({ mcq_ids: shown.slice(0, 50).map((m) => m.id), num_questions: Math.min(50, shown.length), prefer_unseen: false },
    subject ? `Missed · ${subject}` : "Missed questions");
  const types = data?.types;

  return (
    <PageShell title="Mistakes" icon={<AlertTriangle size={22} />} width="default"
      subtitle="Every question you still have wrong, from practice, Daily Dose, mocks and challenges. Get one right and it leaves this list."
      loading={!data && !error} error={error} onRetry={load}
      empty={data && data.total === 0 ? "Nothing missed right now. Questions you get wrong will appear here." : undefined}
      actions={shown.length ? <button className="btn-primary" onClick={practise} style={{ padding: "8px 16px" }}>
        <GraduationCap size={14} />&nbsp;Practise {Math.min(50, shown.length)} again</button> : null}>
      {types && types.total_wrong ? (
        <div className="next-actions">
          <span className="lbl">Last 60 days</span>
          {Object.entries(types.types).map(([k, v]) => (
            <span key={k} className="practice-chip" title={TYPE_LABEL[k]}>{TYPE_LABEL[k] || k}: <b>{v.count}</b></span>
          ))}
          {types.pairs?.open ? (
            <button className="btn-workspace" onClick={() => onNavigate("lookalikes")}><GitCompareArrows size={12} /> {types.pairs.open} look-alike pairs to clear</button>
          ) : null}
        </div>
      ) : null}
      {data?.subjects.length ? (
        <div className="next-actions">
          <button className={`practice-chip ${subject === null ? "active" : ""}`} onClick={() => setSubject(null)}>All · {data.total}</button>
          {data.subjects.map((s) => (
            <button key={s.subject} className={`practice-chip ${subject === s.subject ? "active" : ""}`} onClick={() => setSubject(s.subject)}>
              {s.subject} · {s.count}
            </button>
          ))}
        </div>
      ) : null}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {shown.slice(0, 150).map((m) => (
          <article key={m.id} className="tile" style={{ gap: 6 }}>
            <span className="tile-kicker">{m.subject}{m.topic ? ` · ${m.topic}` : ""} · {SOURCE[m.source] || m.source}{m.at ? ` · ${new Date(m.at).toLocaleDateString()}` : ""}</span>
            <span style={{ fontSize: "0.92rem", lineHeight: 1.5 }}>{m.question_text}</span>
            <span className="tile-text">
              {m.selected ? <>You chose <b style={{ color: "var(--error, #e05555)" }}>{m.selected}. {m.options?.[m.selected]}</b> · </> : null}
              Answer <b style={{ color: "var(--success, #2fb36a)" }}>{m.correct_option}. {m.options?.[m.correct_option]}</b>
            </span>
            <span className="next-actions">
              <button className="btn-workspace" onClick={() => onAsk(askAboutQuestion(m))}>
                <Stethoscope size={12} /> Ask Dr MedNama</button>
              <button className="btn-workspace" onClick={() => onRevise(m.topic || m.subject)}><BookOpenCheck size={12} /> Revise {m.topic || m.subject}</button>
              <button className="btn-workspace" onClick={() => toggleCard(m.id)}>
                {cards[m.id] === "loading" ? <Loader2 size={12} className="animate-spin" /> : <Lightbulb size={12} />} {m.id in cards ? "Hide concept" : "Concept card"}</button>
            </span>
            {m.id in cards && cards[m.id] !== "loading" ? (
              cards[m.id] ? <ConceptCard card={cards[m.id] as ConceptCardData} token={token ?? null} onFigureClick={onFigureClick} />
                : <span className="tile-text">No concept card for this question yet: one is written when you miss it in practice or the Daily Dose.</span>
            ) : null}
          </article>
        ))}
        {shown.length > 150 ? <div className="tile-text">Showing the latest 150 of {shown.length}.</div> : null}
      </div>
    </PageShell>
  );
}
