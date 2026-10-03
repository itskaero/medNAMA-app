"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, PlayCircle, RotateCcw, Target } from "lucide-react";
import { API } from "@/lib/constants";

interface Topic { topic: string; bank: number; answered: number; accuracy: number | null }
interface Subject {
  subject: string; bank: number; answered: number; accuracy: number | null;
  coverage: number | null; topics: Topic[];
}
interface Focus { subject: string; topic: string; answered: number; accuracy: number | null }
interface Mastery {
  bank: number; answered: number; coverage: number | null; pass_line: number;
  subjects: Subject[]; focus: Focus[]; note?: string;
}

const card: React.CSSProperties = {
  background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--r-xl)", padding: "var(--sp-5)",
};
const muted: React.CSSProperties = { fontSize: "0.75rem", color: "var(--text-muted)" };
const pct = (v: number | null) => (v == null ? "–" : `${Math.round(v)}%`);

/** Accuracy bar with the pass-line marker, matching the By-subject rows in StatsView. */
function AccBar({ value, passLine, accent }: { value: number | null; passLine: number | null; accent: string }) {
  return (
    <div style={{ position: "relative", height: "8px", background: "var(--surface-3)", borderRadius: "4px" }}>
      <div style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%`, height: "100%", background: accent, borderRadius: "4px" }} />
      {passLine == null ? null : (
        <div aria-hidden style={{
          position: "absolute", left: `${passLine}%`, top: "-3px", bottom: "-3px", width: "2px", background: "var(--text-muted)",
        }} />
      )}
    </div>
  );
}

function TopicRow({ subject, t, passLine, onPractise, onRevise }: {
  subject: string; t: Topic; passLine: number;
  onPractise: (filters: Record<string, unknown>, label: string) => void;
  onRevise?: (topic: string) => void;
}) {
  const weak = t.answered >= 4 && (t.accuracy ?? 101) < passLine;
  const untried = t.answered === 0;
  return (
    <div style={{ padding: "9px 6px", borderTop: "1px solid var(--border-light)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        <span style={{ flex: 1, fontSize: "0.82rem", color: "var(--text-primary)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {t.topic}
        </span>
        <span style={{ minWidth: "4ch", textAlign: "right", fontSize: "0.82rem", fontWeight: 600,
          color: weak ? "var(--error)" : "var(--text-primary)" }}>{pct(t.accuracy)}</span>
        <button className="btn-workspace" style={{ padding: "3px 9px", fontSize: "0.72rem", whiteSpace: "nowrap" }}
          onClick={() => onPractise({ sub_categories: [subject], topics: [t.topic], num_questions: 20, prefer_unseen: true },
            `Practice · ${subject} · ${t.topic}`)}>
          <PlayCircle size={11} /> Practice
        </button>
        {weak && onRevise ? (
          <button className="btn-workspace" style={{ padding: "3px 9px", fontSize: "0.72rem", whiteSpace: "nowrap" }}
            onClick={() => onRevise(t.topic)} title={`A revision sheet on ${t.topic} from your textbooks`}>Revise</button>
        ) : null}
      </div>
      <div style={{ marginTop: "6px" }}>
        <AccBar value={t.accuracy} passLine={passLine} accent={weak ? "var(--error)" : "var(--sky)"} />
        <div style={{ ...muted, marginTop: "4px" }}>
          {untried ? `${t.bank.toLocaleString()} questions untouched`
            : `${t.answered} answered · ${t.bank.toLocaleString()} in the bank · pass line ${passLine}%`}
        </div>
      </div>
    </div>
  );
}

/** Topic-level coverage of the practice bank: which subjects and topics are weak or untouched. */
export default function MasteryMap({ token, onPractise, onRevise }: {
  token: string | null;
  onPractise: (filters: Record<string, unknown>, label: string) => void;
  onRevise?: (topic: string) => void;
}) {
  const [data, setData] = useState<Mastery | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [showAll, setShowAll] = useState<Record<string, boolean>>({});

  const load = useCallback(() => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return fetch(`${API}/api/stats/mastery`, {
      headers: t ? { Authorization: `Bearer ${t}` } : {},
      credentials: "include",
    }).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<Mastery>;
    });
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    load().then((d) => !cancelled && setData(d)).catch((e) => !cancelled && setError(String(e.message || e)));
    return () => { cancelled = true; };
  }, [load]);

  if (error) {
    return (
      <section style={card}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: "0.95rem", fontWeight: 600, color: "var(--text-primary)" }}>Mastery map</div>
            <div style={{ ...muted, marginTop: "2px" }}>Could not load (topic-level coverage and accuracy).</div>
          </div>
          <button className="btn-workspace" onClick={() => { setError(null); load().then(setData).catch((e) => setError(String(e.message || e))); }}>
            <RotateCcw size={12} /> Try again
          </button>
        </div>
      </section>
    );
  }
  if (!data) {
    return (
      <section style={card}>
        <div style={{ ...muted, display: "flex", gap: "8px", alignItems: "center" }}>
          <Loader2 size={14} className="animate-spin" /> Loading your mastery map…
        </div>
      </section>
    );
  }
  if (data.subjects.length === 0) {
    return null;
  }

  const passLine = data.pass_line;

  return (
    <section style={card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "10px", flexWrap: "wrap" }}>
        <h2 style={{ fontSize: "0.95rem", fontWeight: 600, margin: 0, color: "var(--text-primary)" }}>
          Mastery map <span style={{ fontWeight: 400, fontSize: "0.8rem", color: "var(--text-muted)" }}>
            · topic-level coverage
          </span>
        </h2>
        <span style={{ ...muted }}>
          {data.answered.toLocaleString()} of {data.bank.toLocaleString()} bank questions ({pct(data.coverage)})
        </span>
      </div>

      {data.focus.length ? (
        <div style={{ margin: "12px 0 4px", padding: "10px 12px", background: "var(--surface-3)", borderRadius: "var(--r-lg)" }}>
          <div style={{ ...muted, fontWeight: 600, marginBottom: "6px", display: "flex", alignItems: "center", gap: "6px" }}>
            <Target size={12} /> Focus topics — below the {passLine}% pass line
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {data.focus.map((f) => (
              <button key={`${f.subject}::${f.topic}`} className="btn-workspace" style={{ padding: "4px 10px", fontSize: "0.76rem" }}
                onClick={() => onPractise({ sub_categories: [f.subject], topics: [f.topic], num_questions: 20, prefer_unseen: true },
                  `Practice · ${f.subject} · ${f.topic}`)}>
                <PlayCircle size={12} /> {f.subject} · {f.topic} · {pct(f.accuracy)}
              </button>
            ))}
            {onRevise ? data.focus.slice(0, 3).map((f) => (
              <button key={`rev-${f.subject}::${f.topic}`} className="btn-workspace" style={{ padding: "4px 10px", fontSize: "0.76rem" }}
                onClick={() => onRevise(f.topic)}>Revise {f.topic}</button>
            )) : null}
          </div>
        </div>
      ) : null}

      <div style={{ marginTop: "12px", display: "flex", flexDirection: "column" }}>
        {data.subjects.map((s) => {
          const isOpen = open[s.subject] ?? s.answered > 0;
          const visible = showAll[s.subject] ? s.topics : s.topics.slice(0, 10);
          return (
            <div key={s.subject} style={{ borderTop: "1px solid var(--border-light)" }}>
              <button aria-expanded={isOpen} onClick={() => setOpen({ ...open, [s.subject]: !isOpen })}
                style={{ width: "100%", display: "flex", alignItems: "center", gap: "10px", padding: "11px 2px",
                  background: "none", border: "none", cursor: "pointer", textAlign: "left", color: "var(--text-primary)" }}>
                {isOpen ? <ChevronDown size={15} style={{ flexShrink: 0, color: "var(--text-secondary)" }} />
                  : <ChevronRight size={15} style={{ flexShrink: 0, color: "var(--text-secondary)" }} />}
                <span style={{ flex: 1, fontSize: "0.88rem", fontWeight: 600, minWidth: 0 }}>{s.subject}</span>
                <span style={{ minWidth: "4ch", textAlign: "right", fontSize: "0.8rem", fontWeight: 600,
                  color: s.answered >= 4 && (s.accuracy ?? 101) < passLine ? "var(--error)" : "var(--text-primary)" }}>
                  {pct(s.accuracy)}
                </span>
                <span style={{ ...muted, minWidth: "9ch", textAlign: "right" }}>
                  {s.answered.toLocaleString()}/{s.bank.toLocaleString()}
                </span>
              </button>
              {!isOpen ? null : (
                <div style={{ padding: "0 4px 4px 21px" }}>
                  <div style={{ marginBottom: "10px" }}>
                    <AccBar value={s.coverage ?? 0} passLine={null} accent="var(--sky)" />
                    <div style={{ ...muted, marginTop: "4px" }}>
                      {s.coverage == null ? "No questions in this subject yet." : `${s.coverage}% of this subject's bank answered`}
                    </div>
                  </div>
                  <button className="btn-workspace" style={{ padding: "3px 9px", fontSize: "0.72rem", marginBottom: "4px" }}
                    onClick={() => onPractise({ sub_categories: [s.subject], num_questions: 20, prefer_unseen: true },
                      `Practice · ${s.subject}`)}>
                    <PlayCircle size={11} /> Practice the subject
                  </button>
                  {visible.map((t) => <TopicRow onRevise={onRevise} key={t.topic} subject={s.subject} t={t} passLine={passLine} onPractise={onPractise} />)}
                  {s.topics.length > 10 ? (
                    <button className="btn-workspace" style={{ padding: "3px 8px", fontSize: "0.72rem", marginTop: "6px" }}
                      onClick={() => setShowAll({ ...showAll, [s.subject]: !showAll[s.subject] })}>
                      {showAll[s.subject] ? "Show fewer topics" : `Show ${s.topics.length - 10} more topics`}
                    </button>
                  ) : null}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <p style={{ ...muted, margin: "12px 0 0" }}>
        Each row is one topic in this subject&apos;s question bank: how many exist, how many you&apos;ve answered and your
        accuracy. The tick marks the {passLine}% pass line. {data.note ? data.note + " " : ""}Answer a topic&apos;s questions
        from the bank and it fills in here.
      </p>
    </section>
  );
}