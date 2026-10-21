"use client";

import React, { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { api } from "~/lib/api";
import type { StageId, Topic } from "~/lib/types";

type Hit = Topic & { system: string };

/** The top bar's search: any syllabus topic, as you type; choosing one opens it in the syllabus. */
export default function TopicSearch({ stage, onPick, onOpen }: { stage: StageId; onPick: (q: string) => void; onOpen?: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (q.trim().length < 2) return;
    const t = setTimeout(() => {
      api<{ results: Hit[] }>(`/api/paeds/topics/search?stage=${stage}&q=${encodeURIComponent(q.trim())}`)
        .then((d) => { setHits(d.results.slice(0, 8)); setOpen(true); })
        .catch(() => setHits([]));
    }, 200);
    return () => clearTimeout(t);
  }, [q, stage]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const pick = (text: string) => { onPick(text); setOpen(false); setQ(""); };

  return (
    <div className="pn-searchbox" ref={box}>
      <Search size={15} aria-hidden />
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the syllabus: jaundice, DKA, murmur…"
        aria-label="Search the syllabus" onFocus={() => hits.length && setOpen(true)}
        onKeyDown={(e) => { if (e.key === "Enter" && q.trim()) pick(q.trim()); }} />
      {open && q.trim().length >= 2 ? (
        <div className="pn-results" role="listbox">
          {hits.length ? hits.map((h) => (
            <button key={h.id} type="button" role="option" aria-selected={false} onClick={() => { if (onOpen) { onOpen(h.id); setOpen(false); setQ(""); } else pick(h.name); }}>
              {h.name}<small>{h.system.charAt(0) + h.system.slice(1).toLowerCase()}{h.page ? ` · p.${h.page}` : ""}</small>
            </button>
          )) : <button type="button" disabled>No topic matches “{q.trim()}”</button>}
        </div>
      ) : null}
    </div>
  );
}
