"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

interface Paper { id: string; title: string }
interface Exam { id: string; short_name: string; papers: Paper[] }
interface Topic { id: string; topic_name: string; subtopics: string[] }
interface Subject { subject_name: string; topics: Topic[] }
interface Item {
  mcq_id: number;
  suggested_topic_id: string;
  suggested: string;
  question_text: string;
  options: Record<string, string>;
  correct_option: string;
  source: string | null;
}

const PAGE = 25;

/** Admin > Content: close calls the TOS tagger held out of practice (backend /api/tos/review). Accept the
 *  suggested topic, file it under another, or rule it off this paper's syllabus. Decisions survive re-tagging. */
export default function TosReviewPanel({ getHeaders }: { getHeaders: () => HeadersInit }) {
  const [exams, setExams] = useState<Exam[]>([]);
  const [examId, setExamId] = useState("pget");
  const [paperId, setPaperId] = useState("mbbs-p1");
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [items, setItems] = useState<Item[] | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const [reassign, setReassign] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);

  const call = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const r = await fetch(`${API}${path}`, { ...init, headers: { "Content-Type": "application/json", ...getHeaders() }, credentials: "include" });
    const body = await r.json().catch(() => null);
    if (!r.ok) throw new Error(body?.detail || `Request failed (${r.status})`);
    return body as T;
  }, [getHeaders]);

  useEffect(() => {
    call<Exam[]>("/api/tos/examinations").then(setExams).catch(() => setExams([]));
  }, [call]);

  const load = useCallback((ex: string, pp: string, off: number) => {
    call<{ total: number; items: Item[] }>(`/api/tos/review?exam_id=${ex}&paper_id=${encodeURIComponent(pp)}&offset=${off}&limit=${PAGE}`)
      .then((d) => { setItems(d.items); setTotal(d.total); })
      .catch((e) => { setItems([]); toast.error(e.message); });
    call<{ subjects: Subject[] }>(`/api/tos/paper?exam_id=${ex}&paper_id=${encodeURIComponent(pp)}`)
      .then((t) => setSubjects(t.subjects)).catch(() => setSubjects([]));
  }, [call]);

  useEffect(() => { load(examId, paperId, offset); }, [load, examId, paperId, offset]);

  const decide = async (it: Item, action: "accept" | "reassign" | "reject") => {
    const topic = reassign[it.mcq_id];
    if (action === "reassign" && !topic) return toast.warning("Choose a topic first.");
    setBusy(it.mcq_id);
    try {
      await call(`/api/tos/review/${it.mcq_id}`, {
        method: "POST",
        body: JSON.stringify({ exam_id: examId, paper_id: paperId, action, topic_id: action === "reassign" ? topic : null }),
      });
      setItems((cur) => (cur || []).filter((x) => x.mcq_id !== it.mcq_id));
      setTotal((n) => Math.max(0, n - 1));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the decision");
    } finally {
      setBusy(null);
    }
  };

  const papers = exams.find((e) => e.id === examId)?.papers || [];
  const selectStyle: React.CSSProperties = {
    padding: "6px 8px", borderRadius: 8, border: "1px solid var(--border-light)", background: "var(--surface-2)",
    color: "var(--text-primary)", fontSize: "0.8rem", maxWidth: "100%",
  };

  return (
    <section className="tos-card" aria-label="TOS tag review" style={{ marginTop: "var(--sp-5)" }}>
      <div className="tos-row-between">
        <div>
          <span className="tile-kicker">TOS tag review</span>
          <h2 className="tos-h2">{total.toLocaleString()} close calls held out of practice</h2>
          <p className="tile-text">The tagger could not clearly tell which topic these belong to. Your decision is kept on every re-run of scripts/tag_tos.py.</p>
        </div>
        <div className="practice-picker-row">
          <select aria-label="Examination" style={selectStyle} value={examId} onChange={(e) => {
            const ex = exams.find((x) => x.id === e.target.value);
            setItems(null); setExamId(e.target.value); setOffset(0); if (ex?.papers[0]) setPaperId(ex.papers[0].id);
          }}>
            {exams.map((e) => <option key={e.id} value={e.id}>{e.short_name}</option>)}
          </select>
          <select aria-label="Paper" style={selectStyle} value={paperId} onChange={(e) => { setItems(null); setPaperId(e.target.value); setOffset(0); }}>
            {papers.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select>
        </div>
      </div>

      {items === null ? (
        <div className="page-state"><Loader2 size={16} className="animate-spin" /> Loading…</div>
      ) : !items.length ? (
        <div className="page-state">Nothing waiting for review on this paper.</div>
      ) : (
        <div className="tos-subjects">
          {items.map((it) => (
            <div key={it.mcq_id} className={`practice-subject tos-subject ${open === it.mcq_id ? "open" : ""}`}>
              <div className="practice-subject-head">
                <button type="button" className="practice-subject-pick" aria-expanded={open === it.mcq_id}
                  onClick={() => setOpen(open === it.mcq_id ? null : it.mcq_id)}>
                  <span className="tos-subject-line">
                    {open === it.mcq_id ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                    <span className="practice-subject-name" style={{ fontWeight: 500 }}>{it.question_text.slice(0, 140)}{it.question_text.length > 140 ? "…" : ""}</span>
                  </span>
                  <span className="practice-subject-count">Suggested: {it.suggested} · #{it.mcq_id}{it.source ? ` · ${it.source}` : ""}</span>
                </button>
                <div className="tos-subject-end">
                  <button type="button" className="btn-workspace tos-btn-sm" disabled={busy === it.mcq_id} onClick={() => decide(it, "accept")} title="File it under the suggested topic">
                    <Check size={12} /> Accept
                  </button>
                  <button type="button" className="btn-workspace tos-btn-sm" disabled={busy === it.mcq_id} onClick={() => decide(it, "reject")} title="Not on this paper's syllabus">
                    <X size={12} /> Not this paper
                  </button>
                </div>
              </div>
              {open === it.mcq_id ? (
                <div className="tos-topics" style={{ padding: "10px 14px", gap: 10 }}>
                  <p className="tos-missed-stem">{it.question_text}</p>
                  <div className="tos-subtopics">
                    {Object.entries(it.options || {}).map(([k, v]) => (
                      <span key={k} className={`tos-tag ${k === it.correct_option ? "hit" : ""}`}>{k}. {v}</span>
                    ))}
                  </div>
                  <div className="practice-picker-row">
                    <select aria-label="File under topic" style={selectStyle} value={reassign[it.mcq_id] || ""}
                      onChange={(e) => setReassign((r) => ({ ...r, [it.mcq_id]: e.target.value }))}>
                      <option value="">File under another topic…</option>
                      {subjects.map((s) => (
                        <optgroup key={s.subject_name} label={s.subject_name}>
                          {s.topics.map((t) => <option key={t.id} value={t.id}>{t.topic_name}</option>)}
                        </optgroup>
                      ))}
                    </select>
                    <button type="button" className="btn-workspace tos-btn-sm" disabled={busy === it.mcq_id || !reassign[it.mcq_id]}
                      onClick={() => decide(it, "reassign")}>Save</button>
                  </div>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}

      {total > PAGE ? (
        <div className="tos-row-between">
          <button type="button" className="btn-workspace tos-btn-sm" disabled={offset === 0} onClick={() => { setItems(null); setOffset(Math.max(0, offset - PAGE)); }}>Previous</button>
          <span className="tos-finder-note">{offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}</span>
          <button type="button" className="btn-workspace tos-btn-sm" disabled={offset + PAGE >= total} onClick={() => { setItems(null); setOffset(offset + PAGE); }}>Next</button>
        </div>
      ) : null}
    </section>
  );
}
