"use client";

import React, { useCallback, useEffect, useState } from "react";
import { GraduationCap, Loader2, Timer, Trophy } from "lucide-react";
import PageShell from "@/components/layout/PageShell";
import { API } from "@/lib/constants";
import { toast } from "sonner";

type Specialty = { specialty: string; available: number; can_start: boolean };
type Paper = {
  key: string | null;
  label: string;
  available: number;
  questions: number;
  minutes: number;
  needs_specialty: boolean;
  specialties: Specialty[];
  can_start: boolean;
};
type Programme = {
  programme: string;
  label: string;
  description: string;
  available: number;
  papers: Paper[];
  can_start: boolean;
  note: string;
};

/**
 * Practise > Programme exams: sit a full paper for your exam, or this week's shared mock.
 *
 * Papers come from /api/mocks/programmes with real question counts. FCPS Part 1 is two papers
 * of 100, sat separately: Paper 1 basic sciences, Paper 2 the candidate's specialty, so a
 * specialty paper asks for the specialty first. A programme with no content is shown as
 * unavailable with the reason instead of starting a paper that would come back empty.
 *
 * Filtering by subject or topic lives in Custom exam; this tab is the one-click route.
 */
export default function ExamsView({ token, onOpenMock, onOpenPaper, onBuildCustom }: {
  token: string | null;
  onOpenMock: () => void;
  /** A built paper, by id: sat through the shared /api/mocks/{id}/* flow. */
  onOpenPaper: (mockId: number) => void;
  /** Send the student to Custom exam to pick a paper, subjects and rules themselves. */
  onBuildCustom: () => void;
}) {
  const [programmes, setProgrammes] = useState<Programme[] | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const [specialty, setSpecialty] = useState<Record<string, string>>({});

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
  }, [token]);

  useEffect(() => {
    fetch(`${API}/api/mocks/programmes`, { headers: headers(), credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setProgrammes(d?.programmes ?? []))
      .catch(() => setProgrammes([]));
  }, [headers]);

  const start = async (p: Programme, paper: Paper) => {
    const id = `${p.programme}:${paper.key}`;
    const chosen = specialty[id] || "";
    if (paper.needs_specialty && !chosen) {
      toast.warning("Choose your specialty first.");
      return;
    }
    setStarting(id);
    try {
      const res = await fetch(`${API}/api/mocks/custom`, {
        method: "POST",
        headers: headers(),
        credentials: "include",
        body: JSON.stringify({
          programme: p.programme,
          part: paper.key,
          specialty: chosen || undefined,
          questions: paper.questions,
          minutes: paper.minutes,
        }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok || !body?.mock_id) throw new Error(body?.detail || "Could not build the paper.");
      onOpenPaper(body.mock_id);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Could not build the paper.");
    } finally {
      setStarting(null);
    }
  };

  const selectStyle: React.CSSProperties = {
    padding: "6px 8px", borderRadius: "8px", border: "1px solid var(--border-light)",
    background: "var(--surface-3)", color: "var(--text-primary)", fontSize: "0.8rem", width: "100%",
  };

  return (
    <PageShell title="Programme exams" icon={<GraduationCap size={24} />}
      subtitle="A full, timed paper for your exam. Answers and explanations come at the end, like the real thing; every answer still feeds your Progress and re-tests.">
      <div className="tile-grid">
        <button className="tile" onClick={onOpenMock}>
          <span className="tile-kicker"><Trophy size={12} style={{ verticalAlign: -1 }} /> Every week</span>
          <span className="tile-title">Weekly mock</span>
          <span className="tile-text">The same paper for everyone this week: your rank, percentile and the leaderboard.</span>
        </button>

        {programmes === null ? (
          <div className="tile" aria-busy="true">
            <span className="tile-title"><Loader2 size={14} className="animate-spin" /> Loading programmes…</span>
          </div>
        ) : (
          programmes.map((p) => (
            <div key={p.programme} className="tile" style={!p.can_start ? { opacity: 0.55 } : undefined}
              title={p.can_start ? p.description : p.note}>
              <span className="tile-kicker">
                <Timer size={12} style={{ verticalAlign: -1 }} />{" "}
                {p.papers.length > 1 ? `${p.papers.length} papers` : p.papers.length ? `${p.papers[0].questions} questions · ${p.papers[0].minutes} min` : "Not available yet"}
              </span>
              <span className="tile-title">{p.label}</span>
              {!p.papers.length ? <span className="tile-text">{p.note}</span> : null}
              {p.papers.map((paper) => {
                const id = `${p.programme}:${paper.key}`;
                const chosen = specialty[id] || "";
                return (
                  <div key={id} style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "8px", width: "100%" }}>
                    <span className="tile-text" style={{ fontWeight: 600 }}>
                      {paper.label} · {paper.questions} questions · {paper.minutes} min
                    </span>
                    {paper.needs_specialty ? (
                      <select aria-label={`${p.label} ${paper.label} specialty`} value={chosen} style={selectStyle}
                        onChange={(e) => setSpecialty((cur) => ({ ...cur, [id]: e.target.value }))}>
                        <option value="">Your specialty…</option>
                        {paper.specialties.map((s) => (
                          <option key={s.specialty} value={s.specialty} disabled={!s.can_start}>
                            {s.specialty} · {s.available.toLocaleString()} questions
                          </option>
                        ))}
                      </select>
                    ) : null}
                    <button className="btn-workspace" disabled={!paper.can_start || starting !== null || (paper.needs_specialty && !chosen)}
                      onClick={() => start(p, paper)}
                      style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "0.76rem" }}>
                      {starting === id ? <Loader2 size={12} className="animate-spin" /> : <Timer size={12} />}
                      {paper.can_start ? `Sit ${paper.label.split(" · ")[0]}` : "No questions yet"}
                    </button>
                  </div>
                );
              })}
            </div>
          ))
        )}

        <button className="tile" onClick={onBuildCustom}>
          <span className="tile-kicker"><Timer size={12} style={{ verticalAlign: -1 }} /> Your own rules</span>
          <span className="tile-title">Build a custom exam</span>
          <span className="tile-text">Paper 1 by subject and topic, Paper 2 by specialty, your missed questions or a special set, with your own length and timer.</span>
        </button>
      </div>
    </PageShell>
  );
}
