"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Layers, Loader2, Play, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

/** One seed's progress, as app/hardening.py reports it. */
interface HardenItem { seed_id: number; label: string; stem: string; stage: string; reason: string | null }
interface HardenProgress { total: number; done: number; kept: number; dropped: Record<string, number>; items: HardenItem[] }
interface HardenJob {
  job_id: string; status: "running" | "done" | "failed"; progress?: HardenProgress | null; elapsed_s?: number;
  partial?: boolean; detail?: string;
  result?: { quiz_set_id: string; quiz_set_title: string; total_questions: number; difficulty: number };
}
interface Preview { buckets: { label: string; available: number; picked: number }[]; total: number; estimate_min: [number, number] }

const STORAGE_KEY = "harden_job";
const POLL_MS = 3_000;
const MAX_WAIT_MS = 30 * 60_000;   // a 20-question set takes 10-30 min on the NAS
const MAX_BUCKETS = 6;
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

/** Mock Builder's "Harder versions (AI)": rewrite questions from the current selection into harder ones (same fact
 *  and answer), with a preview of the split, live per-question progress, and a job that survives a reload. */
export default function HardenPanel({
  getHeaders,
  categories,
  subCategories,
  numQuestions,
  onPractice,
  onSaved,
}: {
  getHeaders: () => HeadersInit;
  categories: string[];
  subCategories: string[];
  numQuestions: number;
  onPractice: (quizSetId: string) => void;
  onSaved?: () => void;
}) {
  const [difficulty, setDifficulty] = useState<4 | 5>(4);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [job, setJob] = useState<HardenJob | null>(null);
  const [starting, setStarting] = useState(false);
  const polling = useRef<ReturnType<typeof setTimeout> | null>(null);

  const count = Math.max(5, Math.min(20, Math.round(numQuestions / 5) * 5));
  const buckets = subCategories.length ? subCategories : categories;
  const focusProblem = buckets.length === 0
    ? "Hardening needs a focus: pick 1–6 subjects or topics above (it isn't available for Mixed Practice / All)."
    : buckets.length > MAX_BUCKETS ? `Pick at most ${MAX_BUCKETS} subjects or topics to harden (${buckets.length} selected).` : null;
  const request = { categories: categories.length ? categories : undefined,
    sub_categories: subCategories.length ? subCategories : undefined, num_questions: count, difficulty };

  // What the job would pick, and how long it takes (no AI call).
  const requestKey = JSON.stringify(request);
  useEffect(() => {
    if (focusProblem) { setPreview(null); return; }
    let cancelled = false;
    const t = setTimeout(() => {
      fetch(`${API}/api/chat/harden/preview`, {
        method: "POST", headers: { ...getHeaders(), "Content-Type": "application/json" }, credentials: "include",
        body: requestKey,
      }).then(async (r) => {
        const body = await r.json().catch(() => null);
        if (cancelled) return;
        if (r.ok) { setPreview(body); setPreviewError(null); } else { setPreview(null); setPreviewError(body?.detail || `HTTP ${r.status}`); }
      }).catch(() => !cancelled && setPreviewError("Couldn't reach the server."));
    }, 400);
    return () => { cancelled = true; clearTimeout(t); };
    // requestKey captures the selection, count and difficulty
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey, focusProblem]);

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
          description: `${body.result?.total_questions} rewrites saved to Quiz History (difficulty ${body.result?.difficulty}/5).`,
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

  // A job started before a reload (or while on another tab) picks up where it is.
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (saved?.jobId) { setJob({ job_id: saved.jobId, status: "running" }); poll(saved.jobId, saved.startedAt || Date.now()); }
    } catch {}
    return stopPolling;
  }, [poll]);

  const start = async () => {
    if (focusProblem || starting) return;
    setStarting(true);
    try {
      const requestId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const r = await fetch(`${API}/api/chat/harden/jobs`, {
        method: "POST", headers: { ...getHeaders(), "Content-Type": "application/json" }, credentials: "include",
        body: JSON.stringify({ ...request, request_id: requestId, label: buckets.join(", ") }),
      });
      const body = await r.json().catch(() => null);
      if (!r.ok || !body?.job_id) throw new Error(body?.detail || `HTTP ${r.status}`);
      const startedAt = Date.now();
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ jobId: body.job_id, startedAt })); } catch {}
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
    <div style={{ marginTop: "16px", border: "1px dashed var(--teal)", borderRadius: "12px", padding: "12px 14px", background: "rgba(76, 217, 100, 0.05)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
        <Layers size={14} style={{ color: "var(--teal)" }} />
        <strong style={{ fontSize: "0.84rem" }}>Harder versions (AI)</strong>
      </div>
      <p style={{ fontSize: "0.76rem", color: "var(--text-secondary)", margin: "0 0 10px", lineHeight: 1.45 }}>
        Rewrites questions from your selection with the same fact and correct answer but harder statements and options.
        Questions you already got right come first, spread evenly across what you picked.
      </p>

      {focusProblem ? (
        <div role="alert" style={{ display: "flex", gap: "6px", alignItems: "flex-start", fontSize: "0.76rem", color: "#b45309",
          background: "rgba(245,158,11,0.10)", borderRadius: "8px", padding: "8px 10px", marginBottom: "10px" }}>
          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: "1px" }} /> {focusProblem}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
        {([4, 5] as const).map((d) => (
          <button key={d} type="button" className={`config-grade-chip ${difficulty === d ? "active" : ""}`}
            onClick={() => setDifficulty(d)} disabled={running} title={`Target difficulty ${d}/5`}>
            {d === 4 ? "4 · Hard" : "5 · Brutal"}
          </button>
        ))}
        <button type="button" className="btn-workspace" disabled={!!focusProblem || running || starting} onClick={start}
          style={{ padding: "7px 14px", fontSize: "0.78rem" }}>
          {running || starting ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
          {running ? "Rewriting…" : "Generate harder versions"}
        </button>
      </div>

      {!running && !focusProblem && preview ? (
        <div style={{ marginTop: "8px", fontSize: "0.74rem", color: "var(--text-secondary)" }}>
          {preview.total} question{preview.total === 1 ? "" : "s"}: {preview.buckets.filter((b) => b.picked).map((b) => `${b.picked} ${b.label}`).join(" · ")}
          {" · "}about {preview.estimate_min[0]}–{preview.estimate_min[1]} min
          {preview.total < count ? ` (only ${preview.total} available)` : ""}
        </div>
      ) : null}
      {!running && !focusProblem && previewError ? (
        <div style={{ marginTop: "8px", fontSize: "0.74rem", color: "var(--error)" }}>{previewError}</div>
      ) : null}

      {running ? (
        <div style={{ marginTop: "12px" }} aria-live="polite">
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.76rem", color: "var(--text-secondary)", marginBottom: "4px" }}>
            <span>{p ? <>Checked <b>{p.done}</b> of {p.total} · <b>{p.kept}</b> kept</> : "Picking questions…"}</span>
            <span>{job?.elapsed_s != null ? mmss(job.elapsed_s) : ""}</span>
          </div>
          <div style={{ height: "6px", background: "var(--surface-3)", borderRadius: "3px" }}>
            <div style={{ width: `${pctDone}%`, height: "100%", background: "var(--teal)", borderRadius: "3px", transition: "width 0.4s" }} />
          </div>
          {p ? (
            <ul style={{ listStyle: "none", margin: "10px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: "4px", maxHeight: "260px", overflowY: "auto" }}>
              {p.items.map((it) => (
                <li key={it.seed_id} style={{ display: "flex", gap: "8px", alignItems: "flex-start", fontSize: "0.74rem" }}>
                  <span style={{ width: "14px", flexShrink: 0, marginTop: "1px" }}>
                    {it.stage === "kept" ? <Check size={13} style={{ color: "var(--sea-green)" }} />
                      : it.stage === "dropped" ? <X size={13} style={{ color: "var(--text-muted)" }} />
                      : it.stage === "queued" ? null : <Loader2 size={12} className="animate-spin" />}
                  </span>
                  <span style={{ flex: 1, minWidth: 0, color: it.stage === "dropped" ? "var(--text-muted)" : "var(--text-primary)" }}>
                    <span style={{ color: "var(--text-muted)" }}>{it.label ? `${it.label} · ` : ""}</span>{it.stem}
                    <span style={{ display: "block", color: "var(--text-muted)" }}>
                      {it.stage === "kept" ? "Kept" : it.stage === "dropped" ? `Dropped: ${reason(it.reason)}` : STAGE[it.stage] ?? it.stage}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <div style={{ display: "flex", gap: "8px", marginTop: "10px", flexWrap: "wrap" }}>
            {p && p.kept > 0 ? (
              <button type="button" className="btn-workspace" style={{ padding: "5px 12px", fontSize: "0.76rem" }}
                onClick={() => job && onPractice(job.job_id)}>
                <Play size={12} /> Open the {p.kept} ready so far
              </button>
            ) : null}
            <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", alignSelf: "center" }}>
              Keeps running if you leave this page; come back to see progress.
            </span>
          </div>
        </div>
      ) : null}

      {job?.status === "failed" ? (
        <div style={{ marginTop: "10px", fontSize: "0.76rem", color: "var(--error)" }}>{job.detail || "The job failed."}</div>
      ) : null}

      {job?.status === "done" && job.result ? (
        <div style={{ marginTop: "12px", padding: "10px 12px", border: "1px solid var(--border-light)", borderRadius: "10px", background: "var(--surface-1)" }}>
          <div style={{ fontSize: "0.76rem", color: "var(--text-secondary)", marginBottom: "8px" }}>
            <b style={{ color: "var(--teal)" }}>{job.result.total_questions} harder MCQs</b> · difficulty {job.result.difficulty}/5
            {job.progress ? ` · ${job.progress.total - job.progress.kept} dropped by the checks` : ""}
            {job.partial ? " · what was saved before the server restarted" : ""} · saved to Quiz History
          </div>
          <button type="button" className="btn-primary" onClick={() => onPractice(job.result!.quiz_set_id)} style={{ padding: "7px 14px", fontSize: "0.78rem" }}>
            <Play size={13} fill="currentColor" style={{ marginRight: "6px" }} /> Practice harder versions
          </button>
        </div>
      ) : null}
    </div>
  );
}
