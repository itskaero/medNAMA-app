"use client";

import React, { useEffect, useMemo, useState } from "react";
import { ChevronDown, ListTree } from "lucide-react";
import PageShell from "@/components/layout/PageShell";
import { api } from "~/lib/api";
import type { StageId, System } from "~/lib/types";

interface Syllabus { stage: StageId; title: string; systems: System[]; mini_cex: { page: number; topics: string[] }; dops: { page: number; procedures: string[] } }

/** The whole syllabus of the chosen stage, by system, searchable; plus the Mini-CEX topics and DOPS procedures. */
export default function SyllabusView({ stage, initialQuery = "", onOpen }: { stage: StageId; initialQuery?: string; onOpen?: (id: string) => void }) {
  const [data, setData] = useState<Syllabus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState(initialQuery);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    setData(null);
    api<Syllabus>(`/api/paeds/syllabus?stage=${stage}`).then(setData).catch((e) => setError(e.message));
  }, [stage]);

  const systems = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!data || !words.length) return data?.systems ?? [];
    return data.systems
      .map((s) => ({ ...s, topics: s.topics.filter((t) => {
        const hay = [t.name, s.system, ...t.subtopics].join(" ").toLowerCase();
        return words.every((w) => hay.includes(w));
      }) }))
      .filter((s) => s.topics.length);
  }, [data, q]);
  const total = data?.systems.reduce((n, s) => n + s.topics.length, 0) ?? 0;

  return (
    <PageShell title="Syllabus" icon={<ListTree size={22} />} loading={!data && !error} error={error}
      subtitle={data ? `${data.title}: ${data.systems.length} systems, ${total} topics, from the CPSP curriculum.` : undefined}>
      <input className="pn-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder='Find a topic, e.g. "jaundice" or "DKA"'
        aria-label="Find a topic" />
      <div className="pn-systems" style={{ marginTop: 12 }}>
        {systems.map((s) => {
          const isOpen = open[s.id] || !!q;
          let group: string | undefined;
          return (
            <div key={s.id} className="pn-system">
              <button type="button" aria-expanded={isOpen} onClick={() => setOpen((o) => ({ ...o, [s.id]: !o[s.id] }))}>
                <span>{s.system.charAt(0) + s.system.slice(1).toLowerCase()} <small>· {s.topics.length} topic{s.topics.length === 1 ? "" : "s"} · p.{s.page}</small></span>
                <ChevronDown size={16} style={{ transform: isOpen ? "rotate(180deg)" : undefined }} />
              </button>
              {isOpen ? (
                <div className="pn-topics">
                  {s.topics.map((t) => {
                    const head = t.group && t.group !== group ? (group = t.group) : null;
                    return (
                      <React.Fragment key={t.id}>
                        {head ? <div className="pn-group">{head}</div> : null}
                        <div className="pn-topic">
                          <button type="button" className="pn-topic-open" onClick={() => onOpen?.(t.id)}>{t.name}</button>
                          {t.subtopics.length ? <div className="subs">{t.subtopics.map((x) => <span key={x}>{x}</span>)}</div> : null}
                        </div>
                      </React.Fragment>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {data && !q ? (
        <div className="pn-facts" style={{ marginTop: "var(--sp-5)" }}>
          <div className="pn-fact">
            <span className="tile-kicker">Mini-CEX topics · p.{data.mini_cex.page}</span>
            <span className="tile-text">Assessed at work, and the best guide to what TOACS stations ask.</span>
            <ul>{data.mini_cex.topics.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
          <div className="pn-fact">
            <span className="tile-kicker">DOPS procedures · p.{data.dops.page}</span>
            <span className="tile-text">Procedures you should be able to do and explain step by step.</span>
            <ul>{data.dops.procedures.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
        </div>
      ) : null}
    </PageShell>
  );
}
