"use client";

import React, { useCallback, useEffect, useState } from "react";
import { BookOpen, Database, Library, Loader2, Scale } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";
import PageShell from "@/components/layout/PageShell";
import type { Book } from "@/types";
import type { ViewId } from "@/lib/nav";

/** Admin > Content: the state of the library and of the content batches, with the page-reference batch runnable
 *  from here (the same work as scripts/prewarm_refs.py, a few questions at a time). */
export default function ContentView({ getHeaders, books, onNavigate }: {
  getHeaders: () => HeadersInit;
  books: Book[];
  onNavigate: (v: ViewId) => void;
}) {
  const [todo, setTodo] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState<Record<string, number>>({});
  const loadTodo = useCallback(async () => {
    const r = await fetch(`${API}/api/admin/refs/todo?limit=5000&min_years=1`, { headers: getHeaders(), credentials: "include" }).catch(() => null);
    const d = r && r.ok ? await r.json() : null;
    setTodo(d ? d.mcq_ids.length : null);
  }, [getHeaders]);
  useEffect(() => { loadTodo(); }, [loadTodo]);

  const runRefs = async (n: number) => {
    setRunning(true);
    try {
      const r = await fetch(`${API}/api/admin/refs/todo?limit=${n}&min_years=1`, { headers: getHeaders(), credentials: "include" });
      const ids: number[] = (await r.json()).mcq_ids || [];
      const counts: Record<string, number> = {};
      for (const id of ids) {
        const res = await fetch(`${API}/api/admin/refs/${id}`, { method: "POST", headers: getHeaders(), credentials: "include" });
        const body = await res.json().catch(() => ({}));
        const k = res.ok ? body.status || "done" : "error";
        counts[k] = (counts[k] || 0) + 1;
        setDone({ ...counts });
      }
      toast.success(`Page references: ${Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(", ")}`);
    } catch {
      toast.error("The page-reference batch stopped.");
    } finally {
      setRunning(false);
      loadTodo();
    }
  };

  const status = (s: string) => books.filter((b) => b.status === s).length;
  const pages = books.reduce((a, b) => a + (b.total_pages || 0), 0);

  return (
    <PageShell title="Content" icon={<Database size={22} />} subtitle="The library and the content batches behind explanations and practice.">
      <div className="tile-grid">
        <div className="tile">
          <span className="tile-kicker"><Library size={12} style={{ verticalAlign: -1 }} /> Library</span>
          <span className="tile-title">{books.length} textbooks · {pages.toLocaleString()} pages</span>
          <span className="tile-text">{status("ready")} ready · {status("processing")} processing · {status("failed")} failed</span>
          <span className="next-actions"><button className="btn-workspace" onClick={() => onNavigate("library")}><BookOpen size={12} /> Open the library (upload)</button></span>
        </div>
        <div className="tile">
          <span className="tile-kicker">Page references</span>
          <span className="tile-title">{todo === null ? "…" : `${todo.toLocaleString()} past-paper explanations to reference`}</span>
          <span className="tile-text">Adds checked [Book, Page] points to the most-asked explanations; disputed keys go to Reports.</span>
          <span className="next-actions">
            {[10, 50].map((n) => (
              <button key={n} className="btn-workspace" disabled={running || !todo} onClick={() => runRefs(n)}>
                {running ? <Loader2 size={12} className="animate-spin" /> : null} Reference the next {n}
              </button>
            ))}
          </span>
          {Object.keys(done).length ? <span className="tile-text">This run: {Object.entries(done).map(([k, v]) => `${v} ${k}`).join(" · ")}</span> : null}
        </div>
        <button className="tile" onClick={() => onNavigate("referee")}>
          <span className="tile-kicker"><Scale size={12} style={{ verticalAlign: -1 }} /> Quality</span>
          <span className="tile-title">Answer-key referee</span>
          <span className="tile-text">Check recall answers and question keys against the textbooks.</span>
        </button>
        <div className="tile">
          <span className="tile-kicker">Harder versions &amp; twists</span>
          <span className="tile-title">Written ahead on the PC</span>
          <span className="tile-text">scripts/prewarm_harder.py and prewarm_twists.py, then the transfer scripts to the NAS.</span>
        </div>
      </div>
    </PageShell>
  );
}
