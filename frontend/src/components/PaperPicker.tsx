"use client";

import React, { useEffect, useMemo, useState } from "react";
import { BookMarked, ChevronDown, ChevronRight, GraduationCap, Layers, Loader2, RotateCcw, Sparkles, Stethoscope } from "lucide-react";
import { API } from "@/lib/constants";
import { EMPTY_SCOPE, PracticeScope, Tree } from "@/components/PracticePicker";

/** What Custom exam can be built from. FCPS Part 1 is sat as two papers, so those are the two
 *  main choices; PGET is Punjab/SZABMU PG entrance; the other two are your missed questions and special sets. */
type Choice = "paper1" | "paper2" | "pget" | "missed" | "sets";

interface AiSet { quiz_set_id: string; quiz_set_title: string; question_count: number }
export interface Collection { name: string; count: number }

const both = (x: { bank: number; past: number }) => x.bank + x.past;

const DIFFICULTIES: { value: PracticeScope["difficulty"]; label: string; title: string }[] = [
  { value: undefined, label: "Any", title: "Every question, rated or not" },
  { value: "easier", label: "Easier", title: "Most students get these right (or rated 1-2 when written)" },
  { value: "medium", label: "Medium", title: "About half get these right (or rated 3)" },
  { value: "harder", label: "Harder", title: "Most students get these wrong (or rated 4-5)" },
];

/**
 * Custom exam, step 1: Paper 1, Paper 2, your missed questions, or a special set.
 *
 * Each paper draws on the question bank and the past papers together (backend
 * app/practice_scope.py): Paper 1 by basic-science subject and topic, Paper 2 by specialty.
 * Past-paper years and sources are left to the Past papers tab, which is for going through
 * the archive paper by paper.
 */
export default function PaperPicker({
  getHeaders,
  scope,
  setScope,
  onSize,
  collections,
  pickedCollection,
  onPickCollection,
  onDrillMissed,
  onStartSet,
  busy = false,
}: {
  getHeaders: () => HeadersInit;
  scope: PracticeScope;
  setScope: (s: PracticeScope) => void;
  onSize?: (n: number) => void;
  /** Question sets that are not a paper (look-alikes, hardened, NTS, English, checked MCQs...). */
  collections: Collection[];
  pickedCollection: string | null;
  onPickCollection: (name: string | null) => void;
  onDrillMissed: () => void;
  onStartSet: (quizSetId: string, label: string) => void;
  busy?: boolean;
}) {
  const [tree, setTree] = useState<Tree | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [aiSets, setAiSets] = useState<AiSet[] | null>(null);
  const [missed, setMissed] = useState<number | null>(null);
  const [setPick, setSetPick] = useState<string>("");
  const [choice, setChoice] = useState<Choice | null>(
    scope.paper === "pget1" ? "pget" : scope.paper === "2" ? "paper2" : scope.paper === "1" ? "paper1" : pickedCollection ? "sets" : null
  );

  useEffect(() => {
    fetch(`${API}/api/practice/tree`, { headers: getHeaders(), credentials: "include" })
      .then(async (r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setTree)
      .catch((e) => setError(e.message));
    fetch(`${API}/api/chat/ai-quizzes`, { headers: getHeaders(), credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setAiSets(Array.isArray(d) ? d : []))
      .catch(() => setAiSets([]));
    fetch(`${API}/api/review/mistakes?limit=1000`, { headers: getHeaders(), credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setMissed(typeof d?.total === "number" ? d.total : null))
      .catch(() => setMissed(null));
  }, [getHeaders]);

  const paper1Total = useMemo(() => (tree ? tree.subjects.reduce((a, s) => a + both(s), 0) : 0), [tree]);
  const paper2Total = useMemo(() => (tree?.specialties || []).reduce((a, s) => a + both(s), 0), [tree]);
  const pgetTotal = useMemo(() => (tree?.pget || []).reduce((a, s) => a + s.count, 0), [tree]);

  // How many questions the selection covers (a past-paper question sat by two faculties counts
  // under both, so Paper 2 totals are an upper bound; the Confirm step shows the exact figure).
  const size = useMemo(() => {
    if (!tree) return 0;
    if (scope.paper === "pget1") {
      const picked = scope.subjects || [];
      return (tree.pget || []).filter((s) => !picked.length || picked.includes(s.subject)).reduce((a, s) => a + s.count, 0);
    }
    if (scope.paper === "2") {
      const picked = scope.specialties || [];
      return (tree.specialties || []).filter((s) => !picked.length || picked.includes(s.specialty)).reduce((a, s) => a + both(s), 0);
    }
    if (scope.paper === "1") {
      if (!scope.subjects.length) return paper1Total;
      let n = 0;
      for (const s of tree.subjects) {
        const topics = s.topics.filter((t) => scope.topics.includes(`${s.subject}|${t.topic}`));
        if (topics.length) n += topics.reduce((a, t) => a + both(t), 0);
        else if (scope.subjects.includes(s.subject)) n += both(s);
      }
      return n;
    }
    return 0;
  }, [tree, scope, paper1Total]);
  useEffect(() => { onSize?.(size); }, [size, onSize]);

  const choose = (c: Choice) => {
    setChoice(c);
    setSetPick("");
    if (c === "paper1") { onPickCollection(null); setScope({ ...EMPTY_SCOPE, paper: "1" }); }
    else if (c === "paper2") { onPickCollection(null); setScope({ ...EMPTY_SCOPE, paper: "2", specialties: [] }); }
    else if (c === "pget") { onPickCollection(null); setScope({ ...EMPTY_SCOPE, paper: "pget1", sources: ["bank"], subjects: [] }); }
    else { setScope({ ...EMPTY_SCOPE }); }
  };

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
  const toggleSpecialty = (name: string) => {
    const cur = scope.specialties || [];
    setScope({ ...scope, specialties: cur.includes(name) ? cur.filter((s) => s !== name) : [...cur, name] });
  };

  const card = (c: Choice, icon: React.ReactNode, title: string, text: string) => (
    <button type="button" className={`config-category-card ${choice === c ? "active" : ""}`} onClick={() => choose(c)} aria-pressed={choice === c}>
      <span className="config-category-title" style={{ display: "flex", alignItems: "center", gap: "6px" }}>{icon} {title}</span>
      <span className="config-category-subtitle">{text}</span>
    </button>
  );

  if (error) return <div className="error-banner" role="alert">Couldn&apos;t load the subject list ({error}).</div>;

  const pickedSet = setPick.startsWith("ai:") ? aiSets?.find((s) => `ai:${s.quiz_set_id}` === setPick) : null;

  return (
    <div className="practice-picker">
      <div className="config-category-grid" role="radiogroup" aria-label="What to practise">
        {card("paper1", <BookMarked size={14} />, "Paper 1 · Basic sciences",
          tree ? `Anatomy, Physiology, Pathology, Pharmacology and the rest · ${paper1Total.toLocaleString()} questions` : "Loading…")}
        {card("paper2", <Stethoscope size={14} />, "Paper 2 · Specialty",
          tree ? `Your faculty's paper: Medicine, Surgery, Gynae & Obs… · ${paper2Total.toLocaleString()} questions` : "Loading…")}
        {card("pget", <GraduationCap size={14} />, "PGET Paper 1",
          tree ? `MBBS Basic sciences · ${pgetTotal.toLocaleString()} questions` : "Loading…")}
        {card("missed", <RotateCcw size={14} />, "Missed MCQs",
          missed === null ? "Questions you got wrong and have not yet put right" : `${missed.toLocaleString()} still to put right`)}
        {card("sets", <Sparkles size={14} />, "Special sets",
          `Your AI MCQ sets${aiSets?.length ? ` (${aiSets.length})` : ""}, look-alikes, hardened and more`)}
      </div>

      {!tree && (choice === "paper1" || choice === "paper2" || choice === "pget") ? (
        <div style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}><Loader2 size={13} className="animate-spin" /> Loading subjects…</div>
      ) : null}

      {choice === "paper1" && tree ? (
        <>
          <div className="practice-picker-label" style={{ marginTop: "var(--sp-2)" }}>
            Subjects <span style={{ fontWeight: 400, textTransform: "none" }}>— none picked = all of Paper 1; bank and past papers together</span>
          </div>
          <div className="practice-subjects" role="group" aria-label="Paper 1 subjects">
            {tree.subjects.map((s) => {
              const n = both(s);
              const picked = scope.subjects.includes(s.subject);
              const pickedTopics = scope.topics.filter((t) => t.startsWith(`${s.subject}|`)).length;
              const expanded = open === s.subject;
              return (
                <div key={s.subject} className={`practice-subject ${picked ? "active" : ""}`}>
                  <div className="practice-subject-head">
                    <button type="button" className="practice-subject-pick" onClick={() => toggleSubject(s.subject)} disabled={!n && !picked} aria-pressed={picked}>
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
                        const tn = both(t);
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
        </>
      ) : null}

      {choice === "paper2" && tree ? (
        <>
          <div className="practice-picker-label" style={{ marginTop: "var(--sp-2)" }}>
            Specialty <span style={{ fontWeight: 400, textTransform: "none" }}>— none picked = every specialty; bank and past papers together</span>
          </div>
          <div className="practice-topics" role="group" aria-label="Paper 2 specialties">
            {(tree.specialties || []).map((s) => {
              const n = both(s);
              const on = (scope.specialties || []).includes(s.specialty);
              return (
                <button key={s.specialty} type="button" disabled={!n && !on} aria-pressed={on}
                  className={`practice-chip ${on ? "active" : ""}`} onClick={() => toggleSpecialty(s.specialty)}
                  title={`${s.bank.toLocaleString()} in the bank · ${s.past.toLocaleString()} in past papers`}>
                  {s.specialty} <span className="practice-chip-count">{n.toLocaleString()}</span>
                </button>
              );
            })}
          </div>
        </>
      ) : null}

      {choice === "pget" && tree ? (
        <>
          <div className="practice-picker-label" style={{ marginTop: "var(--sp-2)" }}>
            Subjects <span style={{ fontWeight: 400, textTransform: "none" }}>— none picked = all PGET Paper 1 subjects</span>
          </div>
          <div className="practice-topics" role="group" aria-label="PGET subjects">
            {(tree.pget || []).map((s) => {
              const on = scope.subjects.includes(s.subject);
              return (
                <button key={s.subject} type="button" disabled={!s.count && !on} aria-pressed={on}
                  className={`practice-chip ${on ? "active" : ""}`} onClick={() => toggleSubject(s.subject)}>
                  {s.subject} <span className="practice-chip-count">{s.count.toLocaleString()}</span>
                </button>
              );
            })}
          </div>
        </>
      ) : null}

      {choice === "missed" ? (
        <div className="practice-summary" style={{ justifyContent: "space-between" }}>
          <span>
            {missed ? <>Practise the <b>{missed.toLocaleString()}</b> questions whose latest answer was wrong, from any feature.</>
              : missed === 0 ? "Nothing missed yet: every question you have answered, you last got right." : "Questions you got wrong and have not yet put right."}
          </span>
          <button type="button" className="btn-primary" onClick={onDrillMissed} disabled={busy || missed === 0}
            style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />} Start
          </button>
        </div>
      ) : null}

      {choice === "sets" ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "var(--sp-2)" }}>
          <label className="practice-picker-label" htmlFor="special-set">Set</label>
          <select id="special-set" value={setPick || (pickedCollection ? `col:${pickedCollection}` : "")}
            onChange={(e) => {
              const v = e.target.value;
              setSetPick(v);
              onPickCollection(v.startsWith("col:") ? v.slice(4) : null);
            }}
            style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid var(--border-light)", background: "var(--surface-3)", color: "var(--text-primary)", fontSize: "0.86rem" }}>
            <option value="">Choose a set…</option>
            {aiSets && aiSets.length ? (
              <optgroup label="Your AI MCQ sets">
                {aiSets.map((s) => (
                  <option key={s.quiz_set_id} value={`ai:${s.quiz_set_id}`}>{s.quiz_set_title} · {s.question_count} MCQs</option>
                ))}
              </optgroup>
            ) : null}
            {collections.length ? (
              <optgroup label="Collections">
                {collections.map((c) => (
                  <option key={c.name} value={`col:${c.name}`}>{c.name} · {c.count.toLocaleString()} MCQs</option>
                ))}
              </optgroup>
            ) : null}
          </select>
          {aiSets && !aiSets.length ? (
            <div style={{ fontSize: "0.74rem", color: "var(--text-muted)" }}>
              <Layers size={11} style={{ verticalAlign: -1 }} /> No AI sets yet: write one in the AI MCQs tab and it appears here.
            </div>
          ) : null}
          {pickedSet ? (
            <button type="button" className="btn-primary" disabled={busy} onClick={() => onStartSet(pickedSet.quiz_set_id, pickedSet.quiz_set_title)}
              style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: "6px" }}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />} Start this set
            </button>
          ) : null}
        </div>
      ) : null}

      {choice === "paper1" || choice === "paper2" || choice === "pget" ? (
        <>
          <div className="practice-picker-label" style={{ marginTop: "var(--sp-2)" }}>Difficulty</div>
          <div className="segmented" role="radiogroup" aria-label="Difficulty">
            {DIFFICULTIES.map((d) => (
              <button key={d.label} type="button" role="radio" aria-checked={scope.difficulty === d.value} title={d.title}
                className={`segmented-item ${scope.difficulty === d.value ? "active" : ""}`}
                onClick={() => setScope({ ...scope, difficulty: d.value })}>
                {d.label}
              </button>
            ))}
          </div>
          <div className="practice-summary">
            Selected: <b>{size.toLocaleString()}</b> questions{choice === "paper2" ? " (some past-paper questions were sat by more than one faculty)" : ""}
            {scope.difficulty ? <> · only rated questions count for a difficulty, so fewer will be available</> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
