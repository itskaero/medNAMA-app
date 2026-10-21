"use client";

import React, { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, BookOpen, ClipboardCheck, Layers, Stethoscope, Syringe } from "lucide-react";
import { api } from "~/lib/api";
import { daysTo } from "~/lib/dates";
import type { PaedsBook, Profile, StageId, StageSummary, System, View } from "~/lib/types";

// The syllabus system that matches each rotation, so the dashboard starts where the learner is working.
const ROTATION_SYSTEM: Record<string, RegExp> = {
  Neonatology: /NEONATOLOGY/,
  Emergency: /EMERGENCY/,
  "Paediatric ICU": /CRITICAL|INTENSIVE/,
};

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}
const title = (s: string) => s.charAt(0) + s.slice(1).toLowerCase();

/** Dashboard: where you are, what the exam is, where to start (your rotation's topics), and the shape of the syllabus.
 *  Everything shown is real data from the curriculum and your setup; progress tracking arrives with the topic sheets. */
export default function HomeView({ username, profile, stage, exams, onSetup, go, onTopic, onOpenTopic }: {
  username: string;
  profile: Profile | null;
  stage: StageId;
  exams: StageSummary[];
  onSetup: () => void;
  go: (v: View) => void;
  onTopic: (q: string) => void;
  onOpenTopic: (id: string) => void;
}) {
  const [systems, setSystems] = useState<System[]>([]);
  const [miniCex, setMiniCex] = useState<string[]>([]);
  const [books, setBooks] = useState<PaedsBook[]>([]);
  useEffect(() => {
    api<{ systems: System[]; mini_cex: { topics: string[] } }>(`/api/paeds/syllabus?stage=${stage}`)
      .then((d) => { setSystems(d.systems); setMiniCex(d.mini_cex.topics); }).catch(() => {});
    api<{ books: PaedsBook[] }>("/api/paeds/books").then((d) => setBooks(d.books)).catch(() => {});
  }, [stage]);

  const ex = exams.find((e) => e.id === stage);
  const days = daysTo(profile?.exam_date);
  const stageLabel = stage === "fcps2" ? "FCPS-II" : "IMM";
  const name = username.charAt(0).toUpperCase() + username.slice(1);
  const toacs = ex?.clinical.find((c) => c.component === "TOACS");
  const longCase = ex?.clinical.find((c) => c.component === "Long case");

  const focus = useMemo(() => {
    const re = profile?.rotation ? ROTATION_SYSTEM[profile.rotation] : undefined;
    const sys = re ? systems.find((s) => re.test(s.system)) : undefined;
    return sys
      ? { title: `${profile!.rotation} rotation`, sub: `${title(sys.system)} · ${sys.topics.length} topics`,
          items: sys.topics.slice(0, 6).map((t) => ({ id: t.id as string | null, name: t.name, meta: t.subtopics.length ? `${t.subtopics.length} sub-topics` : "", page: t.page })) }
      : { title: "Mini-CEX topics", sub: "What examiners assess at work, and TOACS asks",
          items: miniCex.slice(0, 6).map((t) => ({ id: null as string | null, name: t, meta: "", page: null as number | null })) };
  }, [profile, systems, miniCex]);

  const heaviest = useMemo(() => [...systems].sort((a, b) => b.topics.length - a.topics.length).slice(0, 7), [systems]);
  const maxTopics = heaviest[0]?.topics.length || 1;
  const ready = books.filter((b) => b.status === "ready").length;
  const year = profile?.training_year ?? null;

  return (
    <div className="pn-dash">
      <div className="pn-col">
        <section className="pn-hero">
          <div>
            <div className="pn-hero-kicker">{greeting()}, Dr. {name}</div>
            <h1>{days !== null ? <>Your {stageLabel} is <em>{days} days</em> away</> : <>Prepare for Paediatrics <em>{stageLabel}</em></>}</h1>
            <p>
              {ex ? `${ex.topics} topics across ${ex.systems} systems in the 2026 CPSP curriculum.` : "The 2026 CPSP curriculum, topic by topic."}
              {profile?.rotation && ROTATION_SYSTEM[profile.rotation] ? ` Start with what you see on ${profile.rotation.toLowerCase()} this week.` : ""}
            </p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="btn-primary" onClick={() => go("syllabus")}>Open the syllabus <ArrowUpRight size={14} /></button>
              <button type="button" className="btn-workspace" onClick={() => go("exam")}>How it&apos;s marked</button>
            </div>
          </div>
          {ex ? (
            <div className="pn-hero-cards" aria-label="The exam at a glance">
              <div className="pn-hcard paper"><small>01 · THEORY</small><b>{ex.theory.papers.length}×{ex.theory.papers[0]?.questions}</b><span>single-best MCQs</span></div>
              <div className="pn-hcard mark"><small>02 · TOACS</small><b>{toacs?.stations?.replace(" to ", "–")}</b><span>interactive stations</span></div>
              {longCase ? (
                <div className="pn-hcard"><small>03 · LONG CASE</small><b>{longCase.minutes}′</b><span>+ {ex.clinical.find((c) => c.component === "Short cases")?.count} short cases</span></div>
              ) : (
                <div className="pn-hcard"><small>03 · AT WORK</small><b>{ex.mini_cex}</b><span>Mini-CEX topics</span></div>
              )}
            </div>
          ) : null}
        </section>

        <div className="pn-stats">
          <div className="pn-stat"><span className="lbl"><Layers size={15} />Systems</span><b>{ex?.systems ?? "—"}</b><small>in the {stageLabel} syllabus</small></div>
          <div className="pn-stat"><span className="lbl"><BookOpen size={15} />Topics</span><b>{ex?.topics ?? "—"}</b><small>each with its page</small></div>
          <div className="pn-stat"><span className="lbl"><Stethoscope size={15} />Mini-CEX</span><b>{ex?.mini_cex ?? "—"}</b><small>clinical assessments</small></div>
          <div className="pn-stat"><span className="lbl"><Syringe size={15} />DOPS</span><b>{ex?.dops ?? "—"}</b><small>procedures</small></div>
        </div>

        <section className="pn-panel">
          <div className="pn-panel-head">
            <div><h2>{focus.title}</h2><small>{focus.sub}</small></div>
            <button type="button" className="pn-link" onClick={() => go("syllabus")}>See all</button>
          </div>
          <div className="pn-list">
            {focus.items.map((t, i) => (
              <button key={t.name} type="button" className="pn-row-item" onClick={() => (t.id ? onOpenTopic(t.id) : onTopic(t.name))}>
                <span className="ic">{String(i + 1).padStart(2, "0")}</span>
                <div style={{ minWidth: 0 }}><div className="t">{t.name}</div>{t.meta ? <div className="s">{t.meta}</div> : null}</div>
                <span className="p">{t.page ? `p.${t.page}` : ""}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="pn-panel">
          <div className="pn-panel-head">
            <div><h2>Where the syllabus is heaviest</h2><small>Topics per system</small></div>
          </div>
          <div className="pn-bars">
            {heaviest.map((s) => (
              <div key={s.id} className="pn-bar" title={title(s.system)}>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title(s.system).replace(/^Paediatric /, "")}</span>
                <div><span style={{ width: `${(s.topics.length / maxTopics) * 100}%` }} /></div>
                <em>{s.topics.length}</em>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="pn-col">
        <section className="pn-panel">
          <div className="pn-panel-head"><h2>My preparation</h2><button type="button" className="pn-link" onClick={onSetup}>Edit</button></div>
          <div className="pn-prep">
          <div className="pn-ring" role="img" aria-label={year ? `Training year ${year} of 4` : "Training year not set"} style={{ ["--p" as string]: year ? (year / 4) * 100 : 0 } as React.CSSProperties}>
            <div><b>{year ? `Y${year}` : "—"}</b><span>of 4</span></div>
          </div>
          <dl className="pn-kv">
            <dt>Exam</dt><dd>{stageLabel}</dd>
            <dt>Date</dt><dd>{profile?.exam_date ? new Date(profile.exam_date).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "Not set"}</dd>
            <dt>Rotation</dt><dd>{profile?.rotation ?? "Not set"}</dd>
            <dt>Daily</dt><dd>{profile ? (profile.minutes_per_day >= 120 ? "2 hours+" : profile.minutes_per_day >= 60 ? "1 hour" : `${profile.minutes_per_day} min`) : "—"}</dd>
          </dl>
          </div>
        </section>

        <section className="pn-panel">
          <div className="pn-panel-head"><h2>The clinical exam</h2><button type="button" className="pn-link" onClick={() => go("exam")}>Details</button></div>
          <div className="pn-mini">
            {ex?.clinical.map((c) => (
              <div key={c.component} className="pn-mini-item">
                <b>{c.component}</b>
                <span>{c.stations ? `${c.stations} interactive stations, ${c.changeover_minutes} min to change` : c.minutes ? `${c.count} · ${c.minutes} minutes` : ""}</span>
                <div className="pn-tag">Curriculum p.{c.page}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="pn-panel">
          <div className="pn-panel-head"><h2>Library</h2><button type="button" className="pn-link" onClick={() => go("library")}>Open</button></div>
          <div className="pn-bar" style={{ gridTemplateColumns: "1fr 44px" }}>
            <div><span style={{ width: `${books.length ? (ready / books.length) * 100 : 0}%` }} /></div>
            <em>{ready}/{books.length}</em>
          </div>
          <span className="tile-text">{ready} of {books.length} Paediatrics books ready to search{books.length > ready ? "; the rest are still being read in." : "."}</span>
          <div className="pn-mini">
            {books.filter((b) => b.role === "spine" || b.role === "reference").map((b) => (
              <div key={b.id} className="pn-mini-item"><b>{b.title}</b><span>{b.role_label}</span></div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
