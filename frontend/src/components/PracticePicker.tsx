"use client";

import React, { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { API } from "@/lib/constants";

/** A Practice selection (backend: app/practice_scope.py). Topics are "Subject|Topic". */
export interface PracticeScope {
  sources: ("bank" | "past")[];
  subjects: string[];
  topics: string[];
  years: number[];
}

export const EMPTY_SCOPE: PracticeScope = { sources: ["past", "bank"], subjects: [], topics: [], years: [] };
export const scopeActive = (s: PracticeScope | null | undefined) => !!s && (s.subjects.length > 0 || s.topics.length > 0);

/** "Upper Limb, Renal" / "Anatomy, Physiology" (+ source when not both) for session titles. */
export function describeScope(s: PracticeScope): string {
  const topicSubjects = new Set(s.topics.map((t) => t.split("|")[0]));
  const parts = [...s.topics.map((t) => t.split("|")[1]), ...s.subjects.filter((x) => !topicSubjects.has(x))];
  const from = s.sources.length === 1 ? (s.sources[0] === "past" ? " · past papers" : " · question bank") : "";
  return `${parts.join(", ") || "Practice"}${from}${s.years.length ? ` · ${s.years.join(", ")}` : ""}`;
}

interface TreeTopic { topic: string; bank: number; past: number }
interface TreeSubject { subject: string; topics: TreeTopic[]; bank: number; past: number }
interface Tree { subjects: TreeSubject[]; years: number[] }

const SOURCE_CHOICES: { key: string; label: string; sources: ("bank" | "past")[]; hint: string }[] = [
  { key: "both", label: "Past papers + question bank", sources: ["past", "bank"], hint: "Past papers first, then the Paper 1 bank" },
  { key: "past", label: "Past papers only", sources: ["past"], hint: "Questions CPSP has asked (FCPS Part 1)" },
  { key: "bank", label: "Question bank only", sources: ["bank"], hint: "The Paper 1 practice bank" },
];

const count = (x: { bank: number; past: number }, sources: ("bank" | "past")[]) =>
  (sources.includes("bank") ? x.bank : 0) + (sources.includes("past") ? x.past : 0);

/** How many questions a scope covers, from the tree's counts (years are not counted). */
export function scopeSize(tree: Tree | null, scope: PracticeScope): number {
  if (!tree) return 0;
  let n = 0;
  for (const s of tree.subjects) {
    const picked = s.topics.filter((t) => scope.topics.includes(`${s.subject}|${t.topic}`));
    if (picked.length) n += picked.reduce((a, t) => a + count(t, scope.sources), 0);
    else if (scope.subjects.includes(s.subject)) n += count(s, scope.sources);
  }
  return n;
}

/** Practice by subject and topic: one list for past papers and the question bank. */
export default function PracticePicker({
  getHeaders,
  scope,
  setScope,
  onSize,
}: {
  getHeaders: () => HeadersInit;
  scope: PracticeScope;
  setScope: (s: PracticeScope) => void;
  onSize?: (n: number) => void;
}) {
  const [tree, setTree] = useState<Tree | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [showYears, setShowYears] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/practice/tree`, { headers: getHeaders(), credentials: "include" })
      .then(async (r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setTree)
      .catch((e) => setError(e.message));
  }, [getHeaders]);

  const size = useMemo(() => scopeSize(tree, scope), [tree, scope]);
  useEffect(() => { onSize?.(size); }, [size, onSize]);

  const sourceKey = SOURCE_CHOICES.find((c) => c.sources.length === scope.sources.length && c.sources.every((s) => scope.sources.includes(s)))?.key ?? "both";
  const toggleSubject = (subject: string) => {
    const on = scope.subjects.includes(subject);
    setScope({
      ...scope,
      subjects: on ? scope.subjects.filter((s) => s !== subject) : [...scope.subjects, subject],
      topics: on ? scope.topics.filter((t) => !t.startsWith(`${subject}|`)) : scope.topics,
    });
    if (!on) setOpen(subject);
  };
  const toggleTopic = (subject: string, topic: string) => {
    const key = `${subject}|${topic}`;
    const on = scope.topics.includes(key);
    const topics = on ? scope.topics.filter((t) => t !== key) : [...scope.topics, key];
    const subjects = scope.subjects.includes(subject) ? scope.subjects : [...scope.subjects, subject];
    setScope({ ...scope, topics, subjects });
  };
  const toggleYear = (y: number) =>
    setScope({ ...scope, years: scope.years.includes(y) ? scope.years.filter((x) => x !== y) : [...scope.years, y].sort() });

  if (error) return <div className="error-banner" role="alert">Couldn&apos;t load the subject list ({error}).</div>;
  if (!tree) return <div style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}><Loader2 size={13} className="animate-spin" /> Loading subjects…</div>;

  return (
    <div className="practice-picker">
      <div className="practice-picker-row" role="radiogroup" aria-label="Questions from">
        <span className="practice-picker-label">Questions from</span>
        {SOURCE_CHOICES.map((c) => (
          <button key={c.key} type="button" role="radio" aria-checked={sourceKey === c.key} title={c.hint}
            className={`practice-chip ${sourceKey === c.key ? "active" : ""}`}
            onClick={() => setScope({ ...scope, sources: c.sources, years: c.sources.includes("past") ? scope.years : [] })}>
            {c.label}
          </button>
        ))}
      </div>

      <div className="practice-subjects" role="group" aria-label="Subjects">
        {tree.subjects.map((s) => {
          const n = count(s, scope.sources);
          const picked = scope.subjects.includes(s.subject);
          const pickedTopics = scope.topics.filter((t) => t.startsWith(`${s.subject}|`)).length;
          const expanded = open === s.subject;
          return (
            <div key={s.subject} className={`practice-subject ${picked ? "active" : ""}`}>
              <div className="practice-subject-head">
                <button type="button" className="practice-subject-pick" onClick={() => toggleSubject(s.subject)}
                  disabled={!n && !picked} aria-pressed={picked}>
                  <span className="practice-subject-name">{s.subject}</span>
                  <span className="practice-subject-count">
                    {n.toLocaleString()}{picked ? (pickedTopics ? ` · ${pickedTopics} topic${pickedTopics > 1 ? "s" : ""}` : " · all topics") : ""}
                  </span>
                </button>
                {s.topics.length > 1 ? (
                  <button type="button" className="practice-subject-expand" aria-expanded={expanded}
                    aria-label={`${expanded ? "Hide" : "Show"} ${s.subject} topics`} onClick={() => setOpen(expanded ? null : s.subject)}>
                    {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </button>
                ) : null}
              </div>
              {expanded ? (
                <div className="practice-topics" role="group" aria-label={`${s.subject} topics`}>
                  {s.topics.map((t) => {
                    const tn = count(t, scope.sources);
                    const on = scope.topics.includes(`${s.subject}|${t.topic}`);
                    return (
                      <button key={t.topic} type="button" disabled={!tn && !on} aria-pressed={on}
                        className={`practice-chip ${on ? "active" : ""}`} onClick={() => toggleTopic(s.subject, t.topic)}>
                        {t.topic} <span className="practice-chip-count">{tn.toLocaleString()}</span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {scope.sources.includes("past") && tree.years.length ? (
        <div className="practice-picker-row">
          <button type="button" className="practice-link" onClick={() => setShowYears(!showYears)} aria-expanded={showYears}>
            {showYears ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            Asked in {scope.years.length ? scope.years.join(", ") : "any year"}
          </button>
          {showYears ? (
            <div className="practice-topics" role="group" aria-label="Past-paper years">
              {tree.years.map((y) => (
                <button key={y} type="button" aria-pressed={scope.years.includes(y)}
                  className={`practice-chip ${scope.years.includes(y) ? "active" : ""}`} onClick={() => toggleYear(y)}>{y}</button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="practice-summary">
        {scopeActive(scope)
          ? <>Selected: <b>{size.toLocaleString()}</b> questions{scope.years.length ? " (before the year filter)" : ""}</>
          : "Pick one or more subjects, then narrow to topics if you like."}
        {scopeActive(scope) ? (
          <button type="button" className="practice-link" onClick={() => setScope({ ...EMPTY_SCOPE, sources: scope.sources })}>Clear</button>
        ) : null}
      </div>
    </div>
  );
}
