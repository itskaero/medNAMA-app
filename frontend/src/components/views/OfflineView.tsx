"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Check, CloudOff, CloudUpload, Download, Loader2, Wifi, X } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";
import { parseMarkdown } from "@/utils/markdown";

interface PackQuestion {
  id: number; question_text: string; options: Record<string, string>; correct_option: string;
  subject: string | null; topic: string | null; explanation_markdown: string | null;
}
interface Pack { pack_id: string; created_at: string; questions: PackQuestion[]; pos: number; picks: Record<number, string> }
interface QueuedAnswer { pack_id: string; mcq_id: number; selected_option: string; confidence: string; answered_at: string }

const SIZES = [20, 40, 80];

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: unknown) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    toast.error("This browser would not store the pack (private mode or storage full).");
  }
}

/** Offline pack: download questions with their keys and explanations, answer them with no connection, and
 *  send the answers to medNAMA when back online. Everything waits on this device until it is synced. */
export default function OfflineView({ getHeaders, username }: { getHeaders: () => HeadersInit; username: string | null }) {
  const packKey = `mednama.offline.pack.${username || "me"}`;
  const queueKey = `mednama.offline.queue.${username || "me"}`;
  const [pack, setPack] = useState<Pack | null>(null);
  const [queue, setQueue] = useState<QueuedAnswer[]>([]);
  const [online, setOnline] = useState(true);
  const [size, setSize] = useState(40);
  const [busy, setBusy] = useState<"download" | "sync" | null>(null);

  useEffect(() => {
    setPack(read<Pack | null>(packKey, null));
    setQueue(read<QueuedAnswer[]>(queueKey, []));
    setOnline(typeof navigator === "undefined" ? true : navigator.onLine);
  }, [packKey, queueKey]);

  const savePack = (p: Pack | null) => { setPack(p); write(packKey, p); };
  const saveQueue = (q: QueuedAnswer[]) => { setQueue(q); write(queueKey, q.length ? q : null); };

  const sync = useCallback(async (quiet = false) => {
    const pending = read<QueuedAnswer[]>(queueKey, []);
    if (!pending.length || (typeof navigator !== "undefined" && !navigator.onLine)) return;
    setBusy("sync");
    let left = pending;
    try {
      for (const packId of Array.from(new Set(pending.map((a) => a.pack_id)))) {
        const answers = pending.filter((a) => a.pack_id === packId);
        const res = await fetch(`${API}/api/offline/sync`, {
          method: "POST", credentials: "include",
          headers: { ...getHeaders(), "Content-Type": "application/json" },
          body: JSON.stringify({ pack_id: packId, answers }),
        });
        if (!res.ok) throw new Error(`sync failed (${res.status})`);
        left = left.filter((a) => a.pack_id !== packId);
      }
      if (!quiet) toast.success("Answers sent to medNAMA.");
    } catch {
      if (!quiet) toast.error("Could not reach medNAMA; your answers are kept here and will be sent later.");
    } finally {
      // Keep answers given while the sync ran (they are not in `pending`).
      const now = read<QueuedAnswer[]>(queueKey, []);
      saveQueue([...left, ...now.filter((a) => !pending.some((p) => p.pack_id === a.pack_id && p.mcq_id === a.mcq_id))]);
      setBusy(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [getHeaders, queueKey]);

  useEffect(() => {
    const up = () => { setOnline(true); sync(true); };
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    sync(true);
    return () => { window.removeEventListener("online", up); window.removeEventListener("offline", down); };
  }, [sync]);

  const download = async () => {
    setBusy("download");
    try {
      const res = await fetch(`${API}/api/offline/pack`, {
        method: "POST", credentials: "include",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ num_questions: size }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Download failed (${res.status}).`);
      savePack({ ...data, pos: 0, picks: {} });
      toast.success(`${data.questions.length} questions saved on this device.`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Download failed.");
    } finally {
      setBusy(null);
    }
  };

  const q = pack ? pack.questions[pack.pos] : null;
  const picked = q && pack ? pack.picks[q.id] : undefined;
  const score = useMemo(() => {
    if (!pack) return { answered: 0, right: 0 };
    const ids = Object.keys(pack.picks).map(Number);
    return {
      answered: ids.length,
      right: pack.questions.filter((x) => ids.includes(x.id) && pack.picks[x.id] === x.correct_option).length,
    };
  }, [pack]);

  const choose = (key: string) => {
    if (!pack || !q || picked) return;
    savePack({ ...pack, picks: { ...pack.picks, [q.id]: key } });
    saveQueue([...read<QueuedAnswer[]>(queueKey, []),
      { pack_id: pack.pack_id, mcq_id: q.id, selected_option: key, confidence: "sure", answered_at: new Date().toISOString() }]);
  };
  const go = (d: number) => pack && savePack({ ...pack, pos: Math.max(0, Math.min(pack.questions.length - 1, pack.pos + d)) });

  const secure = typeof window !== "undefined" && window.isSecureContext;

  return (
    <div className="dashboard-view" style={{ maxWidth: "900px", margin: "0 auto", display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
      <div>
        <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "10px" }}><CloudOff size={24} /> Offline pack</h1>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.86rem" }}>
          Download questions while you are connected and answer them anywhere: on the bus, in the ward, with no signal.
          Answers wait on this device and go to your Stats and review schedule when you are back online.
        </p>
      </div>

      <div style={{ padding: "12px 14px", borderRadius: "12px", border: "1px solid var(--border-light)", background: "var(--surface-2, var(--surface-3))", display: "flex", flexDirection: "row", gap: "12px", alignItems: "center", flexWrap: "wrap", fontSize: "0.82rem" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: "5px", color: online ? "var(--sea-green)" : "#d97706", fontWeight: 600 }}>
          {online ? <Wifi size={14} /> : <CloudOff size={14} />} {online ? "Online" : "Offline"}
        </span>
        <span style={{ color: "var(--text-secondary)" }}>
          {queue.length ? `${queue.length} answer${queue.length === 1 ? "" : "s"} waiting to be sent` : "All answers sent"}
        </span>
        {queue.length ? (
          <button className="btn-workspace" disabled={!online || busy !== null} onClick={() => sync()}>
            {busy === "sync" ? <Loader2 size={12} className="animate-spin" /> : <CloudUpload size={12} />} Send now
          </button>
        ) : null}
        <span style={{ flex: 1 }} />
        <span style={{ color: "var(--text-muted)" }}>New pack:</span>
        {SIZES.map((n) => (
          <button key={n} className={`practice-chip ${size === n ? "active" : ""}`} onClick={() => setSize(n)}>{n}</button>
        ))}
        <button className="btn-primary" disabled={!online || busy !== null} onClick={download} style={{ padding: "7px 14px" }}>
          {busy === "download" ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}&nbsp;Download
        </button>
      </div>
      {!secure ? (
        <div style={{ fontSize: "0.74rem", color: "var(--text-muted)" }}>
          This address is not https, so the browser cannot open medNAMA itself with no connection. A downloaded pack
          still works if the connection drops while this page is open, and it stays here after the page is closed.
        </div>
      ) : (
        <div style={{ fontSize: "0.74rem", color: "var(--text-muted)" }}>
          Tip: install medNAMA (browser menu → Install app / Add to Home screen) to open it with no connection.
        </div>
      )}

      {!pack ? (
        <div style={{ color: "var(--text-muted)", fontSize: "0.86rem", padding: "var(--sp-5) 0" }}>
          No pack on this device yet. Download one while you are connected: questions you have not answered, weighted toward your weakest subject.
        </div>
      ) : q ? (
        <div style={{ padding: "18px", borderRadius: "14px", border: "1px solid var(--border-light)", background: "var(--surface-1, transparent)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", color: "var(--text-muted)", marginBottom: "10px" }}>
            <span>Question {pack.pos + 1} of {pack.questions.length}{q.subject ? ` · ${q.subject}` : ""}{q.topic ? ` · ${q.topic}` : ""}</span>
            <span>{score.answered} answered · {score.right} right</span>
          </div>
          <p style={{ fontSize: "1.02rem", fontWeight: 500, lineHeight: 1.6, whiteSpace: "pre-line" }}>{q.question_text}</p>
          <div className="quiz-options-list" role="radiogroup">
            {Object.keys(q.options).sort().map((key) => {
              const isCorrect = key === q.correct_option;
              const cls = "option-button" + (picked ? (isCorrect ? " correct" : key === picked ? " selected-wrong" : "") : "");
              return (
                <button key={`${q.id}-${key}`} className={cls} role="radio" aria-checked={picked === key} disabled={!!picked}
                  onClick={() => choose(key)}
                  style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", textAlign: "left" }}>
                  <span style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                    <span className="option-badge">{key}</span>
                    <span style={{ lineHeight: 1.4 }}>{q.options[key]}</span>
                  </span>
                  {picked && isCorrect ? <Check size={14} style={{ color: "var(--success)" }} /> : null}
                  {picked === key && !isCorrect ? <X size={14} style={{ color: "var(--error)" }} /> : null}
                </button>
              );
            })}
          </div>
          {picked && q.explanation_markdown ? (
            <div className="prose" style={{ marginTop: "14px", fontSize: "0.86rem" }}
              dangerouslySetInnerHTML={{ __html: parseMarkdown(q.explanation_markdown) }} />
          ) : null}
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: "14px" }}>
            <button className="btn-workspace" onClick={() => go(-1)} disabled={pack.pos === 0}>Previous</button>
            {pack.pos + 1 < pack.questions.length ? (
              <button className="btn-primary" onClick={() => go(1)} style={{ padding: "8px 18px" }}>Next question</button>
            ) : (
              <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                End of the pack: {score.right} of {score.answered} right. Download a new one when you are online.
              </span>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
