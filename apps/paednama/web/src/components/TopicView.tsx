"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ArrowLeft, BookOpenCheck, Loader2 } from "lucide-react";
import { api } from "~/lib/api";
import { renderSheet, type Citation } from "~/lib/md";
import type { StageId, Topic } from "~/lib/types";
import ReadLoop from "~/components/ReadLoop";
import ShifaChat from "~/components/ShifaChat";

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
  const [tab, setTab] = useState<"sheet" | "read" | "ask">("sheet");
  const [lens, setLens] = useState("all");
  const [sheet, setSheet] = useState<{ markdown: string; citations: Citation[] } | null>(null);
  const [sheetError, setSheetError] = useState<string | null>(null);
  const [writing, setWriting] = useState(false);

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

  return (
    <div className="page page-default">
      <header className="page-head">
        <div>
          <button type="button" className="pn-link" onClick={onBack}><ArrowLeft size={13} style={{ verticalAlign: -2 }} /> Syllabus</button>
          <div className="pn-kicker" style={{ marginTop: 10 }}>{title(info.system)} · curriculum p.{info.topic.page ?? info.system_page}</div>
          <h1 className="page-title">{info.topic.name}</h1>
          {info.topic.subtopics.length ? (
            <div className="pn-topic" style={{ paddingLeft: 0 }}><div className="subs">{info.topic.subtopics.map((s) => <span key={s}>{s}</span>)}</div></div>
          ) : null}
        </div>
        <div className="segmented" role="tablist" aria-label="Study mode">
          <button type="button" role="tab" aria-selected={tab === "sheet"} className={`segmented-item ${tab === "sheet" ? "active" : ""}`} onClick={() => setTab("sheet")}>Summary</button>
          <button type="button" role="tab" aria-selected={tab === "read"} className={`segmented-item ${tab === "read" ? "active" : ""}`} onClick={() => setTab("read")}>
            Read &amp; recall{info.reading?.done ? " ✓" : ""}</button>
          <button type="button" role="tab" aria-selected={tab === "ask"} className={`segmented-item ${tab === "ask" ? "active" : ""}`} onClick={() => setTab("ask")}>Ask Dr. Shifa</button>
        </div>
      </header>

      {tab === "sheet" ? (
        <>
          <div className="pn-row" role="radiogroup" aria-label="Which books">
            {info.lenses.map((l) => (
              <button key={l.id} type="button" role="radio" aria-checked={lens === l.id} title={l.role ? `${l.label} · ${l.role}` : l.label}
                className={`practice-chip ${lens === l.id ? "active" : ""}`} onClick={() => setLens(l.id)}>
                {l.id === "all" ? <BookOpenCheck size={13} /> : null}{l.label}
              </button>
            ))}
          </div>
          <section className="pn-panel">
            {writing ? (
              <p className="pn-wait"><Loader2 size={16} className="animate-spin" />
                {lensReady ? "Loading…" : "Writing this summary from the books. The first time takes up to a minute; after that it opens at once."}</p>
            ) : sheetError ? (
              <p className="tile-text">{sheetError}</p>
            ) : sheet ? (
              <>
                <div className="pn-sheet" dangerouslySetInnerHTML={{ __html: renderSheet(sheet.markdown, sheet.citations) }} />
                {sheet.citations.length ? (
                  <ol className="pn-cites">
                    {sheet.citations.map((c) => (
                      <li key={c.n} value={c.n}><b>{c.book_title}</b>{c.chapter ? ` · ${c.chapter}` : ""}{c.page ? ` · p.${c.page}` : ""}</li>
                    ))}
                  </ol>
                ) : null}
              </>
            ) : null}
          </section>
        </>
      ) : tab === "read" ? (
        <ReadLoop stage={stage} topicId={topicId} onFinished={loadInfo} />
      ) : (
        <ShifaChat stage={stage} topicId={topicId} topicName={info.topic.name} compact />
      )}
    </div>
  );
}
