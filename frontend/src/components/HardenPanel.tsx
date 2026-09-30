"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowRight, Check, Loader2, Play, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

/** One seed's progress, as app/hardening.py reports it. */
interface HardenItem { seed_id: number; n?: number; label: string; stage: string; reason: string | null }
interface HardenProgress { total: number; done: number; kept: number; dropped: Record<string, number>; items: HardenItem[] }
interface HardenJob {
  job_id: string; status: "running" | "done" | "failed"; progress?: HardenProgress | null; elapsed_s?: number;
  partial?: boolean; detail?: string;
  result?: { quiz_set_id: string; quiz_set_title: string; total_questions: number; difficulty: number };
}
export interface HardenPreview { buckets: { label: string; available: number; picked: number }[]; total: number; estimate_min: [number, number] }
export type HardenLevel = 0 | 4 | 5;   // 0 = the questions as written

export const HARDEN_STORAGE_KEY = "harden_job";
const STORAGE_KEY = HARDEN_STORAGE_KEY;
const POLL_MS = 3_000;
const MAX_WAIT_MS = 90 * 60_000;   // 50 questions can take over an hour on the NAS
export const HARDEN_MAX_QUESTIONS = 50;
const MAX_BUCKETS = 20;   // a whole category ticks all its subtopics; 'All' is the only thing refused
const STAGE: Record<string, string> = {
  queued: "Waiting", searching: "Searching textbooks", writing: "Writing", refereeing: "Checking with the Referee",
};
const REASON: Record<string, string> = {
  "answer changed": "changed the answer", duplicate: "too close to an existing question", shape: "malformed question",
  unverified: "couldn't be verified", "referee contradicted": "textbooks contradict its key",
  "referee books_conflict": "textbooks disagree", "referee supported": "textbooks back a different option", error: "error",
};
const reason = (r: string | null) => (r ? REASON[r] ?? r : "");
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** Why AI hardening can't be offered for this setup (null = it can). */
export function hardenBlockReason(categories: string[], subCategories: string[], numQuestions: number): string | null {
  if (numQuestions > HARDEN_MAX_QUESTIONS) return `AI difficulty is available for sessions of ${HARDEN_MAX_QUESTIONS} questions or fewer.`;
  const picks = subCategories.length ? subCategories : categories;
  if (picks.length === 0) return "AI difficulty needs a focus: pick a category, subjects or topics on the Topics step (not Mixed Practice / All).";
  if (picks.length > MAX_BUCKETS) return `AI difficulty works on up to ${MAX_BUCKETS} subjects or topics (${picks.length} picked).`;
  return null;
}

/** How a harden request would split across the picked subjects/topics, and roughly how long it takes (no AI call). */
export function useHardenPreview(getHeaders: () => HeadersInit, request: Record<string, unknown> | null) {
  const [preview, setPreview] = useState<HardenPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = request ? JSON.stringify(request) : "";
  useEffect(() => {
    if (!key) { setPreview(null); setError(null); return; }
    let cancelled = false;
    const t = setTimeout(() => {
      fetch(`${API}/api/chat/harden/preview`, {
        method: "POST", headers: { ...getHeaders(), "Content-Type": "application/json" }, credentials: "include", body: key,
      }).then(async (r) => {
        const body = await r.json().catch(() => null);
        if (cancelled) return;
        if (r.ok) { setPreview(body); setError(null); } else { setPreview(null); setError(body?.detail || `HTTP ${r.status}`); }
      }).catch(() => !cancelled && setError("Couldn't reach the server."));
    }, 400);
    return () => { cancelled = true; clearTimeout(t); };
    // key captures the request
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { preview, error };
}

export const previewLine = (p: HardenPreview, wanted: number) =>
  `${p.total} question${p.total === 1 ? "" : "s"}: ${p.buckets.filter((b) => b.picked).map((b) => `${b.picked} ${b.label}`).join(" · ")}`
  + ` · about ${p.estimate_min[0]}–${p.estimate_min[1]} min to prepare${p.total < wanted ? ` (only ${p.total} available)` : ""}`;

/** Mock Builder, Review step with an AI difficulty chosen: prepare harder versions of questions from the selection
 *  (same fact and answer, harder statement and options), show each question's progress, then start the session.
 *  The job keeps running if the page is left or reloaded; coming back resumes the panel. */
export default function HardenPanel({
  getHeaders,
  categories,
  subCategories,
  numQuestions,
  difficulty,
  onStart,
  onSaved,
  scope,
}: {
  getHeaders: () => HeadersInit;
  categories: string[];
  subCategories: string[];
  /** A Practice selection (subjects/topics); used instead of categories when given. */
  scope?: Record<string, unknown> | null;
  numQuestions: number;
  difficulty: 4 | 5;
  onStart: (quizSetId: string) => void;
  onSaved?: () => void;
}) {
  const [job, setJob] = useState<HardenJob | null>(null);
  const [starting, setStarting] = useState(false);
  const polling = useRef<ReturnType<typeof setTimeout> | null>(null);

  const blocked = scope
    ? (numQuestions > HARDEN_MAX_QUESTIONS ? `AI difficulty is available for sessions of ${HARDEN_MAX_QUESTIONS} questions or fewer.` : null)
    : hardenBlockReason(categories, subCategories, numQuestions);
  const picks = subCategories.length ? subCategories : categories;
  const request = scope ? { scope, num_questions: numQuestions, difficulty } : { categories: categories.length ? categories : undefined,
    sub_categories: subCategories.length ? subCategories : undefined, num_questions: numQuestions, difficulty };
  const { preview, error: previewError } = useHardenPreview(getHeaders, blocked || job ? null : request);

  const stopPolling = () => { if (polling.current) clearTimeout(polling.current); polling.current = null; };
  const forget = () => { stopPolling(); try { localStorage.removeItem(STORAGE_KEY); } catch {} };

  const poll = useCallback((jobId: string, startedAt: number) => {
    stopPolling();
    const tick = async () => {
      let r: Response;
      try {
        r = await fetch(`${API}/api/chat/harden/jobs/${encodeURIComponent(jobId)}`, { headers: getHeaders(), credentials: "include" });
      } catch {
        polling.current = setTimeout(tick, POLL_MS * 3);   // network blip: keep waiting
        return;
      }
      if (r.status === 404) { forget(); setJob(null); return; }
      if (!r.ok) { polling.current = setTimeout(tick, POLL_MS * 2); return; }   // server busy: keep waiting
      const body = (await r.json()) as HardenJob;
      setJob(body);
      if (body.status === "done") {
        forget();
        onSaved?.();
        toast.success("Harder versions ready", {
          description: `${body.result?.total_questions} rewrites (difficulty ${body.result?.difficulty}/5) are ready and saved to Quiz History.`,
        });
      } else if (body.status === "failed") {
        forget();
      } else if (Date.now() - startedAt < MAX_WAIT_MS) {
        polling.current = setTimeout(tick, POLL_MS);
      }
    };
    tick();
    // onSaved/getHeaders are stable enough for a poll loop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A job started before a reload (or while on another page) picks up where it is.
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (saved?.jobId) { setJob({ job_id: saved.jobId, status: "running" }); poll(saved.jobId, saved.startedAt || Date.now()); }
    } catch {}
    return stopPolling;
  }, [poll]);

  const prepare = async () => {
    if (blocked || starting) return;
    setStarting(true);
    try {
      const requestId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const r = await fetch(`${API}/api/chat/harden/jobs`, {
        method: "POST", headers: { ...getHeaders(), "Content-Type": "application/json" }, credentials: "include",
        body: JSON.stringify({ ...request, request_id: requestId, label: picks.join(", ") }),
      });
      const body = await r.json().catch(() => null);
      if (!r.ok || !body?.job_id) throw new Error(body?.detail || `HTTP ${r.status}`);
      const startedAt = Date.now();
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ jobId: body.job_id, startedAt, difficulty })); } catch {}
      setJob({ job_id: body.job_id, status: "running" });
      poll(body.job_id, startedAt);
    } catch (e) {
      toast.error(`Could not start: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setStarting(false);
    }
  };

  const running = job?.status === "running";
  const p = job?.progress;
  const pctDone = p && p.total ? Math.round((p.done / p.total) * 100) : 0;

  return (
    <div style={{ border: "1px dashed var(--teal)", borderRadius: "12px", padding: "12px 14px", background: "rgba(76, 217, 100, 0.05)" }}>
      <div style={{ fontSize: "0.84rem", fontWeight: 700, marginBottom: "4px" }}>
        Harder versions · difficulty {difficulty}/5 {difficulty === 4 ? "(multi-step reasoning)" : "(deep integration)"}
      </div>
      <p style={{ fontSize: "0.76rem", color: "var(--text-secondary)", margin: "0 0 10px", lineHeight: 1.45 }}>
        The AI rewrites questions from your selection with the same fact and correct answer but a harder statement and options,
        checks each against your textbooks, then starts the session with your rules. Questions you already got right come first.
      </p>

      {blocked ? (
        <div role="alert" style={{ display: "flex", gap: "6px", alignItems: "flex-start", fontSize: "0.76rem", color: "#b45309",
          background: "rgba(245,158,11,0.10)", borderRadius: "8px", padding: "8px 10px" }}>
          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: "1px" }} /> {blocked}
        </div>
      ) : null}

      {!job && !blocked ? (
        <>
          {preview ? <div style={{ fontSize: "0.74rem", color: "var(--text-secondary)", marginBottom: "8px" }}>{previewLine(preview, numQuestions)}</div> : null}
          {previewError ? <div style={{ fontSize: "0.74rem", color: "var(--error)", marginBottom: "8px" }}>{previewError}</div> : null}
          <button type="button" className="btn-primary" disabled={starting || !!previewError} onClick={prepare} style={{ padding: "9px 20px" }}>
            {starting ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}&nbsp;Prepare harder versions
          </button>
        </>
      ) : null}

      {running ? (
        <div aria-live="polite">
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.76rem", color: "var(--text-secondary)", marginBottom: "4px" }}>
            <span>{p ? <>Checked <b>{p.done}</b> of {p.total} · <b>{p.kept}</b> kept</> : "Picking questions…"}</span>
            <span>{job?.elapsed_s != null ? mmss(job.elapsed_s) : ""}</span>
          </div>
          <div style={{ height: "6px", background: "var(--surface-3)", borderRadius: "3px" }}>
            <div style={{ width: `${pctDone}%`, height: "100%", background: "var(--teal)", borderRadius: "3px", transition: "width 0.4s" }} />
          </div>
          {p ? (
            <ul style={{ listStyle: "none", margin: "10px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: "4px", maxHeight: "280px", overflowY: "auto" }}>
              {p.items.map((it, i) => (
                <li key={it.seed_id} style={{ display: "flex", gap: "8px", alignItems: "flex-start", fontSize: "0.74rem" }}>
                  <span style={{ width: "14px", flexShrink: 0, marginTop: "1px" }}>
                    {it.stage === "kept" ? <Check size={13} style={{ color: "var(--sea-green)" }} />
                      : it.stage === "dropped" ? <X size={13} style={{ color: "var(--text-muted)" }} />
                      : it.stage === "queued" ? null : <Loader2 size={12} className="animate-spin" />}
                  </span>
                  <span style={{ flex: 1, minWidth: 0, color: it.stage === "dropped" ? "var(--text-muted)" : "var(--text-primary)" }}>
                    {/* Number and topic only: the original statement would spoil its harder version. */}
                    <b>Question {it.n ?? i + 1}</b>
                    <span style={{ color: "var(--text-muted)" }}>{it.label ? ` · ${it.label}` : ""}</span>
                    <span style={{ display: "block", color: "var(--text-muted)" }}>
                      {it.stage === "kept" ? "Kept" : it.stage === "dropped" ? `Dropped: ${reason(it.reason)}` : STAGE[it.stage] ?? it.stage}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <div style={{ display: "flex", gap: "8px", marginTop: "10px", flexWrap: "wrap", alignItems: "center" }}>
            {p && p.kept > 0 ? (
              <button type="button" className="btn-workspace" style={{ padding: "5px 12px", fontSize: "0.76rem" }}
                onClick={() => job && onStart(job.job_id)}>
                <Play size={12} /> Start now with the {p.kept} ready
              </button>
            ) : null}
            <span style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
              Keeps running if you leave this page; come back to see progress.
            </span>
          </div>
        </div>
      ) : null}

      {job?.status === "failed" ? (
        <div style={{ fontSize: "0.76rem", color: "var(--error)" }}>
          {job.detail || "The job failed."}{" "}
          <button type="button" className="btn-workspace" style={{ padding: "3px 10px", fontSize: "0.74rem", marginLeft: "6px" }} onClick={() => setJob(null)}>
            Try again
          </button>
        </div>
      ) : null}

      {job?.status === "done" && job.result ? (
        <div>
          <div style={{ fontSize: "0.76rem", color: "var(--text-secondary)", marginBottom: "8px" }}>
            <b style={{ color: "var(--teal)" }}>{job.result.total_questions} harder questions ready</b> · difficulty {job.result.difficulty}/5
            {job.progress && job.progress.total > job.progress.kept ? ` · ${job.progress.total - job.progress.kept} dropped by the checks` : ""}
            {job.partial ? " · what was saved before the server restarted" : ""} · also in Quiz History
          </div>
          <button type="button" className="btn-primary" onClick={() => onStart(job.result!.quiz_set_id)} style={{ padding: "9px 22px" }}>
            Start practice exam <ArrowRight size={15} style={{ marginLeft: "4px" }} />
          </button>
        </div>
      ) : null}
    </div>
  );
}
