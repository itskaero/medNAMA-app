"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ArrowLeft, BookOpen, BookOpenCheck, ListChecks, Loader2, MessageCircleHeart } from "lucide-react";
import { api } from "~/lib/api";
import { renderSheet, type Citation } from "~/lib/md";
import type { StageId, Topic } from "~/lib/types";
import ReadLoop from "~/components/ReadLoop";
import ShifaChat from "~/components/ShifaChat";
import PracticePanel from "~/components/PracticePanel";

interface Lens { id: string; label: string; role?: string; ready: boolean }
interface TopicInfo {
  system: string;
  system_page: number | null;
  topic: Topic;
  lenses: Lens[];
  reading: { done: boolean; sections_done: number[]; recall: [number, number]; cards_added: number } | null;
}
const title = (s: string) => s.charAt(0) + s.slice(1).toLowerCase();

/** One syllabus topic: a summary from all the books (or one book's own account), and the reading loop. */
export default function TopicView({ stage, topicId, onBack }: { stage: StageId; topicId: string; onBack: () => void }) {
  const [info, setInfo] = useState<TopicInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"sheet" | "read" | "practise" | "ask">("sheet");
  const [lens, setLens] = useState("all");
  const [sheet, setSheet] = useState<{ markdown: string; citations: Citation[] } | null>(null);
  const [sheetError, setSheetError] = useState<string | null>(null);
  const [writing, setWriting] = useState(false);
  const [theme, setTheme] = useState<"paper" | "night">("paper");
  useEffect(() => {
    try { if (localStorage.getItem("pn-page-theme") === "night") setTheme("night"); } catch { /* private mode */ }
  }, []);
  const pickTheme = (t: "paper" | "night") => {
    setTheme(t);
    try { localStorage.setItem("pn-page-theme", t); } catch { /* private mode */ }
  };

  const loadInfo = useCallback(() => {
    api<TopicInfo>(`/api/paeds/topic?stage=${stage}&topic_id=${encodeURIComponent(topicId)}`).then(setInfo).catch((e) => setError(e.message));
  }, [stage, topicId]);
  useEffect(() => { loadInfo(); }, [loadInfo]);

  useEffect(() => {
    if (tab !== "sheet") return;
    let live = true;
    setSheet(null);
    setSheetError(null);
    setWriting(true);
    api<{ markdown: string; citations: Citation[] }>(`/api/paeds/topic/sheet?stage=${stage}&topic_id=${encodeURIComponent(topicId)}&lens=${lens}`)
      .then((d) => { if (live) setSheet(d); })
      .catch((e) => { if (live) setSheetError(e.message); })
      .finally(() => { if (live) setWriting(false); });
    return () => { live = false; };
  }, [stage, topicId, lens, tab]);

  if (error) return <div className="page page-default"><p className="page-state page-error">{error}</p></div>;
  if (!info) return <div className="page page-default"><p className="page-state"><Loader2 size={16} className="animate-spin" /> Loading…</p></div>;
  const lensReady = info.lenses.find((l) => l.id === lens)?.ready;

  const lensLabel = info.lenses.find((l) => l.id === lens)?.label ?? "All books";
  const TABS: [typeof tab, string, string, React.ReactNode][] = [
    ["sheet", "Notes", "Notes", <BookOpen key="n" size={15} />],
    ["read", `Read & recall${info.reading?.done ? " ✓" : ""}`, "Recall", <BookOpenCheck key="r" size={15} />],
    ["practise", "Practise MCQs", "MCQs", <ListChecks key="p" size={15} />],
    ["ask", "Ask Dr. Shifa", "Ask", <MessageCircleHeart key="a" size={15} />],
  ];

  return (
    <div className="page page-default pn-topicpage">
      <nav className="pn-crumbs" aria-label="Where you are">
        <button type="button" onClick={onBack}><ArrowLeft size={14} /> Syllabus</button>
        <span aria-hidden>/</span>
        <span>{title(info.system)}</span>
      </nav>
      <h1 className="page-title pn-topic-title">{info.topic.name}</h1>
      {info.topic.subtopics.length ? (
        <div className="pn-topic" style={{ paddingLeft: 0 }}><div className="subs">{info.topic.subtopics.map((s) => <span key={s}>{s}</span>)}</div></div>
      ) : null}

      <div className="pn-tabs" role="tablist" aria-label="Study this topic">
        {TABS.map(([id, label, short, icon]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} aria-label={label} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
            {icon}<span className="pn-tab-long">{label}</span><span className="pn-tab-short">{short}</span>
          </button>
        ))}
      </div>

      {tab === "sheet" ? (
        <>
          <div className="pn-reader-bar">
            <label className="pn-select">
              <span>Source</span>
              <select value={lens} onChange={(e) => setLens(e.target.value)} aria-label="Which books the notes come from">
                {info.lenses.map((l) => <option key={l.id} value={l.id}>{l.id === "all" ? "All books and guidelines" : l.label}</option>)}
              </select>
            </label>
            <div className="pn-theme" role="radiogroup" aria-label="Page colour">
              {(["paper", "night"] as const).map((t) => (
                <button key={t} type="button" role="radio" aria-checked={theme === t} className={theme === t ? "active" : ""} onClick={() => pickTheme(t)}>
                  {t === "paper" ? "Paper" : "Night"}
                </button>
              ))}
            </div>
          </div>
          <article className={`pn-page ${theme === "night" ? "night" : ""}`}>
            <header className="pn-page-run"><span>Paed<b>NAMA</b> · Revision notes</span><span>{title(info.system)}</span></header>
            <div className="pn-page-head">
              <div className="pn-page-kicker">{stage === "imm" ? "IMM" : "FCPS-II"} · CPSP curriculum p.{info.topic.page ?? info.system_page}
                {lens !== "all" ? ` · from ${lensLabel}` : ""}</div>
              <h2>{info.topic.name}</h2>
            </div>
            {writing ? (
              <p className="pn-wait"><Loader2 size={16} className="animate-spin" />
                {lensReady ? "Opening the notes…" : "Writing these notes from the books. The first time takes up to a minute; after that they open at once."}</p>
            ) : sheetError ? (
              <p className="pn-page-note">{sheetError}</p>
            ) : sheet ? (
              <>
                <div className="pn-page-body" dangerouslySetInnerHTML={{ __html: renderSheet(sheet.markdown, sheet.citations) }} />
                {sheet.citations.length ? (
                  <footer className="pn-page-sources">
                    <h3>Sources</h3>
                    <ol>
                      {sheet.citations.map((c) => (
                        <li key={c.n} value={c.n}><i>{c.book_title}</i>{c.chapter ? `, ${c.chapter.split(" > ").slice(-1)[0]}` : ""}{c.page ? `, p. ${c.page}` : ""}</li>
                      ))}
                    </ol>
                  </footer>
                ) : null}
                <p className="pn-page-colophon">Written for PaedNAMA from your Paediatrics library. Every fact is cited to its book and page; check a source before you rely on it.</p>
              </>
            ) : null}
          </article>
        </>
      ) : tab === "read" ? (
        <ReadLoop stage={stage} topicId={topicId} onFinished={loadInfo} />
      ) : tab === "practise" ? (
        <PracticePanel stage={stage} topicId={topicId} />
      ) : (
        <ShifaChat stage={stage} topicId={topicId} topicName={info.topic.name} compact />
      )}
    </div>
  );
}
