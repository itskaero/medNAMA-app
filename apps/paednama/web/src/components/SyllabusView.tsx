"use client";

import React, { useEffect, useMemo, useState } from "react";
import { ArrowLeft, BookOpenCheck, ChevronRight, ListChecks, ListTree, Search } from "lucide-react";
import PageShell from "@/components/layout/PageShell";
import { api } from "~/lib/api";
import type { StageId, System, Topic } from "~/lib/types";

interface Progress { read?: boolean; mcqs?: number; answered?: number }
interface Syllabus {
  stage: StageId; title: string; systems: System[];
  mini_cex: { page: number; topics: string[] }; dops: { page: number; procedures: string[] };
  progress: Record<string, Progress>;
}
const nice = (s: string) => s.charAt(0) + s.slice(1).toLowerCase();
const KEY = "pn-syllabus-system";

/** The syllabus in two steps: every system as a card (with your progress), then one system's topics as rows you open.
 *  Search lists matching topics from every system at once. The open system is remembered for the way back. */
export default function SyllabusView({ stage, initialQuery = "", onOpen }: { stage: StageId; initialQuery?: string; onOpen?: (id: string) => void }) {
  const [data, setData] = useState<Syllabus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState(initialQuery);
  const [systemId, setSystemId] = useState<string | null>(null);
  useEffect(() => {
    setData(null);
    api<Syllabus>(`/api/paeds/syllabus?stage=${stage}`).then(setData).catch((e) => setError(e.message));
    try { setSystemId(sessionStorage.getItem(`${KEY}:${stage}`)); } catch { /* private mode */ }
  }, [stage]);
  const pickSystem = (id: string | null) => {
    setSystemId(id);
    try { id ? sessionStorage.setItem(`${KEY}:${stage}`, id) : sessionStorage.removeItem(`${KEY}:${stage}`); } catch { /* private mode */ }
    if (typeof window !== "undefined") window.scrollTo({ top: 0 });
  };

  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hits = useMemo(() => {
    if (!data || !words.length) return [];
    return data.systems.flatMap((s) => s.topics.filter((t) => {
      const hay = [t.name, s.system, ...t.subtopics].join(" ").toLowerCase();
      return words.every((w) => hay.includes(w));
    }).map((t) => ({ t, s })));
  }, [data, q]); // eslint-disable-line react-hooks/exhaustive-deps

  const total = data?.systems.reduce((n, s) => n + s.topics.length, 0) ?? 0;
  const current = data?.systems.find((s) => s.id === systemId) ?? null;
  const prog = data?.progress ?? {};
  const started = (t: Topic) => !!(prog[t.id]?.read || prog[t.id]?.answered);

  return (
    <PageShell title="Syllabus" icon={<ListTree size={22} />} loading={!data && !error} error={error}
      subtitle={data ? `${data.title}: ${data.systems.length} systems, ${total} topics, from the CPSP curriculum. Pick a system, then a topic.` : undefined}>
      <label className="pn-find">
        <Search size={16} aria-hidden />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder='Find a topic in any system: "jaundice", "DKA", "murmur"'
          aria-label="Find a topic" />
      </label>

      {words.length ? (
        <section className="pn-rows" aria-label="Matching topics">
          <p className="pn-rows-note">{hits.length} topic{hits.length === 1 ? "" : "s"} match</p>
          {hits.map(({ t, s }) => <TopicRow key={t.id} t={t} system={nice(s.system)} p={prog[t.id]} onOpen={onOpen} />)}
        </section>
      ) : current ? (
        <section aria-label={nice(current.system)}>
          <button type="button" className="pn-back" onClick={() => pickSystem(null)}><ArrowLeft size={15} /> All systems</button>
          <div className="pn-system-head">
            <h2>{nice(current.system)}</h2>
            <span>{current.topics.length} topics · curriculum p.{current.page} · {current.topics.filter(started).length} started</span>
          </div>
          <div className="pn-rows">
            {(() => {
              let group: string | undefined;
              return current.topics.map((t, i) => {
                const head = t.group && t.group !== group ? (group = t.group) : null;
                return (
                  <React.Fragment key={t.id}>
                    {head ? <div className="pn-group">{head}</div> : null}
                    <TopicRow t={t} n={i + 1} p={prog[t.id]} onOpen={onOpen} />
                  </React.Fragment>
                );
              });
            })()}
          </div>
        </section>
      ) : data ? (
        <>
          <div className="pn-system-grid">
            {data.systems.map((s) => {
              const done = s.topics.filter(started).length;
              return (
                <button key={s.id} type="button" className="pn-system-card" onClick={() => pickSystem(s.id)}>
                  <span className="pn-system-name">{nice(s.system)}</span>
                  <span className="pn-system-meta">{s.topics.length} topic{s.topics.length === 1 ? "" : "s"} · p.{s.page}</span>
                  <span className="pn-system-bar" aria-hidden><i style={{ width: `${Math.round((done / Math.max(1, s.topics.length)) * 100)}%` }} /></span>
                  <span className="pn-system-foot">{done ? `${done} started` : "Not started"}<ChevronRight size={16} /></span>
                </button>
              );
            })}
          </div>
          <div className="pn-facts" style={{ marginTop: "var(--sp-5)" }}>
            <details className="pn-fact">
              <summary><span className="tile-kicker">Mini-CEX topics · p.{data.mini_cex.page}</span>
                <span className="tile-text">Assessed at work, and the best guide to what TOACS stations ask.</span></summary>
              <ul>{data.mini_cex.topics.map((t) => <li key={t}>{t}</li>)}</ul>
            </details>
            <details className="pn-fact">
              <summary><span className="tile-kicker">DOPS procedures · p.{data.dops.page}</span>
                <span className="tile-text">Procedures you should be able to do and explain step by step.</span></summary>
              <ul>{data.dops.procedures.map((t) => <li key={t}>{t}</li>)}</ul>
            </details>
          </div>
        </>
      ) : null}
    </PageShell>
  );
}

function TopicRow({ t, n, system, p, onOpen }: { t: Topic; n?: number; system?: string; p?: Progress; onOpen?: (id: string) => void }) {
  return (
    <button type="button" className="pn-row-topic" onClick={() => onOpen?.(t.id)}>
      {n ? <span className="pn-row-n">{String(n).padStart(2, "0")}</span> : null}
      <span className="pn-row-main">
        <span className="pn-row-name">{t.name}</span>
        {system ? <span className="pn-row-sys">{system}</span> : null}
        {t.subtopics.length ? <span className="pn-row-subs">{t.subtopics.slice(0, 4).join(" · ")}{t.subtopics.length > 4 ? ` · +${t.subtopics.length - 4}` : ""}</span> : null}
      </span>
      <span className="pn-row-status">
        {p?.read ? <span className="pn-badge pn-badge-ok"><BookOpenCheck size={12} /> Read</span> : null}
        {p?.mcqs ? <span className="pn-badge"><ListChecks size={12} /> {p.answered ?? 0}/{p.mcqs}</span> : null}
      </span>
      <ChevronRight size={18} className="pn-row-go" aria-hidden />
    </button>
  );
}
