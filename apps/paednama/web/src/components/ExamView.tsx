"use client";

import React, { useEffect, useState } from "react";
import { ClipboardList } from "lucide-react";
import PageShell from "@/components/layout/PageShell";
import { api } from "~/lib/api";
import type { StageId, StageSummary } from "~/lib/types";

/** The exam as the CPSP curriculum states it, with the page for every fact. */
export default function ExamView({ stage, onStage }: { stage: StageId; onStage: (s: StageId) => void }) {
  const [data, setData] = useState<{ source: string; note: string; stages: StageSummary[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<{ source: string; note: string; stages: StageSummary[] }>("/api/paeds/exams").then(setData).catch((e) => setError(e.message));
  }, []);
  const st = data?.stages.find((s) => s.id === stage);

  return (
    <PageShell title="The exam" icon={<ClipboardList size={22} />} loading={!data && !error} error={error}
      subtitle="What CPSP assesses, and how. Every fact links to its page in the curriculum."
      actions={
        <div className="segmented" role="radiogroup" aria-label="Exam">
          {(["imm", "fcps2"] as StageId[]).map((s) => (
            <button key={s} type="button" role="radio" aria-checked={stage === s}
              className={`segmented-item ${stage === s ? "active" : ""}`} onClick={() => onStage(s)}>
              {s === "imm" ? "IMM" : "FCPS-II"}
            </button>
          ))}
        </div>
      }>
      {st ? (
        <>
          <div className="pn-facts">
            <div className="pn-fact">
              <span className="tile-kicker">Theory</span>
              {st.theory.papers.map((p) => <div key={p.name}><b>{p.name}</b>: {p.format}</div>)}
              <span className="tile-text">{st.theory.gate}.</span>
              <span className="pn-src">Curriculum p.{st.theory.page}</span>
            </div>
            {st.clinical.map((c) => (
              <div key={c.component} className="pn-fact">
                <span className="tile-kicker">Clinical</span>
                <h3>{c.component}</h3>
                {c.stations ? <div><span className="pn-big">{c.stations}</span> stations · {c.changeover_minutes} min to change</div> : null}
                {c.minutes ? <div><span className="pn-big">{c.minutes}</span> min{c.count ? ` · ${c.count} case${c.count === 1 ? "" : "s"}` : ""}</div> : null}
                {c.examiner ? <span className="tile-text">{c.examiner}</span> : null}
                {c.tasks ? <span className="tile-text">Tasks: {c.tasks.join(", ")}.</span> : null}
                {c.parts ? <ul>{c.parts.map((x) => <li key={x}>{x}</li>)}</ul> : null}
                <span className="pn-src">Curriculum p.{c.page}</span>
              </div>
            ))}
          </div>

          {st.clinical.filter((c) => c.assessed_on).map((c) => (
            <section key={`a-${c.component}`} style={{ marginTop: "var(--sp-5)" }}>
              <h2 className="today-h2">{c.component}: what the examiners mark</h2>
              <div className="pn-facts">
                {Object.entries(c.assessed_on!).map(([area, items]) => (
                  <div key={area} className="pn-fact">
                    <h3>{area}</h3>
                    <ul>{items.map((i) => <li key={i}>{i}</li>)}</ul>
                  </div>
                ))}
              </div>
              {c.note ? <p className="tile-text" style={{ marginTop: 8 }}>{c.note}.</p> : null}
            </section>
          ))}

          <section style={{ marginTop: "var(--sp-5)" }}>
            <h2 className="today-h2">To be eligible</h2>
            <ul className="tile-text">{st.eligibility.items.map((i) => <li key={i}>{i}</li>)}</ul>
            <span className="pn-src">Curriculum p.{st.eligibility.page}</span>
          </section>
          {st.rotations ? (
            <section style={{ marginTop: "var(--sp-5)" }}>
              <h2 className="today-h2">Rotations</h2>
              <ul className="tile-text">{st.rotations.items.map((r) => <li key={r.name}>{r.name}: {r.months} months{r.each ? " each" : ""}</li>)}</ul>
              <span className="pn-src">Curriculum p.{st.rotations.page}</span>
            </section>
          ) : null}
          <p className="pn-src" style={{ marginTop: "var(--sp-5)" }}>{data?.source}. {data?.note}</p>
        </>
      ) : null}
    </PageShell>
  );
}
