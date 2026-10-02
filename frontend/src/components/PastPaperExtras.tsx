"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Layers, Loader2, Scale, Shuffle } from "lucide-react";
import { API } from "@/lib/constants";
import { Figure } from "@/types";
import { StudyQuestion, StudyMCQ } from "@/components/StudyQuestion";
import { EvidenceList, RefereeResult, VerdictBadge } from "@/components/views/RefereeView";

/** What the quiz API adds to a past-paper question (app/past_papers.recall_info). */
export interface PastPaperFields {
  id: number;
  main_category?: string | null;
  archive?: string;
  recall_versions?: number;
  key_conflict?: boolean;
}

interface RecallVersion {
  id: number;
  archive: string;
  question_text: string;
  options: Record<string, string>;
  correct_option: string;
  answer: string;
  same_key: boolean;
  explanation_markdown: string | null;
  paper_years: number[];
}

interface Twist extends StudyMCQ {
  twist_type: string | null;
  twist_label: string;
  referee: string | null;
  grounding: string | null;
}

const isPastPaper = (m: PastPaperFields) => Boolean(m.archive) || (m.main_category || "").startsWith("Past papers");

function useHeaders(token: string | null) {
  return useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
  }, [token]);
}

/** Header badges: which archive recalled it, and whether the archives' keys disagree. */
export function ArchiveBadges({ mcq }: { mcq: PastPaperFields }) {
  return (
    <>
      {mcq.archive ? (
        <span title={mcq.recall_versions ? `Also recalled by another archive (${mcq.recall_versions})` : "Archive that recalled this question"}
          style={{ fontSize: "0.7rem", color: "var(--text-secondary)", background: "var(--surface-3)", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 }}>
          {mcq.archive}{mcq.recall_versions ? ` +${mcq.recall_versions}` : ""}
        </span>
      ) : null}
      {mcq.key_conflict ? (
        <span title="Another archive recalled this question with a different key. Check it against the textbooks after answering."
          style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "0.7rem", color: "#d97706", background: "rgba(245,158,11,0.12)", padding: "2px 8px", borderRadius: "10px", fontWeight: 700 }}>
          <AlertTriangle size={11} /> Keys disagree
        </span>
      ) : null}
    </>
  );
}

/** After answering a past-paper question: its other recalled versions, a textbook check when keys
 *  disagree, and Twists (new questions on the same concept that ask something different). */
export function PastPaperExtras({ mcq, token, onFigureClick }: {
  mcq: PastPaperFields;
  token: string | null;
  onFigureClick: (f: Figure) => void;
}) {
  const headers = useHeaders(token);
  const [recalls, setRecalls] = useState<RecallVersion[] | null>(null);
  const [recallsOpen, setRecallsOpen] = useState(false);
  const [check, setCheck] = useState<RefereeResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [twists, setTwists] = useState<{ status: string; twists?: Twist[]; detail?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const poll = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (poll.current) clearTimeout(poll.current);
  }, []);

  const loadRecalls = async () => {
    setRecallsOpen((o) => !o);
    if (recalls) return;
    try {
      const r = await fetch(`${API}/api/mcqs/${mcq.id}/recalls`, { headers: headers(), credentials: "include" });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      setRecalls(body.versions || []);
    } catch (e) {
      setError(`Could not load the other versions: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const checkKey = async () => {
    setChecking(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/mcqs/${mcq.id}/check-key`, { method: "POST", headers: headers(), credentials: "include" });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      setCheck(body);
    } catch (e) {
      setError(`The textbook check failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setChecking(false);
    }
  };

  const follow = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/mcqs/${mcq.id}/twists`, { headers: headers(), credentials: "include" });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      setTwists(body);
      if (body.status === "running") poll.current = setTimeout(follow, 4000);
    } catch (e) {
      setTwists({ status: "failed", detail: e instanceof Error ? e.message : String(e) });
    }
  }, [mcq.id, headers]);

  const twistIt = async () => {
    setTwists({ status: "running" });
    try {
      const r = await fetch(`${API}/api/mcqs/${mcq.id}/twists`, { method: "POST", headers: headers(), credentials: "include" });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      setTwists(body);
      if (body.status === "running") poll.current = setTimeout(follow, 4000);
    } catch (e) {
      setTwists({ status: "failed", detail: e instanceof Error ? e.message : String(e) });
    }
  };

  if (!isPastPaper(mcq)) return null;
  const small: React.CSSProperties = { padding: "4px 10px", fontSize: "0.76rem" };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px", borderTop: "1px solid var(--border-light)", paddingTop: "12px" }}>
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
        {mcq.recall_versions ? (
          <button type="button" className="btn-workspace" style={small} onClick={loadRecalls} aria-expanded={recallsOpen}>
            <Layers size={12} /> Also recalled as ({mcq.recall_versions})
          </button>
        ) : null}
        {mcq.key_conflict ? (
          <button type="button" className="btn-workspace" style={small} onClick={checkKey} disabled={checking}>
            {checking ? <Loader2 size={12} className="animate-spin" /> : <Scale size={12} />} Check with textbooks
          </button>
        ) : null}
        <button type="button" className="btn-workspace" style={small} onClick={twistIt}
          disabled={twists?.status === "running" || twists?.status === "done"}
          title="New questions on the same concept that ask something different: the next step, the mechanism, a changed finding... Written from your textbooks and checked by the Answer-Key Referee.">
          {twists?.status === "running" ? <Loader2 size={12} className="animate-spin" /> : <Shuffle size={12} />} Twist it
        </button>
      </div>

      {error ? <div style={{ fontSize: "0.78rem", color: "var(--error)" }}>{error}</div> : null}

      {recallsOpen && recalls ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {recalls.map((v) => (
            <div key={v.id} style={{ padding: "10px 12px", borderRadius: "10px", background: "var(--surface-3)", fontSize: "0.82rem" }}>
              <div style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "4px", fontSize: "0.7rem", fontWeight: 700 }}>
                <span style={{ color: "var(--text-secondary)" }}>{v.archive}{v.paper_years.length ? ` · ${v.paper_years.join(", ")}` : ""}</span>
                <span style={{ color: v.same_key ? "var(--sea-green)" : "#d97706" }}>{v.same_key ? "Same key" : "Different key"}</span>
              </div>
              <div style={{ whiteSpace: "pre-line", marginBottom: "6px" }}>{v.question_text}</div>
              {Object.entries(v.options).map(([k, text]) => (
                <div key={k} style={{ fontWeight: k === v.correct_option ? 700 : 400, color: k === v.correct_option ? "var(--sea-green)" : "var(--text-secondary)" }}>
                  {k}. {text}
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : null}

      {check ? (
        <div style={{ padding: "10px 12px", borderRadius: "10px", border: "1px solid var(--border-light)", display: "flex", flexDirection: "column", gap: "6px" }}>
          <VerdictBadge verdict={check.verdict} />
          {check.textbook_answer ? <div style={{ fontSize: "0.82rem" }}>Textbooks: <strong>{check.textbook_answer}</strong></div> : null}
          {check.explanation ? <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>{check.explanation}</div> : null}
          <EvidenceList evidence={check.evidence || []} />
        </div>
      ) : null}

      {twists?.status === "failed" ? (
        <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>No twist this time: {twists.detail}</div>
      ) : null}
      {(twists?.twists || []).map((t, i) => (
          <div key={t.id} style={{ borderRadius: "12px", border: "1px solid var(--border-light)", padding: "12px" }}>
            <div style={{ fontSize: "0.7rem", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-muted)", marginBottom: "8px", display: "flex", gap: "8px" }}>
              <span>Twist {i + 1} · {t.twist_label}</span>
              <span style={{ color: t.grounding === "book" ? "var(--sea-green)" : "var(--text-muted)" }}>
                {t.grounding === "book" ? "From your textbooks" : "AI knowledge"}
              </span>
            </div>
            <StudyQuestion mcq={t} token={token} onFigureClick={onFigureClick} />
          </div>
        ))}
      {twists?.status === "running" ? (
        <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", display: "flex", gap: "6px", alignItems: "center" }}>
          <Loader2 size={12} className="animate-spin" />
          {twists.twists?.length
            ? "Checking the next twist against your textbooks…"
            : "Writing twists from your textbooks and checking each one (the first usually appears within a minute)…"}
        </div>
      ) : null}
    </div>
  );
}
