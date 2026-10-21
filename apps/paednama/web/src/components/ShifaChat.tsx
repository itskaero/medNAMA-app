"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "~/lib/api";
import { renderAnswer } from "~/lib/md";
import type { StageId } from "~/lib/types";

type Mode = "explain" | "tutor" | "reasoning";
interface Msg { id: number; role: "user" | "shifa"; content: string; citations: { n?: number; book_title?: string; page?: number | null }[]; grounding?: string | null }
interface ChatRow { id: number; title: string; mode: Mode; topic_id: string | null; updated_at: string | null }

const MODES: { id: Mode; label: string; hint: string; placeholder: string }[] = [
  { id: "explain", label: "Explain", hint: "A cited answer from your Paediatrics books and guidelines.",
    placeholder: "Ask anything: “Management of DKA in a 9-year-old?”" },
  { id: "tutor", label: "Tutor me", hint: "Dr. Shifa asks, you answer, she corrects from the books, one step at a time.",
    placeholder: "What do you want to be taught? “Teach me neonatal jaundice”" },
  { id: "reasoning", label: "Clinical reasoning", hint: "Describe a child. She asks for your differential first, then ranks it, names the must-not-miss and the next tests.",
    placeholder: "“A 2-year-old with fever for 6 days, red eyes and a rash…”" },
];

/** Dr. Shifa (شفا): the tutor. Used on its own screen, and inside a topic (topicId) where it starts from that topic. */
export default function ShifaChat({ stage, topicId, topicName, compact = false }: {
  stage: StageId; topicId?: string; topicName?: string; compact?: boolean;
}) {
  const [mode, setMode] = useState<Mode>(topicId ? "tutor" : "explain");
  const [chats, setChats] = useState<ChatRow[]>([]);
  const [chatId, setChatId] = useState<number | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  const loadChats = useCallback(() => {
    api<{ chats: ChatRow[] }>(`/api/paeds/shifa/chats${topicId ? `?topic_id=${encodeURIComponent(topicId)}` : ""}`)
      .then((d) => setChats(d.chats)).catch(() => {});
  }, [topicId]);
  useEffect(() => { loadChats(); }, [loadChats]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);

  const open = async (id: number) => {
    const d = await api<{ id: number; mode: Mode; messages: Msg[] }>(`/api/paeds/shifa/chats/${id}`).catch(() => null);
    if (d) { setChatId(d.id); setMode(d.mode); setMsgs(d.messages); }
  };
  const fresh = () => { setChatId(null); setMsgs([]); setText(""); };
  const remove = async (id: number) => {
    await api(`/api/paeds/shifa/chats/${id}`, { method: "DELETE" }).catch(() => null);
    if (id === chatId) fresh();
    loadChats();
  };
  const send = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true);
    setText("");
    setMsgs((m) => [...m, { id: -Date.now(), role: "user", content: q, citations: [] }]);
    try {
      const d = await api<{ chat_id: number; message: Msg }>("/api/paeds/shifa/ask", {
        method: "POST", body: JSON.stringify({ text: q, mode, stage, chat_id: chatId, topic_id: topicId ?? null }),
      });
      setChatId(d.chat_id);
      setMsgs((m) => [...m, d.message]);
      loadChats();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Dr. Shifa did not answer.");
      setMsgs((m) => m.slice(0, -1));
      setText(q);
    } finally {
      setBusy(false);
    }
  };
  const current = MODES.find((m) => m.id === mode)!;
  const starter = topicName && !msgs.length
    ? (mode === "tutor" ? `Teach me ${topicName}, one question at a time.` : mode === "reasoning" ? `Give me a case on ${topicName} and test my reasoning.` : `Summarise the key exam points on ${topicName}.`)
    : null;

  return (
    <div className={`pn-shifa ${compact ? "compact" : ""}`}>
      {!compact ? (
        <aside className="pn-shifa-list" aria-label="Conversations">
          <button type="button" className="btn-workspace" onClick={fresh}><Plus size={14} /> New conversation</button>
          {chats.map((c) => (
            <div key={c.id} className={`pn-shifa-item ${c.id === chatId ? "on" : ""}`}>
              <button type="button" onClick={() => open(c.id)} title={c.title}>
                <span>{c.title}</span><small>{MODES.find((m) => m.id === c.mode)?.label}</small>
              </button>
              <button type="button" className="del" aria-label={`Delete ${c.title}`} onClick={() => remove(c.id)}><Trash2 size={13} /></button>
            </div>
          ))}
        </aside>
      ) : null}
      <section className="pn-shifa-main">
        <div className="pn-shifa-head">
          <div className="segmented" role="radiogroup" aria-label="Mode">
            {MODES.map((m) => (
              <button key={m.id} type="button" role="radio" aria-checked={mode === m.id}
                className={`segmented-item ${mode === m.id ? "active" : ""}`} onClick={() => setMode(m.id)}>{m.label}</button>
            ))}
          </div>
          {compact && chats.length ? (
            <select className="pn-shifa-pick" value={chatId ?? ""} onChange={(e) => (e.target.value ? open(Number(e.target.value)) : fresh())}
              aria-label="Earlier conversations on this topic">
              <option value="">New conversation</option>
              {chats.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
            </select>
          ) : null}
        </div>
        <p className="pn-src" style={{ margin: 0 }}>{current.hint}</p>

        <div className="pn-thread">
          {!msgs.length ? (
            <div className="pn-shifa-hello">
              <b>Dr. Shifa</b>
              <span>I answer from your Paediatrics textbooks and guidelines, and cite the book and page for every point.</span>
              {starter ? <button type="button" className="practice-chip" onClick={() => setText(starter)}>{starter}</button> : null}
            </div>
          ) : null}
          {msgs.map((m) => m.role === "user" ? (
            <div key={m.id} className="pn-msg me">{m.content}</div>
          ) : (
            <div key={m.id} className="pn-msg shifa">
              <span className="pn-kicker">Dr. Shifa{m.grounding === "none" ? " · no passages found" : ""}</span>
              <div className="pn-sheet" dangerouslySetInnerHTML={{ __html: renderAnswer(m.content, m.citations) }} />
              {m.citations.some((c) => c.n) ? (
                <ol className="pn-cites">
                  {m.citations.filter((c) => c.n).map((c) => <li key={c.n} value={c.n}><b>{c.book_title}</b>{c.page ? ` · p.${c.page}` : ""}</li>)}
                </ol>
              ) : null}
            </div>
          ))}
          {busy ? <div className="pn-msg shifa pn-wait"><Loader2 size={15} className="animate-spin" /> Dr. Shifa is reading the books…</div> : null}
          <div ref={end} />
        </div>

        <form className="pn-composer" onSubmit={send}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder={current.placeholder}
            aria-label="Message Dr. Shifa" onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <button type="submit" className="btn-primary" disabled={busy || !text.trim()} aria-label="Send"><ArrowUp size={16} /></button>
        </form>
      </section>
    </div>
  );
}
