"use client";

import React, { useCallback, useEffect, useState } from "react";
import { GraduationCap, Loader2, Timer, Trophy } from "lucide-react";
import PageShell from "@/components/layout/PageShell";
import { API } from "@/lib/constants";
import { toast } from "sonner";

type Programme = {
  programme: string;
  label: string;
  description: string;
  available: number;
  questions: number;
  minutes: number;
  can_start: boolean;
  note: string;
};

/**
 * Practise > Programme exams: sit a full paper for your exam, or this week's shared mock.
 *
 * Replaces a tile that only told the student to go to the Past papers tab. Programmes come from
 * /api/mocks/programmes with a real question count, so one with no content is shown as
 * unavailable with the reason instead of starting a paper that would come back empty.
 *
 * Filtering by year, subject or topic lives in Custom exam; this tab is the one-click route.
 */
export default function ExamsView({ token, onOpenMock, onOpenPaper, onBuildCustom }: {
  token: string | null;
  onOpenMock: () => void;
  /** A built paper, by id: sat through the shared /api/mocks/{id}/* flow. */
  onOpenPaper: (mockId: number) => void;
  /** Send the student to Custom exam to pick sources, subjects and rules themselves. */
  onBuildCustom: () => void;
}) {
  const [programmes, setProgrammes] = useState<Programme[] | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

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

  const start = async (p: Programme) => {
    setStarting(p.programme);
    try {
      const res = await fetch(`${API}/api/mocks/custom`, {
        method: "POST",
        headers: headers(),
        credentials: "include",
        body: JSON.stringify({
          programme: p.programme,
          questions: p.questions,
          minutes: p.minutes,
          label: `${p.label} paper`,
        }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok || !body?.mock_id) throw new Error(body?.detail || "Could not build the paper.");
      onOpenPaper(body.mock_id);
    } catch (e: any) {
      toast.error(e?.message || "Could not build the paper.");
    } finally {
      setStarting(null);
    }
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
            <button
              key={p.programme}
              className="tile"
              disabled={!p.can_start || starting !== null}
              title={p.can_start ? p.description : p.note}
              onClick={() => start(p)}
              style={!p.can_start ? { opacity: 0.55, cursor: "not-allowed" } : undefined}
            >
              <span className="tile-kicker">
                <Timer size={12} style={{ verticalAlign: -1 }} /> {p.questions} questions · {p.minutes} min
              </span>
              <span className="tile-title">
                {starting === p.programme ? <Loader2 size={14} className="animate-spin" /> : null} {p.label}
              </span>
              <span className="tile-text">
                {p.can_start
                  ? `${p.description}. ${p.available.toLocaleString()} questions available.`
                  : p.note}
              </span>
            </button>
          ))
        )}

        <button className="tile" onClick={onBuildCustom}>
          <span className="tile-kicker"><Timer size={12} style={{ verticalAlign: -1 }} /> Your own rules</span>
          <span className="tile-title">Build a custom exam</span>
          <span className="tile-text">Choose the sources, subjects, topics, years and length yourself, and see how many questions match as you go.</span>
        </button>
      </div>
    </PageShell>
  );
}
