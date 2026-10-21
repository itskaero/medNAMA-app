"use client";

import React, { useState } from "react";
import { X } from "lucide-react";
import { toast } from "sonner";
import { api } from "~/lib/api";
import type { Profile, StageId } from "~/lib/types";

const MINUTES = [30, 60, 120];

/** The one-time setup: which CPSP stage, when, where you are in training, and how long a day you can study. */
export default function Setup({ profile, onSaved, onClose, onSignOut }: {
  profile: Profile;
  /** shown in the dialog so a phone (no sidebar) can sign out too */
  onSignOut?: () => void;
  onSaved: (p: Profile) => void;
  onClose: () => void;
}) {
  const [stage, setStage] = useState<StageId>(profile.stage ?? "imm");
  const [examDate, setExamDate] = useState(profile.exam_date ?? "");
  const [year, setYear] = useState<number | null>(profile.training_year ?? null);
  const [rotation, setRotation] = useState<string | null>(profile.rotation ?? null);
  const [minutes, setMinutes] = useState(profile.minutes_per_day || 60);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const today = new Date().toISOString().slice(0, 10);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await api<Profile>("/api/paeds/profile", {
        method: "PUT",
        body: JSON.stringify({ stage, exam_date: examDate || null, training_year: year, rotation, minutes_per_day: minutes }),
      });
      toast.success("Saved");
      onSaved({ ...profile, ...p });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal aria-label="Your preparation">
      <div className="modal-panel pn-setup" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">Your preparation</span>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close"><X size={14} /></button>
        </div>
        <div className="pn-setup-body">
          {error ? <div className="error-banner" role="alert">{error}</div> : null}
          <span className="pn-label">Exam</span>
          <div className="segmented" role="radiogroup" aria-label="Exam">
            {(profile.stages ?? [{ id: "imm", title: "IMM" }, { id: "fcps2", title: "FCPS-II" }]).map((s) => (
              <button key={s.id} type="button" role="radio" aria-checked={stage === s.id}
                className={`segmented-item ${stage === s.id ? "active" : ""}`} onClick={() => setStage(s.id)}>
                {s.id === "imm" ? "IMM" : "FCPS-II"}
              </button>
            ))}
          </div>
          <span className="pn-label">Exam date (if you know it)</span>
          <input type="date" className="field-input-new" min={today} value={examDate} onChange={(e) => setExamDate(e.target.value)} />
          <span className="pn-label">Training year</span>
          <div className="pn-row">
            {[1, 2, 3, 4].map((y) => (
              <button key={y} type="button" className={`practice-chip ${year === y ? "active" : ""}`} onClick={() => setYear(y)}>
                Year {y}
              </button>
            ))}
          </div>
          <span className="pn-label">Current rotation</span>
          <div className="pn-row">
            {(profile.rotations ?? []).map((r) => (
              <button key={r} type="button" className={`practice-chip ${rotation === r ? "active" : ""}`} onClick={() => setRotation(r)}>
                {r}
              </button>
            ))}
          </div>
          <span className="pn-label">Study time on a typical day</span>
          <div className="segmented" role="radiogroup" aria-label="Minutes a day">
            {MINUTES.map((m) => (
              <button key={m} type="button" role="radio" aria-checked={minutes === m}
                className={`segmented-item ${minutes === m ? "active" : ""}`} onClick={() => setMinutes(m)}>
                {m === 120 ? "2 hours+" : m === 60 ? "1 hour" : "30 min"}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
            <span style={{ display: "flex", gap: 8 }}>
              <button type="button" className="btn-workspace" onClick={onClose}>Later</button>
              {onSignOut ? <button type="button" className="btn-workspace" onClick={onSignOut}>Sign out</button> : null}
            </span>
            <button type="button" className="btn-primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
