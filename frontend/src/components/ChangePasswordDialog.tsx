"use client";

import React, { useState } from "react";
import { X } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

/** Change your own password (opened from the sidebar). */
export default function ChangePasswordDialog({ getHeaders, onClose }: { getHeaders: () => HeadersInit; onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next !== again) { setError("The two new passwords don't match."); return; }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API}/api/auth/change-password`, {
        method: "POST", credentials: "include",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d.detail || "Couldn't change the password.");
      toast.success("Password changed");
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't change the password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal aria-label="Change password">
      <div className="modal-panel" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "420px" }}>
        <div className="modal-header">
          <span className="modal-title">Change password</span>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close"><X size={14} /></button>
        </div>
        <form onSubmit={submit} style={{ padding: "var(--sp-5)", display: "flex", flexDirection: "column", gap: "12px" }}>
          {error ? <div className="error-banner" role="alert">{error}</div> : null}
          <label className="field-label" htmlFor="pw-current">Current password</label>
          <input id="pw-current" type="password" className="field-input-new" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
          <label className="field-label" htmlFor="pw-new">New password (6+ characters)</label>
          <input id="pw-new" type="password" className="field-input-new" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={6} required />
          <label className="field-label" htmlFor="pw-again">New password again</label>
          <input id="pw-again" type="password" className="field-input-new" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" minLength={6} required />
          <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Change password"}</button>
        </form>
      </div>
    </div>
  );
}
