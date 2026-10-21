"use client";

import React, { useCallback, useEffect, useState } from "react";
import { History, Loader2, Mic, ShieldAlert, Stethoscope, Timer } from "lucide-react";
import { toast } from "sonner";
import PageShell from "@/components/layout/PageShell";
import { API } from "@/lib/constants";

interface StationItem {
  id: number;
  title: string;
  specialty: string;
  station_type: string;
  readiness: "practice_only" | "assessment_ready";
  topic: string | null;
  time_limit_s: number | null;
  difficulty: number | null;
  last_attempt?: { id: number; state: string; mode: string; percent: number | null; at: string; repeat_due: string | null };
  suggested?: boolean;
}
interface Level { level: number; reason: string; recent: number }
interface AttemptRow { id: number; station_id: number; title: string; mode: string; state: string; marks_awarded: number | null; marks_available: number | null; at: string }

const MODES = [
  { id: "guided", label: "Guided", hint: "Hints and short teaching after each answer. Untimed. Best for learning." },
  { id: "timed", label: "Timed", hint: "Exam conditions: the clock runs, no hints, no feedback until you submit." },
] as const;
const PERSONAS = [
  { id: "tutor", label: "Supportive tutor" },
  { id: "examiner", label: "Examiner" },
  { id: "strict", label: "Strict examiner" },
] as const;
const PRESSURES = [
  { id: "calm", label: "Calm" },
  { id: "realistic", label: "Realistic" },
  { id: "high", label: "High pressure" },
] as const;

/** Practise > TOACS / viva: the station library and how to sit a station (backend app/routers/stations.py). */
export default function ToacsView({ token, onOpenAttempt }: {
  token: string | null;
  /** Open an attempt; `circuitId` when it is one station of a circuit. */
  onOpenAttempt: (attemptId: number, circuitId?: number) => void;
}) {
  const [stations, setStations] = useState<StationItem[] | null>(null);
  const [attempts, setAttempts] = useState<AttemptRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"guided" | "timed">("guided");
  const [persona, setPersona] = useState<string>("examiner");
  const [pressure, setPressure] = useState<string>("realistic");
  const [starting, setStarting] = useState<number | null>(null);
  const [levels, setLevels] = useState<Record<string, Level>>({});
  const [circuitNote, setCircuitNote] = useState("");
  const [circuitN, setCircuitN] = useState(4);
  const [circuitBusy, setCircuitBusy] = useState(false);
  const [now] = useState(() => Date.now());

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
  }, [token]);

  useEffect(() => {
    fetch(`${API}/api/stations`, { headers: headers(), credentials: "include" })
      .then(async (r) => (r.ok ? r.json() : Promise.reject(new Error((await r.json().catch(() => null))?.detail || `HTTP ${r.status}`))))
      .then((d) => { setStations(d.stations); setLevels(d.levels || {}); setCircuitNote(d.circuit_note || ""); })
      .catch((e) => setError(e.message));
    fetch(`${API}/api/station-attempts`, { headers: headers(), credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then(setAttempts)
      .catch(() => setAttempts([]));
  }, [headers]);

  const start = async (s: StationItem) => {
    setStarting(s.id);
    try {
      const r = await fetch(`${API}/api/stations/${s.id}/attempts`, {
        method: "POST", headers: headers(), credentials: "include", body: JSON.stringify({ mode, persona, pressure }),
      });
      const body = await r.json().catch(() => null);
      if (!r.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `HTTP ${r.status}`);
      onOpenAttempt(body.id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not start the station");
    } finally {
      setStarting(null);
    }
  };

  const startCircuit = async (specialty: string) => {
    setCircuitBusy(true);
    try {
      const r = await fetch(`${API}/api/station-circuits`, {
        method: "POST", headers: headers(), credentials: "include", body: JSON.stringify({ specialty, n: circuitN, persona, pressure }),
      });
      const body = await r.json().catch(() => null);
      if (!r.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `HTTP ${r.status}`);
      onOpenAttempt(body.attempt_id, body.circuit_id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not start the circuit");
    } finally {
      setCircuitBusy(false);
    }
  };

  const active = attempts.filter((a) => a.state === "active");
  const specialties = [...new Set((stations || []).map((s) => s.specialty))];

  return (
    <PageShell title="TOACS / viva" icon={<Stethoscope size={24} />} width="wide" error={error}
      subtitle="Examiner-led stations: answer one question at a time, as in a viva, then get a criterion-by-criterion debrief.">
      <div className="tos-note"><ShieldAlert size={13} style={{ verticalAlign: -2 }} /> An educational simulation marked by AI against a reviewed rubric.
        It is not an official CPSP assessment and does not predict your result. <b>Paediatrics is the pilot</b>; more specialties follow.</div>

      <section className="tos-card" aria-label="How to sit a station">
        <div className="tos-row-between">
          <div className="practice-picker-row">
            <span className="practice-picker-label">Mode</span>
            <div className="segmented" role="radiogroup">
              {MODES.map((m) => (
                <button key={m.id} type="button" role="radio" aria-checked={mode === m.id} title={m.hint}
                  className={`segmented-item ${mode === m.id ? "active" : ""}`} onClick={() => setMode(m.id)}>{m.label}</button>
              ))}
            </div>
          </div>
          <div className="practice-picker-row">
            <span className="practice-picker-label">Examiner</span>
            <div className="segmented" role="radiogroup">
              {PERSONAS.map((p) => (
                <button key={p.id} type="button" role="radio" aria-checked={persona === p.id}
                  className={`segmented-item ${persona === p.id ? "active" : ""}`} onClick={() => setPersona(p.id)}>{p.label}</button>
              ))}
            </div>
            {mode === "timed" ? (
              <div className="segmented" role="radiogroup" aria-label="Pressure">
                {PRESSURES.map((p) => (
                  <button key={p.id} type="button" role="radio" aria-checked={pressure === p.id}
                    className={`segmented-item ${pressure === p.id ? "active" : ""}`} onClick={() => setPressure(p.id)}>{p.label}</button>
                ))}
              </div>
            ) : null}
          </div>
        </div>
        <p className="tile-text" style={{ margin: 0 }}>{MODES.find((m) => m.id === mode)?.hint} The examiner&apos;s tone changes with the persona; the marking standard does not.</p>
      </section>

      {active.length ? (
        <section className="tos-card" aria-label="Unfinished stations">
          <span className="tile-kicker"><History size={12} style={{ verticalAlign: -1 }} /> Unfinished</span>
          <div className="practice-picker-row">
            {active.map((a) => (
              <button key={a.id} type="button" className="practice-chip" onClick={() => onOpenAttempt(a.id)}>
                {a.title} <span className="practice-chip-count">{a.mode} · continue</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {stations && stations.length ? specialties.map((sp) => {
        const lv = levels[sp];
        const count = stations.filter((s) => s.specialty === sp).length;
        return (
          <section key={sp} className="tos-card" aria-label={`${sp} circuit`}>
            <div className="tos-row-between">
              <div>
                <span className="tile-kicker">{sp} · {count} station{count === 1 ? "" : "s"}</span>
                {lv ? <p className="tile-text" style={{ margin: "4px 0 0" }}>{lv.reason} Stations at that level are marked <b>Suggested</b>; you can pick any.</p> : null}
              </div>
              <div className="practice-picker-row">
                <span className="practice-picker-label">Circuit</span>
                <div className="segmented" role="radiogroup" aria-label="Stations in the circuit">
                  {[2, 3, 4, 5, 6].filter((n) => n <= count).map((n) => (
                    <button key={n} type="button" role="radio" aria-checked={circuitN === n}
                      className={`segmented-item ${circuitN === n ? "active" : ""}`} onClick={() => setCircuitN(n)}>{n}</button>
                  ))}
                </div>
                <button type="button" className="btn-primary" style={{ padding: "8px 16px" }} disabled={circuitBusy || count < 2}
                  title={circuitNote} onClick={() => startCircuit(sp)}>
                  {circuitBusy ? <Loader2 size={13} className="animate-spin" /> : <Timer size={13} />} Start a timed circuit
                </button>
              </div>
            </div>
            <p className="tile-text" style={{ margin: 0 }}>A circuit is {Math.min(circuitN, count)} timed stations back to back, each with its own clock, then a combined result. {circuitNote ? "Provisional: no official station count is used." : ""}</p>
          </section>
        );
      }) : null}

      {stations === null ? (
        <div className="page-state"><Loader2 size={16} className="animate-spin" /> Loading stations…</div>
      ) : !stations.length ? (
        <div className="empty-state-card">
          <Stethoscope size={28} className="empty-icon" />
          <div className="empty-title">No stations published yet</div>
          <div className="empty-desc">Stations appear here once a reviewer has approved and published them.</div>
        </div>
      ) : (
        <div className="tile-grid">
          {stations.map((s) => {
            const due = s.last_attempt?.repeat_due && new Date(s.last_attempt.repeat_due).getTime() <= now;
            return (
              <div key={s.id} className="tile">
                <span className="tile-kicker">
                  {s.specialty} · {s.station_type.replace("_", " ")}{s.time_limit_s ? ` · ${Math.round(s.time_limit_s / 60)} min` : ""}
                </span>
                <span className="tile-title">{s.title}</span>
                {s.topic ? <span className="tile-text">{s.topic}</span> : null}
                <span className="practice-picker-row">
                  <span className={`qp-tag ${s.readiness === "assessment_ready" ? "ok" : "warn"}`}
                    title={s.readiness === "assessment_ready" ? "Its AI marking has been checked against an expert's marks" : "Its AI marking has not yet been checked against an expert: treat scores as a guide"}>
                    {s.readiness === "assessment_ready" ? "Marking validated" : "Practice only"}
                  </span>
                  {s.last_attempt ? (
                    <span className="tos-tag">{s.last_attempt.percent !== null ? `Last: ${s.last_attempt.percent}%` : `Last: ${s.last_attempt.state}`}</span>
                  ) : null}
                  {due ? <span className="qp-tag">Due for a repeat</span> : null}
                  {s.suggested ? <span className="qp-tag ok" title={levels[s.specialty]?.reason}>Suggested</span> : null}
                  {s.difficulty ? <span className="tos-tag" title="Station difficulty, 1 (easier) to 5 (harder)">Level {s.difficulty}</span> : null}
                </span>
                <span className="next-actions">
                  <button type="button" className="btn-primary" style={{ padding: "8px 16px" }} disabled={starting !== null} onClick={() => start(s)}>
                    {starting === s.id ? <Loader2 size={13} className="animate-spin" /> : mode === "timed" ? <Timer size={13} /> : <Mic size={13} />}
                    {mode === "timed" ? " Start timed" : " Start guided"}
                  </button>
                  {s.last_attempt && s.last_attempt.state !== "active" ? (
                    <button type="button" className="btn-workspace" onClick={() => onOpenAttempt(s.last_attempt!.id)}>Last debrief</button>
                  ) : null}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </PageShell>
  );
}
