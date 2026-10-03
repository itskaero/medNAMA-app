"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft, BookOpenCheck, ChevronDown, FileText, ListChecks, Loader2,
  RefreshCw, Scale, Sparkles, X,
} from "lucide-react";
import { API } from "@/lib/constants";
import { parseMarkdown } from "@/utils/markdown";
import { FiguresDrawer } from "@/components/FiguresDrawer";
import { Figure } from "@/types";
import { toast } from "sonner";

/** A book a student can revise from, with how many section headings it has. */
interface BookOption { id: number; title: string; total_pages: number; chapter_count: number }

interface Coverage {
  kind: "section" | "topic";
  read: number;
  own?: number | null;      // section scope only: paragraphs the heading itself covers
  extended?: boolean;       // section scope only: read continued onto following pages
}

interface RevisionSheet {
  label: string;
  markdown: string;
  citations: { book_title: string; page_number: number }[];
  figures: Figure[];
  coverage: Coverage;
  gap_count: number;
  cached: boolean;
  created_at: string | null;
}

const CHAPTER_DEBOUNCE_MS = 250;
const QUIZ_POLL_MS = 2500;

/**
 * Fast revision from the student's own textbooks: pick the books, aim the sheet with a
 * topic or a chapter/section heading, choose quick vs full, then (for single books) ask
 * for a drill on the same scope.
 */
export default function ReviseView({
  token,
  isAdmin,
  onBack,
  onFigureClick,
  onTestMe,
}: {
  token: string | null;
  isAdmin: boolean;
  onBack: () => void;
  onFigureClick: (f: Figure) => void;
  /** Start a saved AI quiz set straight away (skips the builder). */
  onTestMe: (quizSetId: string, label: string) => void;
}) {
  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
  }, [token]);

  // ── scope builder ────────────────────────────────────────────────────────────
  const [books, setBooks] = useState<BookOption[]>([]);
  const [bookIds, setBookIds] = useState<number[]>([]);
  const [chapter, setChapter] = useState<string | null>(null);
  const [topic, setTopic] = useState("");
  const [length, setLength] = useState<"quick" | "full">("quick");
  const [chapterQuery, setChapterQuery] = useState("");
  const [chapterResults, setChapterResults] = useState<string[]>([]);
  const [chapterOpen, setChapterOpen] = useState(false);
  const [anchorInput, setAnchorInput] = useState(false);   // search box vs chosen heading chip

  const [buildEnabled, setBuildEnabled] = useState(false);

  // ── the sheet ────────────────────────────────────────────────────────────────
  const [sheet, setSheet] = useState<RevisionSheet | null>(null);
  const [sheetState, setSheetState] = useState<"idle" | "loading" | "error">("idle");
  const [sheetError, setSheetError] = useState<string | null>(null);
  const [quizBusy, setQuizBusy] = useState(false);

  const searchSeq = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const openRef = useRef(false);        // a Study Corner "Open" asked us to show a saved sheet
  const builtOnceRef = useRef(false);   // only the first auto-open builds

  // Load the books once; restore a previously saved scope so the page survives refresh.
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${API}/api/study/revision-books`, { headers: headers(), credentials: "include" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const list: BookOption[] = await r.json();
        setBooks(list);
        try {
          const saved = JSON.parse(localStorage.getItem("mednama_revise_scope") || "null");
          if (saved && Array.isArray(saved.book_ids)) {
            const ids = saved.book_ids.filter((n: number) => list.some((b) => b.id === n));
            if (ids.length) setBookIds(ids);
            if (typeof saved.chapter === "string" && saved.chapter) setChapter(saved.chapter);
            if (typeof saved.topic === "string") setTopic(saved.topic);
            if (saved.length === "full") setLength("full");
            if (localStorage.getItem("mednama_revise_open") === "1") {
              openRef.current = true;                 // opened from Study Corner: build it once
              localStorage.removeItem("mednama_revise_open");
            }
          }
          // "Revise <topic>" from Mistakes, Today or Stats: that topic across the chosen books (all of them if none)
          const linked = localStorage.getItem("mednama_revise_topic");
          if (linked) {
            localStorage.removeItem("mednama_revise_topic");
            setTopic(linked);
            setChapter(null);
            setBookIds((cur) => (cur.length ? cur : list.map((b) => b.id)));
            openRef.current = true;
          }
        } catch {
          /* storage unavailable */
        }
      } catch {
        /* offline or not ready; the view still works for what it can show */
      }
    };
    load();
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setBuildEnabled(bookIds.length > 0 && Boolean((chapter || "").trim() || topic.trim()));
  }, [bookIds, chapter, topic]);

  // Persist the scope, so "revise this again tomorrow" survives a refresh.
  useEffect(() => {
    try {
      localStorage.setItem("mednama_revise_scope", JSON.stringify({ book_ids: bookIds, chapter, topic, length }));
    } catch {
      /* storage unavailable */
    }
  }, [bookIds, chapter, topic, length]);

  // Chapter search: debounced, and a later query never overwrites an earlier one.
  useEffect(() => {
    if (!anchorInput || !bookIds.length) {
      setChapterResults([]);
      return;
    }
    const seq = ++searchSeq.current;
    const q = chapterQuery.trim();
    const t = setTimeout(async () => {
      try {
        const r = await fetch(
          `${API}/api/study/revision-chapters?book_ids=${bookIds.join(",")}&q=${encodeURIComponent(q)}`,
          { headers: headers(), credentials: "include" }
        );
        const results: string[] = r.ok ? await r.json() : [];
        if (seq === searchSeq.current) {
          setChapterResults(results);
          setChapterOpen(true);
        }
      } catch {
        if (seq === searchSeq.current) setChapterResults([]);
      }
    }, CHAPTER_DEBOUNCE_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorInput, chapterQuery, bookIds]);

  const toggleBook = (id: number) => {
    setBookIds((cur) => (cur.includes(id) ? cur.filter((b) => b !== id) : [...cur, id]));
    setChapter(null);                    // a heading of the old selection may not exist here
    setChapterQuery("");
    setAnchorInput(false);
    setSheet(null);                      // the saved sheet was for another scope
  };

  const pickChapter = (name: string) => {
    setChapter(name);
    setAnchorInput(false);
    setChapterQuery(name);
    setChapterOpen(false);
    setSheet(null);
  };

  const build = async (regenerate = false) => {
    if (!buildEnabled && !regenerate) return;
    setSheetState("loading");
    setSheetError(null);
    try {
      const r = await fetch(`${API}/api/study/revision-sheet`, {
        method: "POST", headers: headers(), credentials: "include",
        body: JSON.stringify({
          book_ids: bookIds,
          chapter: chapter || null,
          topic: topic.trim() || null,
          length,
          regenerate: isAdmin && regenerate,
        }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      setSheet(body);
      setSheetState("idle");
      if (!body.cached) toast.success("Sheet saved to your Study Corner → Sheets tab.");
    } catch (e) {
      setSheetError(e instanceof Error ? e.message : String(e));
      setSheetState("error");
    }
  };

  // Opened from Study Corner: once the saved scope is restored (and buildable), show it.
  useEffect(() => {
    if (openRef.current && buildEnabled && !builtOnceRef.current) {
      openRef.current = false;
      builtOnceRef.current = true;
      build(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildEnabled]);

  useEffect(() => {
    if (sheetState === "idle") return;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sheetState]);

  const testMe = async () => {
    const subject = (chapter || topic).trim();
    const labels = books.filter((b) => bookIds.includes(b.id)).map((b) => b.title);
    const label = `${labels[0]} · ${subject}${length === "quick" ? " · quick" : ""}`;
    setQuizBusy(true);
    try {
      const startRes = await fetch(`${API}/api/chat/generate-ai-quiz/jobs`, {
        method: "POST", headers: headers(), credentials: "include",
        body: JSON.stringify({
          prompt: `High-yield ${subject} questions written from this book's own chapters`,
          book_id: bookIds[0],
          chapter: chapter || undefined,
          count: 10,
          exam_profile: "fcps",
        }),
      });
      const started = await startRes.json().catch(() => null);
      if (!startRes.ok || !started?.job_id) throw new Error((started && started.detail) || `HTTP ${startRes.status}`);
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, QUIZ_POLL_MS));
        const jobRes = await fetch(`${API}/api/chat/generate-ai-quiz/jobs/${started.job_id}`, { headers: headers(), credentials: "include" });
        const job = await jobRes.json().catch(() => null);
        if (job?.status === "done") {
          const qid = job.result?.quiz_set_id;
          if (!qid) throw new Error("quiz was saved but could not be started");
          onTestMe(qid, label);
          return;
        }
        if (job?.status === "failed") throw new Error(job.detail || "quiz generation failed");
      }
      throw new Error("still generating; try again shortly");
    } catch (e) {
      setSheetError(e instanceof Error ? e.message : String(e));
      setSheetState("error");
    } finally {
      setQuizBusy(false);
    }
  };

  const coverageNote = (c: Coverage): string => {
    if (c.kind === "topic") {
      const what = topic.trim() || chapter || "your topic";
      return `read the best passages on “${what}” across the selected books (${c.read} passages)`;
    }
    if (c.own && c.extended) {
      const own = c.own === 1 ? "one paragraph" : `${c.own} paragraphs`;
      return `the heading itself covers ${own}, so the sheet continues onto the pages that follow (${c.read} paragraphs read)`;
    }
    return `read ${c.read} paragraph${c.read === 1 ? "" : "s"} from this section`;
  };

  const input: React.CSSProperties = {
    background: "var(--surface-3)", border: "1px solid var(--border-light)", borderRadius: "10px",
    color: "var(--text-primary)", padding: "8px 10px", fontSize: "0.86rem", width: "100%",
  };
  const box: React.CSSProperties = { border: "1px solid var(--border-light)", borderRadius: "14px", padding: "16px 18px", background: "var(--surface-2)", marginBottom: "var(--sp-4)" };

  return (
    <div className="dashboard-view" role="region" aria-label="Revision sheets" style={{ maxWidth: "900px", margin: "0 auto" }}>
      <button className="btn-workspace" onClick={onBack} style={{ marginBottom: "var(--sp-3)" }}><ArrowLeft size={12} /> Back</button>
      <div className="dashboard-header">
        <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <BookOpenCheck size={20} style={{ color: "var(--sea-green)" }} /> Revise from your books
        </h1>
        <p style={{ margin: 0, fontSize: "0.85rem", color: "var(--text-secondary)" }}>
          One page of high-yield bullets, written only from pages you choose — no answer is invented, every reference is checked.
        </p>
      </div>

      {/* ── scope builder ── */}
      <section style={box} aria-label="Build a revision sheet">
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <label style={{ fontSize: "0.78rem", fontWeight: 700, display: "block", marginBottom: "6px" }}>Books
              <span style={{ color: "var(--text-muted)", fontWeight: 400 }}> — pick one or more</span>
            </label>
            {books.length === 0 ? (
              <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>Loading your books…</span>
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginTop: "2px" }}>
                {books.map((b) => {
                  const on = bookIds.includes(b.id);
                  return (
                    <button key={b.id} type="button" onClick={() => toggleBook(b.id)} aria-pressed={on}
                      className="btn-workspace"
                      title={`${b.chapter_count} section headings`}
                      style={{
                        padding: "6px 12px", fontSize: "0.8rem", borderColor: on ? "var(--sea-green)" : undefined,
                        background: on ? "rgba(52,211,153,0.14)" : undefined, fontWeight: on ? 700 : 400,
                      }}>
                      {b.title}
                      <span style={{ color: "var(--text-muted)", marginLeft: 6, fontSize: "0.68rem" }}>{b.chapter_count} sections</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="revise-grid">
            <div>
              <label style={{ fontSize: "0.78rem", fontWeight: 700, display: "block", marginBottom: "6px" }}>
                Chapter or section
                <span style={{ color: "var(--text-muted)", fontWeight: 400 }}> — optional anchor</span>
              </label>
              {!anchorInput && chapter ? (
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span className="btn-workspace" style={{ padding: "6px 10px", fontSize: "0.8rem", background: "rgba(48,197,255,0.12)", borderColor: "var(--sky)" }}>
                    {chapter}
                  </span>
                  <button type="button" className="btn-workspace" style={{ padding: "6px 8px" }} onClick={() => { setChapter(null); setAnchorInput(true); setChapterQuery(""); }} title="Clear the chapter">
                    <X size={12} />
                  </button>
                </div>
              ) : (
                <div style={{ position: "relative" }}>
                  <input value={chapterQuery} placeholder={bookIds.length ? (chapterQuery ? "" : "Search headings…") : "Pick a book first"}
                    disabled={!bookIds.length} onChange={(e) => { setAnchorInput(true); setChapterQuery(e.target.value); }}
                    onFocus={() => { setAnchorInput(true); setChapterOpen(true); }}
                    onBlur={() => setTimeout(() => setChapterOpen(false), 150)}
                    onKeyDown={(e) => { if (e.key === "Escape") setChapterOpen(false); }}
                    style={{ ...input, paddingRight: "28px" }} aria-label="Search chapter or section" />
                  <ChevronDown size={14} style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }} />
                  {chapterOpen && chapterResults.length > 0 ? (
                    <ul style={{
                      position: "absolute", zIndex: 20, top: "calc(100% + 4px)", left: 0, right: 0, maxHeight: 220, overflowY: "auto",
                      background: "var(--surface-3)", border: "1px solid var(--border-light)", borderRadius: "10px", margin: 0,
                      padding: "4px", listStyle: "none", boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
                    }}>
                      {chapterResults.map((name) => (
                        <li key={name}>
                          <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pickChapter(name)}
                            style={{ width: "100%", textAlign: "left", background: "none", border: 0, color: "var(--text-primary)", padding: "7px 8px", borderRadius: "8px", cursor: "pointer", fontSize: "0.8rem" }}>
                            {name}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              )}
            </div>

            <div>
              <label style={{ fontSize: "0.78rem", fontWeight: 700, display: "block", marginBottom: "6px" }}>
                Topic
                <span style={{ color: "var(--text-muted)", fontWeight: 400 }}> — what to look for</span>
              </label>
              <input value={topic} placeholder="e.g. acute glomerulonephritis, oxytocin, ARDS"
                onChange={(e) => { setTopic(e.target.value); setSheet(null); }}
                onKeyDown={(e) => { if (e.key === "Enter" && buildEnabled) build(); }}
                style={input} aria-label="Topic" />
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            <div role="group" aria-label="Sheet length" style={{ display: "flex", border: "1px solid var(--border-light)", borderRadius: "10px", overflow: "hidden" }}>
              {(["quick", "full"] as const).map((l) => (
                <button key={l} type="button" onClick={() => setLength(l)}
                  style={{
                    padding: "6px 14px", fontSize: "0.76rem", cursor: "pointer", border: 0,
                    background: length === l ? "rgba(48,197,255,0.16)" : "transparent",
                    color: "var(--text-primary)", fontWeight: length === l ? 700 : 400,
                  }}>
                  {l === "quick" ? "Quick · ~10 bullets" : "Full · ~22 bullets"}
                </button>
              ))}
            </div>
            <button type="button" className="btn-workspace" disabled={!buildEnabled} onClick={() => build(false)}
              style={{ marginLeft: "auto", fontWeight: 700 }}>
              {sheetState === "loading" ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
              {" "}Write the sheet
            </button>
          </div>
        </div>
      </section>

      {/* ── the sheet ── */}
      {sheetState === "loading" ? (
        <div style={{ color: "var(--text-muted)", display: "flex", gap: "8px", alignItems: "center", padding: "var(--sp-5) 0" }}>
          <Loader2 size={16} className="animate-spin" /> Reading the demanded pages and writing the sheet (a minute on a fresh topic, then it is saved)…
        </div>
      ) : sheetState === "error" ? (
        <div>
          <p style={{ color: "var(--error)", fontSize: "0.86rem" }}>Could not write the sheet: {sheetError}</p>
          <button className="btn-workspace" onClick={() => build(sheet !== null)}>Try again</button>
        </div>
      ) : sheet ? (
        <article style={{ border: "1px solid var(--border-light)", borderRadius: "14px", padding: "16px 18px", background: "var(--surface-2)" }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: "10px", flexWrap: "wrap", marginBottom: "8px" }}>
            <h2 style={{ margin: 0, fontSize: "0.95rem" }}>{sheet.label}</h2>
            <span style={{ marginLeft: "auto", fontSize: "0.68rem", color: "var(--text-muted)", background: "var(--surface-3)", border: "1px solid var(--border-light)", borderRadius: "20px", padding: "2px 10px" }}>
              {sheet.cached ? "saved " : "new "}sheet
            </span>
            {bookIds.length === 1 && (chapter || topic.trim()) ? (
              <button type="button" className="btn-workspace" onClick={testMe} disabled={quizBusy}
                style={{ fontWeight: 700 }}
                title="10 questions written from the same book and section, then start them immediately">
                {quizBusy ? <Loader2 size={12} className="animate-spin" /> : <ListChecks size={12} />} Test me
              </button>
            ) : null}
          </div>

          <FiguresDrawer figures={sheet.figures} token={token} onFigureClick={onFigureClick} />

          <div className="prose" style={{ fontSize: "0.9rem" }} dangerouslySetInnerHTML={{ __html: parseMarkdown(sheet.markdown) }} />

          <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap", marginTop: "14px", fontSize: "0.72rem", color: "var(--text-muted)" }}>
            <span><FileText size={11} /> {sheet.citations.length} textbook reference{sheet.citations.length === 1 ? "" : "s"} checked against the passages</span>
            <span>· {coverageNote(sheet.coverage)}</span>
            <span title="Every sheet you write is kept in Study Corner → Sheets">· saved to your Study Corner</span>
            {sheet.gap_count > 0 && <span>· {sheet.gap_count} of your missed question{sheet.gap_count === 1 ? "" : "s"} in these books below</span>}
            {isAdmin ? (
              <button className="btn-workspace" style={{ padding: "2px 8px", fontSize: "0.7rem", marginLeft: "auto" }}
                onClick={() => build(true)} title="Admin: discard the saved sheet and write it again">
                <RefreshCw size={11} /> Regenerate
              </button>
            ) : null}
          </div>
        </article>
      ) : (
        <div style={{ border: "1px dashed var(--border-light)", borderRadius: "14px", padding: "var(--sp-5)", textAlign: "center", color: "var(--text-muted)", fontSize: "0.85rem" }}>
          <Scale size={18} style={{ opacity: 0.5, marginBottom: 6 }} />
          <div>Pick your books and aim the sheet — a topic, or a chapter/section to start from — then write it.</div>
          <div style={{ fontSize: "0.72rem", marginTop: 4 }}>Every bullet is taken from the pages you chose, with its [Book, Page] reference; figures on those pages come along.</div>
        </div>
      )}
    </div>
  );
}