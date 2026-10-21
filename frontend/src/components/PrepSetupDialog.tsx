"use client";

import React, { useMemo, useState } from "react";
import { X } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

export interface PrepProfile {
  exam_level: string | null;
  exam_date: string | null;
  exam_id: string | null;
  paper_id: string | null;
  paper_title: string | null;
  minutes_per_day: number | null;
  onboarded: boolean;
  needs_onboarding: boolean;
  needs_paper: boolean;
  clinical: boolean;
  options?: { id: string; short_name: string; title: string;
    papers: { id: string; title: string; category?: string; available: boolean }[] }[];
  other_levels?: { id: string; label: string }[];
  minutes_choices?: number[];
}

// The programme the sidebar already knows -> the exam whose papers to offer first.
const LEVEL_EXAM: Record<string, string> = { PGET: "pget", "FCPS-I": "fcps1", MBBS: "mbbs" };
const MINUTE_LABEL: Record<number, string> = { 30: "30 min", 60: "1 hour", 120: "2 hours+" };

/** Three short steps that shape the whole app: which paper, when, and how long a day.
 *  New learners see it once; learners who already chose an exam only confirm their paper ("confirm"). */
export default function PrepSetupDialog({ profile, getHeaders, confirm = false, onSaved, onClose }: {
  profile: PrepProfile;
  getHeaders: () => HeadersInit;
  /** an existing learner: only the paper is asked, the rest keeps its defaults */
  confirm?: boolean;
  onSaved: (p: PrepProfile) => void;
  onClose: () => void;
}) {
  const options = useMemo(() => profile.options ?? [], [profile.options]);
  const [step, setStep] = useState(0);
  const [examId, setExamId] = useState<string | null>(
    profile.exam_id ?? (profile.exam_level ? LEVEL_EXAM[profile.exam_level] ?? null : options[0]?.id ?? null));
  // An exam without a Table of Specifications here (FCPS-II, MD, MS, USMLE): no paper to choose.
  const [otherLevel, setOtherLevel] = useState<string | null>(
    !profile.exam_id && profile.exam_level && !LEVEL_EXAM[profile.exam_level] ? profile.exam_level : null);
  const [paperId, setPaperId] = useState<string | null>(profile.paper_id);
  const [examDate, setExamDate] = useState(profile.exam_date ?? "");
  const [minutes, setMinutes] = useState(profile.minutes_per_day ?? 60);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const exam = options.find((e) => e.id === examId) ?? null;
  const papers = (exam?.papers ?? []).filter((p) => p.available);
  const stepOk = step === 0 ? Boolean(otherLevel || (examId && paperId)) : true;
  const last = confirm || step === 2;
  const today = new Date().toISOString().slice(0, 10);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API}/api/prep/profile`, {
        method: "PUT", credentials: "include",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify(otherLevel
          ? { exam_id: null, exam_level: otherLevel, exam_date: examDate || null, minutes_per_day: minutes }
          : { exam_id: examId, paper_id: paperId, exam_date: examDate || null, minutes_per_day: minutes }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Couldn't save your plan.");
      toast.success("Your plan is set");
      onSaved({ ...profile, ...d });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save your plan.");
    } finally {
      setBusy(false);
    }
  };

  const title = confirm ? `Which ${profile.exam_level ?? ""} paper are you preparing for?` : [
    "What are you preparing for?", "When is your exam?", "How long can you study on a typical day?"][step];

  return (
    <div className="modal-backdrop" role="dialog" aria-modal aria-label="Set up your preparation">
      <div className="modal-panel prep-setup" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">{title}</span>
          <button className="modal-close-btn" onClick={onClose} aria-label="Later"><X size={14} /></button>
        </div>
        <div className="prep-setup-body">
          {!confirm ? (
            <div className="prep-steps" aria-label={`Step ${step + 1} of 3`}>
              {[0, 1, 2].map((i) => <span key={i} className={i <= step ? "on" : ""} />)}
            </div>
          ) : null}
          {error ? <div className="error-banner" role="alert">{error}</div> : null}

          {step === 0 ? (
            <>
              <div className="prep-row">
                {options.map((e) => (
                  <button key={e.id} type="button" className={`practice-chip ${!otherLevel && examId === e.id ? "active" : ""}`}
                    title={e.title} onClick={() => { setExamId(e.id); setOtherLevel(null); setPaperId(null); }}>
                    {e.short_name}
                  </button>
                ))}
                {!confirm ? (profile.other_levels ?? []).map((l) => (
                  <button key={l.id} type="button" className={`practice-chip ${otherLevel === l.id ? "active" : ""}`}
                    onClick={() => { setOtherLevel(l.id); setPaperId(null); }}>
                    {l.id}
                  </button>
                )) : null}
              </div>
              {otherLevel ? (
                <p className="prep-note">
                  There is no official blueprint for {otherLevel} here yet, so your plan uses reviews, questions and
                  {otherLevel === "FCPS-II" ? " TOACS / viva stations." : " revision."}
                </p>
              ) : exam ? (
                <div className="prep-papers" role="radiogroup" aria-label={`${exam.short_name} papers`}>
                  {papers.map((p) => (
                    <button key={p.id} type="button" role="radio" aria-checked={paperId === p.id}
                      className={`prep-paper ${paperId === p.id ? "active" : ""}`} onClick={() => setPaperId(p.id)}>
                      <span>{p.title}</span>
                      {p.category ? <small>{p.category}</small> : null}
                    </button>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}

          {step === 1 ? (
            <>
              <input type="date" className="field-input-new" min={today} value={examDate}
                onChange={(e) => setExamDate(e.target.value)} aria-label="Exam date" />
              <p className="prep-note">Today uses it to pace your topics week by week. You can leave it empty and add it later.</p>
            </>
          ) : null}

          {step === 2 ? (
            <>
              <div className="segmented" role="radiogroup" aria-label="Minutes a day">
                {(profile.minutes_choices ?? [30, 60, 120]).map((m) => (
                  <button key={m} type="button" role="radio" aria-checked={minutes === m}
                    className={`segmented-item ${minutes === m ? "active" : ""}`} onClick={() => setMinutes(m)}>
                    {MINUTE_LABEL[m] ?? `${m} min`}
                  </button>
                ))}
              </div>
              <p className="prep-note">Roughly one topic per 40 minutes. Today suggests that many each day, never more.</p>
            </>
          ) : null}

          <div className="prep-actions">
            {step > 0 && !confirm ? (
              <button type="button" className="btn-workspace" onClick={() => setStep(step - 1)}>Back</button>
            ) : (
              <button type="button" className="btn-workspace" onClick={onClose}>Later</button>
            )}
            <button type="button" className="btn-primary" disabled={!stepOk || busy}
              onClick={() => (last ? save() : setStep(step + 1))}>
              {busy ? "Saving…" : last ? "Save my plan" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
