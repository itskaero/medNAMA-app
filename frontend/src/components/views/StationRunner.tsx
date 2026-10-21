"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Flag, Lightbulb, Loader2, Send, ShieldAlert, Stethoscope, Timer } from "lucide-react";
import { toast } from "sonner";
import PageShell from "@/components/layout/PageShell";
import SessionSummary, { type NextAction } from "@/components/SessionSummary";
import { CONFIDENCE_LABEL, type Confidence } from "@/components/QuestionPlayer";
import { API } from "@/lib/constants";
import type { AppLinks } from "@/lib/nav";
import { parseMarkdown } from "@/utils/markdown";

interface Turn { role: "examiner" | "candidate"; text: string; item?: number; confidence?: string; follow_up?: boolean; hint_after?: boolean }
interface Attempt {
  id: number; station_id: number; title: string; mode: "guided" | "timed"; persona: string; pressure: string;
  state: "active" | "submitted" | "scoring_pending" | "scored"; transcript: Turn[]; hints_used: number;
  deadline: string | null; server_now: string; time_left: number | null; time_limit_s: number;
  instructions: string; items_total: number; item: number; readiness: string;
}
interface CriterionResult {
  score_id: number; criterion_id: number; competency: string; expected: string; alternatives: string[]; safety_critical: boolean;
  marks_available: number; marks_awarded: number; evidence: string | null; rationale: string; uncertainty: "none" | "low" | "high";
  flagged: boolean; prompted: boolean; unsafe: boolean; error_class: string | null;
  source: { book_title?: string; page_number?: number | null; quote?: string };
}
interface Debrief {
  attempt_id: number; station_id: number; title: string; mode: string; state: string; readiness: string;
  marks_awarded: number | null; marks_available: number | null; result_note: string | null; hints_used: number;
  competencies: { competency: string; awarded: number; available: number }[];
  criteria: CriterionResult[]; safety_findings: CriterionResult[]; error_classes: Record<string, number>;
  model_answer: string; sources: { book_title: string; page_number: number | null }[]; transcript: Turn[];
  learn: { kind: string; label: string; exam_id?: string; paper_id?: string; topic_id?: string; after_days?: number }[];
  repeat_after_days: number; practice_only: boolean;
}

const ERROR_LABEL: Record<string, string> = {
  knowledge_gap: "Knowledge gap", reasoning: "Reasoning", prioritisation: "Prioritisation", communication: "Communication",
  practical: "Practical steps", safety: "Patient safety", time: "Time management",
};
const fmtClock = (s: number) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.floor(Math.max(0, s) % 60)).padStart(2, "0")}`;

/** One TOACS / viva attempt: the examiner conversation, then the debrief (backend app/routers/stations.py). */
interface Circuit {
  id: number; stations: { index: number; title: string; attempt_id: number | null; state: string; marks_awarded: number | null; marks_available: number | null }[];
  next_index: number | null; done: boolean; marks_awarded: number; marks_available: number; note: string;
}

export default function StationRunner({ token, attemptId, circuitId, onExit, onOpenAttempt, links }: {
  token: string | null;
  attemptId: number;
  /** Set when this station is one of a circuit's (backend /api/station-circuits). */
  circuitId?: number | null;
  onExit: () => void;
  onOpenAttempt?: (attemptId: number, circuitId?: number) => void;
  links?: AppLinks;
}) {
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [debrief, setDebrief] = useState<Debrief | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [circuit, setCircuit] = useState<Circuit | null>(null);
  const [nextBusy, setNextBusy] = useState(false);
  const [text, setText] = useState("");
  const [confidence, setConfidence] = useState<Confidence | undefined>();
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [clockOffset, setClockOffset] = useState(0);
  const submitted = useRef(false);
  const endRef = useRef<HTMLDivElement | null>(null);
  const draftKey = `mednama.station.draft.${attemptId}`;

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
  }, [token]);
  const call = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const r = await fetch(`${API}${path}`, { ...init, headers: headers(), credentials: "include" });
    const body = await r.json().catch(() => null);
    if (!r.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${r.status})`);
    return body as T;
  }, [headers]);

  // The circuit's progress, once this station has been submitted
  useEffect(() => {
    if (!circuitId || !debrief) return;
    call<Circuit>(`/api/station-circuits/${circuitId}`).then(setCircuit).catch(() => setCircuit(null));
  }, [circuitId, debrief, call]);

  const nextStation = async () => {
    if (!circuitId) return;
    setNextBusy(true);
    try {
      const r = await call<Circuit & { attempt_id: number | null }>(`/api/station-circuits/${circuitId}/next`, { method: "POST" });
      if (r.attempt_id && onOpenAttempt) onOpenAttempt(r.attempt_id, circuitId);
      else setCircuit(r);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not start the next station");
    } finally {
      setNextBusy(false);
    }
  };

  const loadDebrief = useCallback(() => {
    call<Debrief>(`/api/station-attempts/${attemptId}/debrief`).then(setDebrief).catch((e) => setError(e.message));
  }, [call, attemptId]);

  // Load (or resume after a refresh / lost connection)
  useEffect(() => {
    call<Attempt>(`/api/station-attempts/${attemptId}`)
      .then((a) => {
        setClockOffset(new Date(a.server_now + "Z").getTime() - Date.now());
        setAttempt(a);
        setDone(a.item >= a.items_total);
        if (a.state !== "active") loadDebrief();
        try { setText(localStorage.getItem(draftKey) || ""); } catch { /* storage unavailable */ }
      })
      .catch((e) => setError(e.message));
  }, [call, attemptId, draftKey, loadDebrief]);

  // Keep the typed answer if the page is closed mid-sentence
  useEffect(() => {
    try { if (text) localStorage.setItem(draftKey, text); else localStorage.removeItem(draftKey); } catch { /* ignore */ }
  }, [text, draftKey]);

  // Interruptions are recorded, never used to pause or extend the clock (revision.md §18)
  useEffect(() => {
    if (!attempt || attempt.state !== "active") return;
    const send = (event: string) => {
      fetch(`${API}/api/station-attempts/${attemptId}/events`, { method: "POST", headers: headers(), credentials: "include",
        body: JSON.stringify({ event }) }).catch(() => undefined);
    };
    const vis = () => send(document.hidden ? "hidden" : "visible");
    const off = () => send("offline");
    const on = () => send("online");
    document.addEventListener("visibilitychange", vis);
    window.addEventListener("offline", off);
    window.addEventListener("online", on);
    return () => { document.removeEventListener("visibilitychange", vis); window.removeEventListener("offline", off); window.removeEventListener("online", on); };
  }, [attempt, attemptId, headers]);

  const submit = useCallback(async () => {
    if (submitted.current) return;
    submitted.current = true;
    setSubmitting(true);
    try {
      const d = await call<Debrief>(`/api/station-attempts/${attemptId}/submit`, { method: "POST" });
      setDebrief(d);
      setAttempt((a) => (a ? { ...a, state: d.state as Attempt["state"] } : a));
      try { localStorage.removeItem(draftKey); } catch { /* ignore */ }
    } catch (e) {
      submitted.current = false;
      toast.error(`${e instanceof Error ? e.message : "Could not submit"}. Your answers are saved; try again.`);
    } finally {
      setSubmitting(false);
    }
  }, [call, attemptId, draftKey]);

  // Timed: server-clock countdown, submitted automatically at zero (never extended)
  const deadline = attempt?.deadline ? new Date(attempt.deadline + "Z").getTime() : null;
  const submitRef = useRef(submit);
  useEffect(() => { submitRef.current = submit; }, [submit]);
  useEffect(() => {
    if (!deadline || attempt?.state !== "active") return;
    const id = setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (deadline - (t + clockOffset) <= 0) {
        clearInterval(id);
        toast.warning("Time is up: your station has been submitted.");
        submitRef.current();
      }
    }, 1000);
    return () => clearInterval(id);
  }, [deadline, attempt?.state, clockOffset]);
  const remaining = deadline ? (deadline - (now + clockOffset)) / 1000 : null;

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [attempt?.transcript.length]);

  const send = async (hint = false) => {
    if (!attempt) return;
    const answer = text.trim() || (hint ? "I am not sure." : "");
    if (!answer) return;
    setSending(true);
    try {
      const r = await call<{ ended: boolean; done?: boolean; attempt?: Attempt }>(`/api/station-attempts/${attemptId}/turn`, {
        method: "POST", body: JSON.stringify({ text: answer, confidence: confidence || "sure", hint }),
      });
      if (r.ended) {
        toast.warning("Time was up before that answer: the station has been submitted.");
        submit();
        return;
      }
      if (r.attempt) setAttempt(r.attempt);
      if (r.done) setDone(true);
      setText("");
      setConfidence(undefined);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not send your answer");
    } finally {
      setSending(false);
    }
  };

  const challenge = async (c: CriterionResult) => {
    const rationale = window.prompt(`Why should "${c.expected}" get more marks? Quote what you said or give a source.`);
    if (!rationale || rationale.trim().length < 10) return;
    try {
      await call(`/api/station-attempts/${attemptId}/challenges`, { method: "POST", body: JSON.stringify({ score_id: c.score_id, rationale }) });
      toast.success("Sent for review. The original mark is kept until a reviewer decides.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not send the challenge");
    }
  };

  if (error) return <PageShell title="Station" icon={<Stethoscope size={22} />} error={error} onRetry={onExit} />;
  if (!attempt) return <PageShell title="Station" icon={<Stethoscope size={22} />} loading />;

  // ── Debrief ──
  if (attempt.state !== "active") {
    if (!debrief) return <PageShell title={attempt.title} icon={<Stethoscope size={22} />} loading />;
    if (debrief.state !== "scored") {
      return (
        <PageShell title={debrief.title} icon={<Stethoscope size={22} />}>
          <div className="tos-note warn">Your answers are saved. The AI marker is not available right now, so marking is pending.
            <button type="button" className="practice-link" style={{ marginLeft: 8 }} onClick={loadDebrief}>Try marking again</button></div>
        </PageShell>
      );
    }
    const actions: NextAction[] = [];
    for (const l of debrief.learn) {
      if (l.kind === "notes" && links) actions.push({ label: l.label, icon: <BookOpen size={12} />, onClick: () => links.go("tos") });
      if (l.kind === "repeat") actions.push({ label: `Repeat in ${l.after_days} days`, onClick: onExit, hint: "Come back to this station later to check it has stuck" });
    }
    const moreInCircuit = !!(circuit && circuit.next_index !== null);
    if (moreInCircuit) actions.unshift({ label: `Next station (${(circuit!.next_index ?? 0) + 1} of ${circuit!.stations.length})`, primary: true, onClick: nextStation });
    actions.push({ label: "Back to stations", primary: !actions.length, onClick: onExit });
    return (
      <PageShell title={debrief.title} icon={<Stethoscope size={22} />} width="wide">
        <SessionSummary
          kicker={<>{debrief.mode === "timed" ? "Timed station" : "Guided station"}{debrief.hints_used ? ` · ${debrief.hints_used} hint${debrief.hints_used > 1 ? "s" : ""}` : ""}</>}
          title="Debrief"
          right={debrief.marks_awarded ?? 0}
          total={debrief.marks_available ?? 0}
          note={debrief.result_note}
          stats={debrief.competencies.map((c) => ({ label: c.competency, value: `${c.awarded}/${c.available}` }))}
          actions={actions}
        />
        {circuit ? (
          <section className="tos-card" aria-label="Circuit">
            <div className="tos-row-between">
              <span className="tile-kicker">Circuit · station {debrief ? circuit.stations.findIndex((x) => x.attempt_id === attemptId) + 1 : 1} of {circuit.stations.length}</span>
              {circuit.done && circuit.marks_available ? (
                <b>Circuit result: {circuit.marks_awarded}/{circuit.marks_available} ({Math.round((100 * circuit.marks_awarded) / circuit.marks_available)}%)</b>
              ) : null}
            </div>
            <div className="practice-picker-row">
              {circuit.stations.map((x) => (
                <span key={x.index} className={`tos-tag ${x.attempt_id === attemptId ? "hit" : ""}`}>
                  {x.index + 1}. {x.title}{x.marks_available ? ` · ${x.marks_awarded}/${x.marks_available}` : x.state === "not_started" ? " · next" : ""}
                </span>
              ))}
            </div>
            {circuit.next_index !== null ? (
              <button type="button" className="btn-primary" style={{ alignSelf: "flex-start", padding: "8px 16px" }} disabled={nextBusy} onClick={nextStation}>
                {nextBusy ? <Loader2 size={13} className="animate-spin" /> : <Timer size={13} />} Next station
              </button>
            ) : null}
            <p className="tile-text" style={{ margin: 0 }}>{circuit.note}</p>
          </section>
        ) : null}
        {debrief.practice_only ? (
          <div className="tos-note warn">This station&apos;s AI marking has not yet been checked against an expert examiner. Use the marks as a guide, and challenge any you disagree with.</div>
        ) : null}
        {debrief.safety_findings.length ? (
          <section className="tos-card" aria-label="Safety findings">
            <span className="tile-kicker" style={{ color: "var(--error)" }}><ShieldAlert size={12} style={{ verticalAlign: -2 }} /> Patient safety</span>
            {debrief.safety_findings.map((c) => (
              <p key={c.score_id} className="tile-text" style={{ margin: 0 }}>
                <b>{c.unsafe ? "Unsafe action: " : "Safety point missed: "}</b>{c.expected}{c.evidence ? <> · you said &ldquo;{c.evidence}&rdquo;</> : null}
              </p>
            ))}
          </section>
        ) : null}
        {Object.keys(debrief.error_classes).length ? (
          <div className="practice-picker-row">
            <span className="practice-picker-label">Where marks were lost</span>
            {Object.entries(debrief.error_classes).map(([k, n]) => <span key={k} className="tos-tag">{ERROR_LABEL[k] || k} · {n}</span>)}
          </div>
        ) : null}
        <section className="tos-card" aria-label="Marks by criterion">
          <span className="tile-kicker">Marks by criterion</span>
          {debrief.criteria.map((c) => (
            <div key={c.score_id} className="viva-crit">
              <div className="tos-row-between">
                <b>{c.expected}</b>
                <span className="practice-picker-row">
                  {c.safety_critical ? <span className="qp-tag bad">safety-critical</span> : null}
                  {c.prompted ? <span className="qp-tag warn" title="Earned after a hint: credit is capped">after a hint</span> : null}
                  {c.flagged ? <span className="qp-tag warn" title="The marker was not sure; a reviewer may look at it">uncertain</span> : null}
                  <span className={`qp-tag ${c.marks_awarded >= c.marks_available ? "ok" : c.marks_awarded > 0 ? "" : "bad"}`}>{c.marks_awarded}/{c.marks_available}</span>
                </span>
              </div>
              <span className="tile-text">{c.rationale}{c.evidence ? <> · <i>&ldquo;{c.evidence}&rdquo;</i></> : null}</span>
              {c.alternatives?.length ? <span className="tile-text">Also accepted: {c.alternatives.join("; ")}</span> : null}
              <span className="practice-picker-row">
                {c.source?.book_title ? <span className="tos-tag"><BookOpen size={11} /> {c.source.book_title} · p. {c.source.page_number ?? "?"}</span> : null}
                <button type="button" className="practice-link" onClick={() => challenge(c)}><Flag size={11} /> Challenge this mark</button>
              </span>
            </div>
          ))}
        </section>
        <section className="tos-card" aria-label="Model answer">
          <span className="tile-kicker">A strong answer</span>
          <div className="prose tos-prose" dangerouslySetInnerHTML={{ __html: parseMarkdown(debrief.model_answer || "") }} />
        </section>
      </PageShell>
    );
  }

  // ── Sitting the station ──
  const low = remaining !== null && remaining < 60;
  const canSend = !!text.trim() && !!confidence && !sending;
  return (
    <PageShell title={attempt.title} icon={<Stethoscope size={22} />} width="default"
      subtitle={`${attempt.mode === "timed" ? "Timed" : "Guided"} · ${attempt.persona === "tutor" ? "supportive tutor" : attempt.persona === "strict" ? "strict examiner" : "examiner"} · question ${Math.min(attempt.item + 1, attempt.items_total)} of ${attempt.items_total}`}
      actions={remaining !== null ? <span className={`tos-timer ${low ? "low" : ""}`}><Timer size={15} /> {fmtClock(remaining)}</span> : null}>
      {attempt.instructions ? <div className="tos-note">{attempt.instructions}</div> : null}
      <div className="viva-thread" aria-live="polite">
        {attempt.transcript.map((t, i) => (
          <div key={i} className={`viva-turn ${t.role}`}>
            <span className="viva-who">{t.role === "examiner" ? "Examiner" : "You"}{t.role === "candidate" && t.confidence ? ` · ${CONFIDENCE_LABEL[t.confidence as Confidence] || t.confidence}` : ""}</span>
            <div className="viva-text">{t.text}</div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      {done ? (
        <div className="tos-launch">
          <div className="tos-launch-text"><b>The examiner has finished.</b><span>Submit for your debrief, or add anything you forgot first.</span></div>
          <button type="button" className="btn-primary" disabled={submitting} onClick={submit}>
            {submitting ? <Loader2 size={14} className="animate-spin" /> : null} Submit for debrief
          </button>
        </div>
      ) : null}
      <div className="viva-input">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={4000}
          placeholder="Answer as you would to the examiner…" aria-label="Your answer"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && canSend) send(); }} />
        <div className="tos-row-between">
          <div className="qp-confidence">
            <span className="lbl">How sure are you?</span>
            {(Object.keys(CONFIDENCE_LABEL) as Confidence[]).map((c) => (
              <button key={c} type="button" className={`qp-pill ${confidence === c ? "active" : ""}`} onClick={() => setConfidence(c)}>{CONFIDENCE_LABEL[c]}</button>
            ))}
          </div>
          <div className="page-actions">
            {attempt.mode === "guided" ? (
              <button type="button" className="btn-workspace" disabled={sending} onClick={() => send(true)} title="A nudge from the examiner; marks earned after a hint are capped">
                <Lightbulb size={13} /> Hint
              </button>
            ) : null}
            <button type="button" className="btn-workspace" disabled={submitting} onClick={() => {
              if (window.confirm(done ? "Submit for your debrief?" : "Finish early and submit what you have answered?")) submit();
            }}>Submit</button>
            <button type="button" className="btn-primary" disabled={!canSend} onClick={() => send()} title={confidence ? "Ctrl+Enter" : "Choose how sure you are first"}>
              {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Answer
            </button>
          </div>
        </div>
      </div>
    </PageShell>
  );
}
