"use client";

import React from "react";
import { History, Timer, Trophy } from "lucide-react";
import PageShell from "@/components/layout/PageShell";

// CPSP FCPS-I: two papers of 100 single-best-answer MCQs, 2 hours each, 75% pass mark, no negative marking.
const PRESETS = [
  { key: "short", label: "FCPS mock (short)", questions: 50, minutes: 60, text: "Half a paper: every subject, unseen questions first." },
  { key: "full", label: "Full FCPS-I paper", questions: 100, minutes: 120, text: "One full paper, timed like the real exam." },
];

/** Practise > Exams: every timed format in one place (board mode, answers at the end). */
export default function ExamsView({ onStartExam, onOpenMock, onOpenPastPapers }: {
  token: string | null;
  onStartExam: (preset: { questions: number; minutes: number; label: string }) => void;
  onOpenMock: () => void;
  onOpenPastPapers: () => void;
}) {
  return (
    <PageShell title="Exams" icon={<Timer size={24} />}
      subtitle="Timed, exam-style papers. Answers and explanations come at the end, like the real thing; every answer still feeds your Progress and re-tests.">
      <div className="tile-grid">
        {PRESETS.map((p) => (
          <button key={p.key} className="tile" onClick={() => onStartExam({ questions: p.questions, minutes: p.minutes, label: p.label })}>
            <span className="tile-kicker"><Timer size={12} style={{ verticalAlign: -1 }} /> {p.questions} questions · {p.minutes} min</span>
            <span className="tile-title">{p.label}</span>
            <span className="tile-text">{p.text}</span>
          </button>
        ))}
        <button className="tile" onClick={onOpenMock}>
          <span className="tile-kicker"><Trophy size={12} style={{ verticalAlign: -1 }} /> Every week</span>
          <span className="tile-title">Weekly mock</span>
          <span className="tile-text">The same paper for everyone this week: your rank, percentile and the leaderboard.</span>
        </button>
        <button className="tile" onClick={onOpenPastPapers}>
          <span className="tile-kicker"><History size={12} style={{ verticalAlign: -1 }} /> From the archive</span>
          <span className="tile-title">Timed past paper</span>
          <span className="tile-text">Pick years or sittings in Past papers, then "Start timed paper" (50, 100 or 200 questions).</span>
        </button>
      </div>
    </PageShell>
  );
}
