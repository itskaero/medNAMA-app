"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy, Loader2, Share2, Swords, Trophy, XCircle } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";
import { parseMarkdown } from "@/utils/markdown";

interface DuelQuestion {
  id: number;
  question_text: string;
  options: Record<string, string>;
  figure_id: number | null;
  correct_option?: string;
  explanation_markdown?: string | null;
  picks?: Record<string, string | undefined>;
}

interface DuelData {
  code: string;
  title: string | null;
  expires_at: string;
  played: boolean;
  total: number;
  players: { username: string; score: number; time_ms: number | null; is_you: boolean }[];
  questions: DuelQuestion[];
}

const SUBJECTS = ["Mixed", "Anatomy", "Physiology", "Pathology", "Pharmacology", "Microbiology", "Medicine", "Surgery", "ENT"];

const duelLink = (code: string) => (typeof window !== "undefined" ? `${window.location.origin}/?duel=${code}` : `/?duel=${code}`);

function shareOnWhatsApp(text: string) {
  window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank", "noopener");
}

export default function DuelView({ token, initialCode }: { token: string | null; initialCode?: string | null }) {
  const [code, setCode] = useState<string | null>(initialCode || null);
  const [joinInput, setJoinInput] = useState("");
  const [subject, setSubject] = useState("Mixed");
  const [creating, setCreating] = useState(false);
  const [duel, setDuel] = useState<DuelData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [idx, setIdx] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const startedAt = useRef<number | null>(null);

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    fetch(`${API}/api/duels/${encodeURIComponent(code)}`, { headers: headers(), credentials: "include" })
      .then(async (res) => {
        const body = await res.json().catch(() => null);
        if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
        return body as DuelData;
      })
      .then((data) => {
        if (cancelled) return;
        setDuel(data);
        setError(null);
        setIdx(0);
        setAnswers({});
        startedAt.current = Date.now();
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [code, headers]);

  const create = async () => {
    setCreating(true);
    try {
      const res = await fetch(`${API}/api/duels`, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ subject: subject === "Mixed" ? null : subject, count: 10 }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
      setCode(body.code);
    } catch (e) {
      toast.error(`Could not create the duel: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setCreating(false);
    }
  };

  const submit = async () => {
    if (!duel) return;
    setSubmitting(true);
    try {
      const res = await fetch(`${API}/api/duels/${duel.code}/submit`, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ answers, time_ms: startedAt.current ? Date.now() - startedAt.current : null }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
      setDuel(body);
    } catch (e) {
      toast.error(`Could not submit: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSubmitting(false);
    }
  };

  const copyLink = async () => {
    if (!duel) return;
    try {
      await navigator.clipboard.writeText(duelLink(duel.code));
      toast.success("Duel link copied.");
    } catch {
      toast.error("Could not copy; select the link manually.");
    }
  };

  // ── home: create or join ───────────────────────────────────────────────
  if (!code) {
    return (
      <div className="dashboard-view" style={{ maxWidth: "640px", margin: "0 auto" }}>
        <div className="dashboard-header">
          <h1 className="dashboard-title" style={{ display: "flex", gap: "8px", alignItems: "center" }}><Swords size={20} /> Challenge a friend</h1>
          <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--text-secondary)" }}>
            10 questions, same for both of you. Share the link (WhatsApp works well), each plays once, then compare answers and explanations.
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          <label style={{ fontSize: "0.74rem", color: "var(--text-muted)" }}>Subject</label>
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            {SUBJECTS.map((s) => (
              <button key={s} className="btn-workspace" onClick={() => setSubject(s)}
                style={{ fontSize: "0.74rem", padding: "3px 10px", borderColor: subject === s ? "var(--sky)" : undefined, color: subject === s ? "var(--sky)" : undefined }}>
                {s}
              </button>
            ))}
          </div>
          <button className="btn-workspace" onClick={create} disabled={creating} style={{ alignSelf: "flex-start", display: "flex", gap: "6px", alignItems: "center" }}>
            {creating ? <Loader2 size={13} className="animate-spin" /> : <Swords size={13} />} Create a duel
          </button>
          <div style={{ borderTop: "1px solid var(--border)", marginTop: "10px", paddingTop: "10px", display: "flex", gap: "6px", alignItems: "center" }}>
            <input value={joinInput} onChange={(e) => setJoinInput(e.target.value.trim())} placeholder="Have a code or link? Paste it here"
              style={{ flex: 1, padding: "6px 8px", borderRadius: "8px", border: "1px solid var(--border-light)", background: "var(--surface-3)", color: "var(--text-primary)" }} />
            <button className="btn-workspace" disabled={!joinInput} onClick={() => setCode(joinInput.split("duel=").pop() || joinInput)}>Join</button>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="dashboard-view" style={{ maxWidth: "640px", margin: "0 auto" }}>
        <p style={{ color: "var(--error)" }}>{error}</p>
        <button className="btn-workspace" onClick={() => { setCode(null); setError(null); }}>Back</button>
      </div>
    );
  }
  if (!duel) {
    return <div className="dashboard-view" style={{ color: "var(--text-muted)" }}><Loader2 size={14} className="animate-spin" /> Loading duel…</div>;
  }

  // ── results ────────────────────────────────────────────────────────────
  if (duel.played) {
    const me = duel.players.find((p) => p.is_you);
    return (
      <div className="dashboard-view" style={{ maxWidth: "760px", margin: "0 auto" }}>
        <div className="dashboard-header">
          <h1 className="dashboard-title" style={{ display: "flex", gap: "8px", alignItems: "center" }}><Trophy size={20} style={{ color: "#f59e0b" }} /> {duel.title}</h1>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginBottom: "var(--sp-4)" }}>
          {duel.players.map((p, i) => (
            <div key={p.username} style={{ display: "flex", justifyContent: "space-between", padding: "8px 12px", borderRadius: "10px", border: `1px solid ${p.is_you ? "var(--sky)" : "var(--border)"}` }}>
              <span>{i === 0 ? "🥇 " : i === 1 ? "🥈 " : ""}{p.username}{p.is_you ? " (you)" : ""}</span>
              <span style={{ fontWeight: 700 }}>{p.score}/{duel.total}{p.time_ms ? <span style={{ color: "var(--text-muted)", fontWeight: 400 }}> · {Math.round(p.time_ms / 1000)}s</span> : null}</span>
            </div>
          ))}
          {duel.players.length < 2 ? <div style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>Waiting for a friend to play — share the link below.</div> : null}
        </div>
        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "var(--sp-4)" }}>
          <button className="btn-workspace" onClick={copyLink}><Copy size={12} /> Copy link</button>
          <button className="btn-workspace" onClick={() => shareOnWhatsApp(`I scored ${me?.score ?? "?"}/${duel.total} on this medNAMA FCPS duel (${duel.title}). Can you beat me? ${duelLink(duel.code)}`)}>
            <Share2 size={12} /> Share on WhatsApp
          </button>
          <button className="btn-workspace" onClick={() => { setCode(null); setDuel(null); }}>New duel</button>
        </div>
        {duel.questions.map((q, i) => {
          const mine = me ? q.picks?.[me.username] : undefined;
          const right = mine === q.correct_option;
          return (
            <details key={q.id} style={{ border: "1px solid var(--border)", borderRadius: "10px", padding: "8px 12px", marginBottom: "6px" }}>
              <summary style={{ cursor: "pointer", display: "flex", gap: "6px", alignItems: "center", fontSize: "0.86rem" }}>
                {right ? <CheckCircle2 size={14} style={{ color: "var(--sea-green)" }} /> : <XCircle size={14} style={{ color: "var(--error)" }} />}
                Q{i + 1}. {q.question_text.slice(0, 110)}{q.question_text.length > 110 ? "…" : ""}
              </summary>
              <div style={{ marginTop: "8px", fontSize: "0.82rem", display: "flex", flexDirection: "column", gap: "6px" }}>
                <div>{q.question_text}</div>
                {Object.keys(q.options).sort().map((k) => (
                  <div key={k} style={{ color: k === q.correct_option ? "var(--sea-green)" : "var(--text-secondary)", fontWeight: k === q.correct_option ? 700 : 400 }}>
                    {k}. {q.options[k]}
                    {Object.entries(q.picks || {}).filter(([, v]) => v === k).map(([u]) => (
                      <span key={u} className="model-chip-pill" style={{ marginLeft: "6px", fontSize: "0.66rem" }}>{u}</span>
                    ))}
                  </div>
                ))}
                {q.explanation_markdown ? <div className="prose" dangerouslySetInnerHTML={{ __html: parseMarkdown(q.explanation_markdown) }} /> : null}
              </div>
            </details>
          );
        })}
      </div>
    );
  }

  // ── play ───────────────────────────────────────────────────────────────
  const q = duel.questions[idx];
  const answered = Object.keys(answers).length;
  return (
    <div className="dashboard-view" style={{ maxWidth: "760px", margin: "0 auto" }}>
      <div className="dashboard-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "8px" }}>
        <h1 className="dashboard-title" style={{ display: "flex", gap: "8px", alignItems: "center" }}><Swords size={20} /> {duel.title}</h1>
        <div style={{ display: "flex", gap: "6px" }}>
          <button className="btn-workspace" onClick={copyLink}><Copy size={12} /> Copy link</button>
          <button className="btn-workspace" onClick={() => shareOnWhatsApp(`FCPS duel on medNAMA — ${duel.title}. Same 10 questions, can you beat me? ${duelLink(duel.code)}`)}>
            <Share2 size={12} /> Invite on WhatsApp
          </button>
        </div>
      </div>
      <div style={{ fontSize: "0.74rem", color: "var(--text-muted)", marginBottom: "8px" }}>Question {idx + 1} of {duel.total} · {answered} answered · answers are revealed after you submit</div>
      {q ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {q.figure_id ? <img src={`${API}/api/figures/${q.figure_id}?token=${token ?? ""}`} alt="Question figure" style={{ maxHeight: "300px", objectFit: "contain" }} /> : null}
          <p style={{ fontSize: "1rem", lineHeight: 1.6, margin: 0 }}>{q.question_text}</p>
          {Object.keys(q.options).sort().map((k) => (
            <button key={k} type="button" onClick={() => setAnswers((a) => ({ ...a, [String(q.id)]: k }))}
              style={{
                display: "flex", gap: "10px", textAlign: "left", padding: "10px 12px", borderRadius: "10px", cursor: "pointer",
                border: `1px solid ${answers[String(q.id)] === k ? "var(--sky)" : "var(--border-light)"}`,
                background: answers[String(q.id)] === k ? "rgba(48,197,255,0.08)" : "var(--surface-3)", color: "var(--text-primary)",
              }}>
              <span className="option-badge">{k}</span><span>{q.options[k]}</span>
            </button>
          ))}
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: "6px" }}>
            <button className="btn-workspace" disabled={idx === 0} onClick={() => setIdx((i) => i - 1)}>Previous</button>
            {idx + 1 < duel.total ? (
              <button className="btn-workspace" onClick={() => setIdx((i) => i + 1)}>Next</button>
            ) : (
              <button className="btn-workspace" disabled={submitting} onClick={submit} style={{ borderColor: "var(--sky)", color: "var(--sky)" }}>
                {submitting ? <Loader2 size={12} className="animate-spin" /> : null} Submit ({answered}/{duel.total})
              </button>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
