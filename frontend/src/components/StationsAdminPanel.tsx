"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Loader2, Stethoscope } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

interface AdminStation {
  id: number; title: string; specialty: string; status: string; readiness: string; topic: string | null;
  latest_version: number | null; latest_approved: boolean; flagged_criteria: number; open_challenges: number; calibrated: boolean;
}
interface Challenge {
  id: number; station: string; attempt_id: number; rationale: string; source: string | null; criterion: string | null;
  marks_available: number; marks_awarded: number; evidence: string | null; ai_rationale: string; created_at: string;
}

/** Admin > Content: publish or retire TOACS stations and settle learners' score challenges. Stations are drafted,
 *  edited, approved and calibrated in Seed Studio on the PC (labs/seed_studio, "TOACS stations"). */
export default function StationsAdminPanel({ getHeaders }: { getHeaders: () => HeadersInit }) {
  const [stations, setStations] = useState<AdminStation[] | null>(null);
  const [challenges, setChallenges] = useState<Challenge[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState<Record<number, { marks: string; note: string; gold: boolean }>>({});

  const call = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const r = await fetch(`${API}${path}`, { ...init, headers: { "Content-Type": "application/json", ...getHeaders() }, credentials: "include" });
    const body = await r.json().catch(() => null);
    if (!r.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${r.status})`);
    return body as T;
  }, [getHeaders]);

  const load = useCallback(() => {
    call<AdminStation[]>("/api/admin/stations").then(setStations).catch(() => setStations([]));
    call<Challenge[]>("/api/admin/station-challenges?status=open").then(setChallenges).catch(() => setChallenges([]));
  }, [call]);
  useEffect(() => { load(); }, [load]);

  const act = async (key: string, path: string, body: unknown, ok: string) => {
    setBusy(key);
    try {
      await call(path, { method: "POST", body: JSON.stringify(body) });
      toast.success(ok);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(null);
    }
  };

  const f = (id: number) => form[id] || { marks: "", note: "", gold: false };
  const setF = (id: number, patch: Partial<{ marks: string; note: string; gold: boolean }>) => setForm((cur) => ({ ...cur, [id]: { ...f(id), ...patch } }));

  return (
    <section className="tos-card" aria-label="TOACS stations" style={{ marginTop: "var(--sp-5)" }}>
      <div>
        <span className="tile-kicker"><Stethoscope size={12} style={{ verticalAlign: -1 }} /> TOACS / viva stations</span>
        <h2 className="tos-h2">Publication and score challenges</h2>
        <p className="tile-text">Draft, edit, approve and calibrate stations in Seed Studio → TOACS stations. Here you publish or retire them and settle learners&apos; challenges.</p>
      </div>
      {stations === null ? (
        <div className="page-state"><Loader2 size={16} className="animate-spin" /> Loading…</div>
      ) : !stations.length ? (
        <div className="page-state">No stations yet.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="users-table">
            <thead><tr><th>Station</th><th>Status</th><th>Version</th><th>Flags</th><th>Challenges</th><th></th></tr></thead>
            <tbody>
              {stations.map((s) => (
                <tr key={s.id}>
                  <td><b>{s.title}</b><div className="tile-text">{s.specialty}{s.topic ? ` · ${s.topic}` : ""}</div></td>
                  <td>{s.status}{s.status === "published" ? <div className="tile-text">{s.readiness === "assessment_ready" ? "marking validated" : "practice only"}</div> : null}</td>
                  <td>v{s.latest_version}{s.latest_approved ? " ✓" : " (unapproved)"}</td>
                  <td>{s.flagged_criteria || "–"}</td>
                  <td>{s.open_challenges || "–"}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {s.status !== "published" && s.latest_approved && s.status !== "retired" ? (
                      <button type="button" className="btn-workspace tos-btn-sm" disabled={busy !== null}
                        onClick={() => act(`p${s.id}`, `/api/admin/stations/${s.id}/publish`, {}, "Published")}>Publish</button>
                    ) : null}
                    {s.status === "published" ? (
                      <button type="button" className="btn-workspace tos-btn-sm" disabled={busy !== null}
                        onClick={() => act(`u${s.id}`, `/api/admin/stations/${s.id}/status`, { status: "approved" }, "Unpublished")}>Unpublish</button>
                    ) : null}
                    {s.status !== "retired" ? (
                      <button type="button" className="btn-workspace tos-btn-sm" disabled={busy !== null}
                        onClick={() => window.confirm("Retire this station? Learners will no longer see it; past attempts are kept.") &&
                          act(`r${s.id}`, `/api/admin/stations/${s.id}/status`, { status: "retired" }, "Retired")}>Retire</button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <span className="tile-kicker" style={{ marginTop: 8 }}>Open challenges ({challenges.length})</span>
      {!challenges.length ? <div className="tile-text">No open challenges.</div> : challenges.map((c) => (
        <div key={c.id} className="viva-crit">
          <b>{c.station}: {c.criterion}</b>
          <span className="tile-text">Awarded {c.marks_awarded}/{c.marks_available}. Marker: {c.ai_rationale}{c.evidence ? ` · quoted “${c.evidence}”` : ""}</span>
          <span className="tile-text">Learner: {c.rationale}{c.source ? ` (source: ${c.source})` : ""}</span>
          <div className="practice-picker-row">
            <input type="number" min={0} max={c.marks_available} step={0.5} placeholder="Corrected marks" aria-label="Corrected marks"
              value={f(c.id).marks} onChange={(e) => setF(c.id, { marks: e.target.value })}
              style={{ width: 130, padding: "6px 8px", borderRadius: 8, border: "1px solid var(--border-light)", background: "var(--surface-2)", color: "var(--text-primary)" }} />
            <input type="text" placeholder="Your reason (required)" aria-label="Reviewer note" value={f(c.id).note}
              onChange={(e) => setF(c.id, { note: e.target.value })}
              style={{ flex: "1 1 220px", padding: "6px 8px", borderRadius: 8, border: "1px solid var(--border-light)", background: "var(--surface-2)", color: "var(--text-primary)" }} />
            <label className="tile-text" style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
              <input type="checkbox" checked={f(c.id).gold} onChange={(e) => setF(c.id, { gold: e.target.checked })} /> use as a gold answer
            </label>
            <button type="button" className="btn-workspace tos-btn-sm" disabled={busy !== null || f(c.id).note.trim().length < 3 || f(c.id).marks === ""}
              onClick={() => act(`c${c.id}`, `/api/admin/station-challenges/${c.id}`,
                { decision: "upheld", corrected_marks: Number(f(c.id).marks), note: f(c.id).note, promote_to_gold: f(c.id).gold }, "Challenge upheld")}>Uphold</button>
            <button type="button" className="btn-workspace tos-btn-sm" disabled={busy !== null || f(c.id).note.trim().length < 3}
              onClick={() => act(`c${c.id}`, `/api/admin/station-challenges/${c.id}`, { decision: "rejected", note: f(c.id).note }, "Challenge rejected")}>Reject</button>
          </div>
        </div>
      ))}
    </section>
  );
}
