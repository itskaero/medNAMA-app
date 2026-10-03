"use client";

import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle2, GitCompareArrows, Loader2, RotateCcw } from "lucide-react";
import { API } from "@/lib/constants";
import { Figure } from "@/types";
import { PairCard, PairData } from "@/components/PairCard";
import { StudyMCQ, StudyQuestion } from "@/components/StudyQuestion";
import { AppLinks } from "@/lib/nav";

interface UserPair extends PairData {
  times_confused: number;
  last_confused_at: string;
  cleared: boolean;
  next_mcq_id: number | null;
  questions: StudyMCQ[];
}

/** The look-alike concepts this student mixes up, each with a comparison and a two-question drill. */
export default function LookalikesView({ token, onFigureClick, links }: { token: string | null; onFigureClick: (f: Figure) => void; links?: AppLinks }) {
  const [pairs, setPairs] = useState<UserPair[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drill, setDrill] = useState<{ pairId: number; step: number } | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/study/lookalikes`, { headers: headers(), credentials: "include" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((body) => {
        if (!cancelled) setPairs(body.pairs);
      })
      .catch((e) => {
        if (!cancelled) setError(`Could not load your look-alikes (${e instanceof Error ? e.message : String(e)}).`);
      });
    return () => {
      cancelled = true;
    };
  }, [headers, refreshKey]);

  const reload = () => {
    setError(null);
    setRefreshKey((k) => k + 1);
  };

  return (
    <div className="dashboard-view" role="region" aria-label="Look-alikes" style={{ maxWidth: "820px", margin: "0 auto" }}>
      <div className="dashboard-header">
        <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <GitCompareArrows size={20} style={{ color: "#d97706" }} /> Look-alikes
        </h1>
        <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--text-secondary)" }}>
          Pairs of concepts you have mixed up, compared side by side from your textbooks. Answer both questions of a pair
          correctly to clear it.
        </p>
      </div>

      {error ? (
        <div>
          <p style={{ color: "var(--error)" }}>{error}</p>
          <button className="btn-workspace" onClick={reload}><RotateCcw size={12} /> Try again</button>
        </div>
      ) : !pairs ? (
        <div style={{ color: "var(--text-muted)", display: "flex", gap: "8px", alignItems: "center" }}>
          <Loader2 size={16} className="animate-spin" /> Loading…
        </div>
      ) : pairs.length === 0 ? (
        <p style={{ color: "var(--text-secondary)" }}>
          None yet. When you pick an option that resembles the right answer (afferent for efferent, Crohn&apos;s for UC),
          the pair appears here with a comparison and two questions to tell them apart.
          {links ? <>{" "}<button className="btn-workspace" onClick={() => links.go("quiz")} style={{ marginLeft: 4 }}>Practise</button></> : null}
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
          {pairs.map((p) => {
            const drilling = drill?.pairId === p.id;
            const q = drilling ? p.questions[drill!.step] : null;
            return (
              <section key={p.id} style={{ border: "1px solid var(--border-light)", borderRadius: "14px", padding: "14px", display: "flex", flexDirection: "column", gap: "10px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                    Mixed up {p.times_confused}× · last {new Date(p.last_confused_at).toLocaleDateString()}
                  </span>
                  {p.cleared ? (
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "0.75rem", color: "var(--sea-green)", fontWeight: 700 }}>
                      <CheckCircle2 size={13} /> Cleared
                    </span>
                  ) : p.status === "pending" ? (
                    <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                      <Loader2 size={12} className="animate-spin" /> Writing the comparison…
                    </span>
                  ) : p.status === "ready" && p.questions.length ? (
                    <button className="btn-workspace" style={{ padding: "4px 12px", fontSize: "0.75rem" }}
                      onClick={() => setDrill(drilling ? null : { pairId: p.id, step: 0 })}>
                      {drilling ? "Close drill" : "Drill this pair"}
                    </button>
                  ) : null}
                </div>
                {p.status === "ready" ? <PairCard pair={p} /> : p.status === "failed" ? (
                  <div style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>{p.term_a} vs {p.term_b}: the comparison could not be written.</div>
                ) : (
                  <div style={{ fontSize: "0.85rem", fontWeight: 600 }}>{p.term_a} vs {p.term_b}</div>
                )}
                {links && !drilling ? (
                  <div className="next-actions">
                    <button className="btn-workspace" onClick={() => links.ask(`How do I tell ${p.term_a} from ${p.term_b}? Give the distinguishing features an FCPS question would test.`)}>
                      Ask Dr MedNama</button>
                    <button className="btn-workspace" onClick={() => links.revise(`${p.term_a} vs ${p.term_b}`)}>Revise {p.term_a} vs {p.term_b}</button>
                  </div>
                ) : null}
                {drilling && q ? (
                  <div style={{ borderTop: "1px solid var(--border-light)", paddingTop: "10px" }}>
                    <div style={{ fontSize: "0.7rem", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-muted)", marginBottom: "8px" }}>
                      Question {drill!.step + 1} of {p.questions.length}
                    </div>
                    <StudyQuestion
                      key={`${p.id}-${drill!.step}`}
                      mcq={q}
                      token={token}
                      onFigureClick={onFigureClick}
                      onNext={() => {
                        if (drill!.step + 1 < p.questions.length) setDrill({ pairId: p.id, step: drill!.step + 1 });
                        else {
                          setDrill(null);
                          reload();
                        }
                      }}
                      nextLabel={drill!.step + 1 < p.questions.length ? "Next" : "Done"}
                    />
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
