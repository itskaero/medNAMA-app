"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ArrowRight, ArrowUp, BookOpenCheck, CalendarClock, ChevronDown, Flame, GraduationCap, Stethoscope } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";
import PageShell from "@/components/layout/PageShell";
import { VoiceButton } from "@/components/VoiceButton";
import { ReadinessCard, type Readiness } from "@/components/ReadinessCard";
import type { PracticeScope } from "@/components/PracticePicker";
import type { NavParams, ViewId } from "@/lib/nav";

interface TodayData {
  continue: { attempt_id: number; label: string; answered: number; total: number } | null;
  dose: { started: boolean; completed: boolean; done: number; total: number };
  focus: { subject: string; topic: string; answered: number; accuracy: number | null }[];
  /** Paper 1 topics by weakness x how often past papers ask them (backend app/study_plan.py). */
  plan_topics?: PlanTopic[];
}
interface PlanTopic {
  kind: "weak" | "untouched";
  subject: string;
  topic: string;
  past_questions: number;
  answered: number;
  accuracy: number | null;
  scope: PracticeScope;
}
interface TreeSubject { subject: string; bank: number; past: number }
/** A topic to work on: from the yield-weighted plan when the server has one, else the mastery map's weak topics. */
type Focus = { subject: string; topic: string; answered: number; accuracy: number | null; kind?: "weak" | "untouched";
  past_questions?: number; scope?: PracticeScope };

/** One thing to do today. The first becomes "Next up"; the rest are a short list under it. */
type PlanItem =
  | { kind: "continue"; c: NonNullable<TodayData["continue"]> }
  | { kind: "dose"; dose: TodayData["dose"] }
  | { kind: "focus"; f: Focus }
  | { kind: "examdate" };

/** The paper-driven plan (backend app/routers/prep.py /api/today/plan): one next step and the week's target. */
interface PaperPlan {
  profile: { paper_title: string | null; exam_level: string | null };
  days_left: number | null;
  due_reviews: number;
  topics_total: number;
  topics_practised: number;
  weekly_target: number | null;
  today_topics: { topic: string; subject: string; why: string; params: NavParams }[];
  primary: { kind: "continue" | "review" | "topic" | "mock" | "setup" | "dose"; label: string; why: string; view: ViewId; params: NavParams };
}

const DETAILS_KEY = "mednama_today_details";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}
const displayName = (u: string | null) => (u ? u.charAt(0).toUpperCase() + u.slice(1) : "");

/** Today: one next step, a box to ask Dr MedNama, a short plan, and readiness in one line (details on demand).
 *  Everything else stays one click away in Learn, Practise and Review. */
export default function TodayView({ token, username, getHeaders, onNavigate, onPractise, onPractiseScope, onResume, onAsk, onSetupPrep, planVersion }: {
  token: string | null;
  username: string | null;
  getHeaders: () => HeadersInit;
  onNavigate: (v: ViewId, params?: NavParams) => void;
  onPractise: (filters: Record<string, unknown>, label: string) => void;
  onPractiseScope: (scope: PracticeScope) => void;
  onResume: (attemptId: number) => void;
  onAsk: (question: string) => void;
  /** open the paper / exam date / daily time setup */
  onSetupPrep?: () => void;
  /** changes when the preparation profile is saved, so the plan reloads */
  planVersion?: string;
}) {
  const [paperPlan, setPaperPlan] = useState<PaperPlan | null>(null);
  const [data, setData] = useState<TodayData | null>(null);
  const [tree, setTree] = useState<TreeSubject[]>([]);
  const [ready, setReady] = useState<Readiness | null>(null);
  const [question, setQuestion] = useState("");
  const [examDate, setExamDate] = useState("");
  const [showDetails, setShowDetails] = useState(() => {
    try { return typeof window !== "undefined" && localStorage.getItem(DETAILS_KEY) === "1"; } catch { return false; }
  });

  const load = useCallback(async () => {
    const get = (path: string) =>
      fetch(`${API}${path}`, { headers: getHeaders(), credentials: "include" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    const [t, p, r, pp] = await Promise.all([get("/api/today"), get("/api/practice/tree"), get("/api/study/readiness"), get("/api/today/plan")]);
    if (t) setData(t);
    if (pp) setPaperPlan(pp);
    if (p?.subjects) setTree(p.subjects.map((s: { subject: string; bank?: number; past?: number }) => ({ subject: s.subject, bank: s.bank ?? 0, past: s.past ?? 0 })));
    if (r) setReady(r);
  }, [getHeaders]);
  useEffect(() => { load(); }, [load, planVersion]);

  const toggleDetails = () => {
    setShowDetails((open) => {
      try { localStorage.setItem(DETAILS_KEY, open ? "0" : "1"); } catch { /* storage unavailable */ }
      return !open;
    });
  };
  const reviseTopic = (topic: string) => {
    try { localStorage.setItem("mednama_revise_topic", topic); } catch { /* storage unavailable */ }
    onNavigate("revise");
  };
  const practiseTopic = (f: Focus) =>
    onPractise(f.scope ? { scope: f.scope, num_questions: 20, prefer_unseen: true }
      : { sub_categories: [f.subject], topics: [f.topic], num_questions: 20, prefer_unseen: true }, `Practice · ${f.topic}`);
  const ask = (e: React.FormEvent) => {
    e.preventDefault();
    if (question.trim()) onAsk(question.trim());
  };
  const saveExamDate = async () => {
    if (!examDate) return;
    const res = await fetch(`${API}/api/study/exam-date`, {
      method: "PUT",
      headers: { ...getHeaders(), "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ exam_date: examDate }),
    }).catch(() => null);
    if (res?.ok) { toast.success("Exam date saved."); load(); } else toast.error("Could not save the exam date.");
  };

  // The plan, most useful first: an unfinished session, today's Dose, the weakest topics, then the exam date.
  const plan: PlanItem[] = [];
  if (data?.continue) plan.push({ kind: "continue", c: data.continue });
  if (data && !data.dose.completed) plan.push({ kind: "dose", dose: data.dose });
  const topics: Focus[] = data?.plan_topics?.length ? data.plan_topics : data?.focus || [];
  for (const f of topics.slice(0, 2)) plan.push({ kind: "focus", f });
  if (ready && !ready.exam_date) plan.push({ kind: "examdate" });
  // When the paper plan's step is about the paper itself (a topic, a mock, due reviews), it leads and the
  // older list moves under "Also today"; an unfinished session or today's Dose still comes first otherwise.
  const paperFirst = !!paperPlan && ["topic", "mock", "review"].includes(paperPlan.primary.kind);
  const next: PlanItem | undefined = paperFirst ? undefined : plan[0];
  const rest = paperFirst ? plan : plan.slice(1);
  const runPrimary = () => {
    const p = paperPlan!.primary;
    if (p.kind === "setup") onSetupPrep?.();
    else if (p.kind === "continue") onResume(Number(p.params.resume));
    else onNavigate(p.view, p.params);
  };
  const isNew = !!ready && ready.predicted_score === null && !ready.subjects.length && !data?.continue;

  const contextLine = !ready ? null : isNew ? (
    <>Welcome to medNAMA. Start with today&apos;s Dose and it will learn where you stand.</>
  ) : (
    <span className="today-context">
      {ready.days_left !== null ? <span><b>{ready.days_left}</b> days to your exam</span> : null}
      {ready.streak.current > 0 ? (
        <span><Flame size={14} aria-hidden style={{ color: "var(--today-flame)", verticalAlign: -2 }} /> <b>{ready.streak.current}</b>-day streak</span>
      ) : null}
      {ready.daily_target ? <span>aim for <b>{ready.daily_target}</b> questions today</span> : null}
    </span>
  );

  return (
    <PageShell width="default" title={<>{greeting()}{username ? <>, <i style={{ color: "var(--sky)" }}>Dr. {displayName(username)}</i></> : null}</>}
      subtitle={contextLine}>
      {paperPlan && paperFirst ? <PlanCard plan={paperPlan} onPrimary={runPrimary} onNavigate={onNavigate} onSetup={onSetupPrep} /> : null}
      {paperFirst ? null : next ? <NextUp item={next} isNew={isNew} onNavigate={onNavigate} onResume={onResume} onPractise={practiseTopic} onRevise={reviseTopic}
        examDate={examDate} setExamDate={setExamDate} saveExamDate={saveExamDate} /> : data ? (
        <section className="today-next" aria-label="Next up">
          <h2 className="today-next-title">You&apos;re done for today</h2>
          <p className="today-next-text">Daily Dose finished. Ask a question below, or practise a subject if you want more.</p>
        </section>
      ) : null}
      {paperPlan && !paperFirst && (paperPlan.topics_total || paperPlan.primary.kind === "setup") ? (
        <PlanCard plan={paperPlan} onPrimary={runPrimary} onNavigate={onNavigate} onSetup={onSetupPrep}
          compact={paperPlan.primary.kind !== "setup"} />
      ) : null}

      <form className="today-ask" onSubmit={ask} role="search">
        <Stethoscope size={18} aria-hidden className="today-ask-icon" />
        <input value={question} onChange={(e) => setQuestion(e.target.value)} aria-label="Ask Dr MedNama"
          placeholder="Ask Dr MedNama anything…" title="Answers come from your textbooks, with the page." />
        <VoiceButton value={question} onChange={setQuestion} />
        <button type="submit" className="today-ask-send" disabled={!question.trim()} aria-label="Ask">
          <ArrowUp size={16} />
        </button>
      </form>

      {rest.length ? (
        <section aria-label="Also today" className="today-plan">
          <h2 className="today-h2">Also today</h2>
          <ul>
            {rest.slice(0, 3).map((item) => (
              <PlanRow key={planKey(item)} item={item} onNavigate={onNavigate} onResume={onResume} onPractise={practiseTopic}
                onRevise={reviseTopic} examDate={examDate} setExamDate={setExamDate} saveExamDate={saveExamDate} />
            ))}
          </ul>
        </section>
      ) : null}

      {ready ? (
        <section aria-label="Readiness" className="today-ready">
          <div className="today-ready-row">
            <div className="today-ready-text">
              <h2 className="today-h2">Readiness</h2>
              {ready.predicted_score === null ? (
                <span className="today-muted">Answer 10+ questions in a subject to see your estimate.</span>
              ) : (
                <span>
                  <b className="today-ready-score" data-pass={ready.predicted_score >= ready.pass_line || undefined}>
                    {Math.round(ready.predicted_score * 100)}%
                  </b>
                  <span className="today-muted"> against the {Math.round(ready.pass_line * 100)}% pass line</span>
                </span>
              )}
            </div>
            <button type="button" className="today-link" onClick={toggleDetails} aria-expanded={showDetails}>
              {showDetails ? "Hide details" : "Details"} <ChevronDown size={14} className="today-chev" data-open={showDetails || undefined} />
            </button>
          </div>
          {ready.predicted_score !== null ? (
            <div className="today-meter" role="img" aria-label={`Readiness ${Math.round(ready.predicted_score * 100)}%, pass line ${Math.round(ready.pass_line * 100)}%`}>
              <span style={{ width: `${ready.predicted_score * 100}%` }} />
              <i style={{ left: `${ready.pass_line * 100}%` }} />
            </div>
          ) : null}
          {showDetails ? (
            <div className="today-details">
              <ReadinessCard token={token} onOpenLookalikes={() => onNavigate("lookalikes")} onOpenSprint={() => onNavigate("sprint")} onSaved={load} />
            </div>
          ) : null}
        </section>
      ) : null}

      {tree.length ? (
        <section aria-label="Practise a subject" className="today-subjects">
          <h2 className="today-h2">Practise a subject</h2>
          <div className="today-chips">
            {tree.filter((s) => s.bank + s.past > 0).map((s) => (
              <button key={s.subject} type="button" className="today-chip" title={`${(s.bank + s.past).toLocaleString()} questions`}
                onClick={() => onPractiseScope({ sources: ["past", "bank"], subjects: [s.subject], topics: [], years: [] })}>
                {s.subject}
              </button>
            ))}
            <button type="button" className="today-link" onClick={() => onNavigate("quiz")}>
              Topics, papers and more <ArrowRight size={13} />
            </button>
          </div>
        </section>
      ) : null}
    </PageShell>
  );
}

/** The paper plan: where the learner stands on their paper, this week's target, and today's topics. */
function PlanCard({ plan, onPrimary, onNavigate, onSetup, compact = false }: {
  plan: PaperPlan; onPrimary: () => void; onNavigate: (v: ViewId, params?: NavParams) => void; onSetup?: () => void; compact?: boolean;
}) {
  const p = plan.primary;
  const share = plan.topics_total ? plan.topics_practised / plan.topics_total : 0;
  return (
    <section className="plan-card" aria-label="Your plan">
      <div className="plan-kicker">
        {plan.profile.paper_title ? <>Your paper · {plan.profile.paper_title}</> : <>Your plan</>}
        {onSetup && plan.profile.paper_title ? <> · <button type="button" className="today-link" onClick={onSetup}>change</button></> : null}
      </div>
      {!compact ? (
        <div className="plan-main">
          <div>
            <div className="plan-label">{p.label}</div>
            <div className="plan-why">{p.why}</div>
          </div>
          <button type="button" className="btn-primary" onClick={onPrimary}>
            {p.kind === "setup" ? "Set up" : p.kind === "mock" ? "Build the mock" : "Start"} <ArrowRight size={14} />
          </button>
        </div>
      ) : null}
      {plan.topics_total ? (
        <>
          <div className="plan-stats">
            <span><b>{plan.topics_practised}</b> of <b>{plan.topics_total}</b> topics practised</span>
            {plan.weekly_target !== null ? <span>this week: <b>{plan.weekly_target}</b> topic{plan.weekly_target === 1 ? "" : "s"}</span> : null}
            {plan.due_reviews ? <span><b>{plan.due_reviews}</b> reviews due</span> : null}
          </div>
          <div className="plan-bar" role="img" aria-label={`${Math.round(share * 100)}% of topics practised`}>
            <span style={{ width: `${share * 100}%` }} />
          </div>
        </>
      ) : null}
      {plan.today_topics.length > (compact ? 0 : 1) ? (
        <div className="plan-topics" aria-label="Today's topics">
          {plan.today_topics.slice(compact ? 0 : 1).map((t) => (
            <button key={t.topic} type="button" className="practice-chip" title={`${t.subject} · ${t.why}`}
              onClick={() => onNavigate("tos", t.params)}>
              {t.topic}
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

const planKey = (i: PlanItem) => (i.kind === "focus" ? `focus|${i.f.subject}|${i.f.topic}` : i.kind);

interface ItemProps {
  item: PlanItem;
  onNavigate: (v: ViewId) => void;
  onResume: (attemptId: number) => void;
  onPractise: (f: Focus) => void;
  onRevise: (topic: string) => void;
  examDate: string;
  setExamDate: (v: string) => void;
  saveExamDate: () => void;
}

function describe(item: PlanItem, isNew = false): { title: string; text: string } {
  switch (item.kind) {
    case "continue":
      return { title: `Pick up ${item.c.label}`, text: `${item.c.answered} of ${item.c.total} answered. Carry on where you stopped.` };
    case "dose":
      return item.dose.started
        ? { title: "Finish today's Daily Dose", text: `${item.dose.done} of ${item.dose.total} done. About 10 minutes in all.` }
        : { title: isNew ? "Start your first Daily Dose" : "Start today's Daily Dose",
            text: isNew ? "A short mixed set, about 10 minutes. It finds your weak subjects so tomorrow's plan fits you."
                        : "Re-tests that are due, your weakest subject, an image and a pearl. About 10 minutes." };
    case "focus": {
      const f = item.f;
      const asked = f.past_questions ? ` · asked in ${f.past_questions.toLocaleString()} past-paper question${f.past_questions === 1 ? "" : "s"}` : "";
      if (f.kind === "untouched")
        return { title: isNew ? `Start with ${f.topic}` : f.topic,
          text: `${f.subject} · ${f.answered ? `only ${f.answered} answered so far` : "not practised yet"}${asked}` };
      return { title: f.topic, text: `${f.subject} · ${f.accuracy ?? 0}% right on ${f.answered} answered${asked}` };
    }
    case "examdate":
      return { title: "When is your exam?", text: "Set the date and the plan paces your days to it." };
  }
}

function Actions({ item, primary, onNavigate, onResume, onPractise, onRevise, examDate, setExamDate, saveExamDate }: ItemProps & { primary?: boolean }) {
  const main = primary ? "btn-primary" : "btn-workspace";
  switch (item.kind) {
    case "continue":
      return <button type="button" className={main} onClick={() => onResume(item.c.attempt_id)}>Resume <ArrowRight size={14} /></button>;
    case "dose":
      return <button type="button" className={main} onClick={() => onNavigate("daily")}>{item.dose.started ? "Continue" : "Start"} <ArrowRight size={14} /></button>;
    case "focus":
      return (
        <>
          <button type="button" className={main} onClick={() => onPractise(item.f)}><GraduationCap size={14} /> Practise</button>
          <button type="button" className="btn-workspace" onClick={() => onRevise(item.f.topic)}><BookOpenCheck size={14} /> Revise</button>
        </>
      );
    case "examdate":
      return (
        <span className="today-date">
          <CalendarClock size={14} aria-hidden />
          <input type="date" value={examDate} onChange={(e) => setExamDate(e.target.value)} aria-label="Exam date" />
          <button type="button" className={main} onClick={saveExamDate} disabled={!examDate}>Save</button>
        </span>
      );
  }
}

function NextUp(props: ItemProps & { isNew: boolean }) {
  const { title, text } = describe(props.item, props.isNew);
  const c = props.item.kind === "continue" ? props.item.c : null;
  const d = props.item.kind === "dose" && props.item.dose.started ? props.item.dose : null;
  const progress = c ? c.answered / Math.max(1, c.total) : d ? d.done / Math.max(1, d.total) : null;
  return (
    <section className="today-next" aria-label="Next up">
      <h2 className="today-next-title">{title}</h2>
      <p className="today-next-text">{text}</p>
      {progress !== null ? <div className="today-progress" aria-hidden><span style={{ width: `${progress * 100}%` }} /></div> : null}
      <div className="today-next-actions"><Actions {...props} primary /></div>
    </section>
  );
}

function PlanRow(props: ItemProps) {
  const { title, text } = describe(props.item);
  return (
    <li className="today-row">
      <div className="today-row-text">
        <span className="today-row-title">{title}</span>
        <span className="today-muted">{text}</span>
      </div>
      <div className="today-row-actions"><Actions {...props} /></div>
    </li>
  );
}
