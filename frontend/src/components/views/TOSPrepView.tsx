"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Compass,
  FileText,
  Layers,
  LineChart,
  Lightbulb,
  ListOrdered,
  Loader2,
  Search,
  Sparkles,
  Target,
  Timer,
  Trophy,
  X,
  XCircle,
} from "lucide-react";
import PageShell from "@/components/layout/PageShell";
import QuestionPlayer, { QuestionNavigator } from "@/components/QuestionPlayer";
import SessionSummary from "@/components/SessionSummary";
import { API } from "@/lib/constants";
import type { NavParams } from "@/lib/nav";
import { parseMarkdown } from "@/utils/markdown";
import { toast } from "sonner";

// ─── Types (backend: app/routers/tos.py) ──────────────────────────────────────

interface PaperSummary {
  id: string;
  title: string;
  specialty?: string | null;
  paper?: string;
  category?: string;
  total_mcqs: number;
  subjects_count: number;
  weighted: boolean;
  available: boolean;
}

interface ExamFamily {
  id: string;
  title: string;
  short_name: string;
  authority: string;
  version: string;
  source?: string;
  papers: PaperSummary[];
}

interface SubtopicDetail {
  name: string;
  bank_mcq_count: number | null;
  has_notes: boolean;
  accuracy: number | null;
}

interface TOSTopic {
  id: string;
  topic_name: string;
  subtopics: string[];
  subtopic_details: SubtopicDetail[];
  /** null: the paper's questions are not mapped to topics yet (unknown, not zero). */
  bank_mcq_count: number | null;
  attempted_count: number;
  correct_count: number;
  accuracy: number | null;
  mastery_estimable: boolean;
  has_notes: boolean;
}

interface TOSSubject {
  id: string;
  subject_name: string;
  official_mcq_count: number | null;
  bank_mcq_count: number | null;
  attempted_count: number;
  accuracy: number | null;
  topics_with_questions: number | null;
  topics: TOSTopic[];
}

interface PaperTree {
  id: string;
  title: string;
  total_mcqs: number;
  weighted: boolean;
  available: boolean;
  tagged: boolean;
  source?: string;
  version?: string;
  bank_mcq_count: number | null;
  attempted_count: number;
  accuracy: number | null;
  subjects: TOSSubject[];
}

interface Question {
  id: number;
  question_text: string;
  options: Record<string, string>;
  correct_option: string;
  difficulty?: number;
  explanation_markdown?: string;
  source?: string;
  grounding?: string | null;
  figure_id?: number | null;
}

interface PlanQuota {
  subject_id: string;
  subject_name: string;
  official_quota: number | null;
  quota: number;
  quota_source: "official" | "internal";
  available_in_bank: number;
  supplied: number;
  sufficient: boolean;
  deviation: number;
  mini_questions: number;
  mini_minutes: number;
}

/** /api/tos/mock/audit: the blueprint allocation and what the bank can supply. */
interface MockPlan {
  title: string;
  tagged: boolean;
  weighted: boolean;
  official_total_mcqs: number;
  bank_total_available: number;
  planned_questions: number;
  supplied_questions: number;
  minutes: number;
  is_compliant: boolean;
  compliance_note: string;
  deviations: string[];
  quotas: PlanQuota[];
}

interface Sheet { subtopic?: string; label?: string; markdown: string; citations: Citation[] }
interface Citation { n?: number; book_title: string; chapter?: string | null; page_number?: number | null }
interface SheetResponse {
  available: boolean;
  cached?: boolean;
  origin?: "seed" | "cache" | "generated";
  label: string;
  markdown: string;
  citations: Citation[];
  sheets?: Sheet[];
  message?: string;
}

interface SessionResults {
  accuracy: number;
  correct_count: number;
  total_attempted: number;
  answered: number;
  mode: string;
  weak_subtopics: { subtopic: string; missed: number }[];
  missed_questions?: { question_text: string; selected_option?: string | null; correct_option: string; explanation_markdown?: string; source?: string }[];
  difficulty_breakdown?: { difficulty: number; correct: number; total: number; accuracy: number }[];
  topic_answers: number;
  topic_accuracy: number | null;
  mastery_threshold: number;
}

interface Progress {
  topics_total: number;
  topics_practised: number;
  answers: number;
  accuracy: number | null;
  mastery_threshold: number;
  trend: { week: string; answers: number; accuracy: number }[];
  difficulty: { difficulty: number; answers: number; accuracy: number }[];
  repeatedly_missed: { mcq_id: number; misses: number; topic: string; stem: string }[];
  notes_opened: number;
  suggestions: { topic_id: string; subject: string; topic: string; answers: number; accuracy: number | null; why: string }[];
}

interface ActiveTopic { subject: TOSSubject; topic: TOSTopic; subtopic: string | null }

const FULL = "full";
const COUNTS = [5, 10, 20, 40];
const tone = (acc: number | null | undefined) =>
  acc === null || acc === undefined ? undefined : acc >= 75 ? "good" : acc >= 50 ? "mid" : "bad";

/** A server error as a sentence: FastAPI sends validation errors as a list of objects, gateways send HTML. */
function errorText(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)));
    return `The request was not accepted: ${msgs.join("; ")}`;
  }
  if (status === 401) return "Please sign in again.";
  if (status === 502 || status === 504 || status === 524) return "The server took too long to answer. Try again in a minute.";
  return `Request failed (${status})`;
}

/** Which stream chips an exam offers, and whether a paper belongs to one. */
function streamsFor(examId: string): { id: string; label: string }[] {
  if (examId === "mbbs") {
    return [{ id: "all", label: "All years" }, ...[1, 2, 3, 4].map((y) => ({ id: `y${y}`, label: `Year ${y}` })),
      { id: "y5", label: "Year 5 · Clerkships" }];
  }
  if (examId === "pget") return [{ id: "all", label: "All papers" }, { id: "basic", label: "Paper-I · Basic sciences" }, { id: "clinical", label: "Paper-II · Specialties" }];
  if (examId === "fcps1") return [{ id: "all", label: "All papers" }, { id: "p1", label: "Paper 1 · Basic sciences" }, { id: "p2", label: "Paper 2 · Specialty" }];
  return [{ id: "all", label: "All papers" }];
}
function inStream(p: PaperSummary, examId: string, stream: string): boolean {
  if (stream === "all") return true;
  if (examId === "mbbs") return p.id.startsWith(`${stream}-`);
  if (examId === "pget") return stream === "basic" ? p.category === "basic" : p.category !== "basic";
  if (examId === "fcps1") return stream === "p1" ? p.id.endsWith("-p1") : p.id.endsWith("-p2");
  return true;
}
function paperBadge(p: PaperSummary, examId: string): { badge: string; title: string } {
  if (examId === "mbbs") {
    const year = p.title.match(/Year-(\d)/)?.[1];
    // "Year-1 (1st Professional) · Block-I (Foundation-I, Hematopoietic & Lymphatic-I)": the block's own modules
    // are in the last brackets, not the first.
    const modules = [...p.title.matchAll(/\(([^)]+)\)/g)].map((m) => m[1]).pop();
    if (p.id.includes("block")) return { badge: `Year ${year} · ${p.paper || "Block"}`, title: modules || p.title };
    return { badge: `Year ${year} clerkship`, title: p.title.split("·")[1]?.trim() || p.title };
  }
  if (examId === "pget") {
    if (p.category === "basic") return p.id.includes("bds")
      ? { badge: "Paper-I · BDS", title: "Basic Medical Sciences (Dental)" }
      : { badge: "Paper-I · MBBS", title: "Basic Medical Sciences" };
    return { badge: "Paper-II · Clinical", title: p.specialty || p.title.replace(/^Paper-II\s*·\s*/, "") };
  }
  if (examId === "fcps1") return { badge: p.id.endsWith("-p1") ? "Paper 1 · Basic sciences" : "Paper 2 · Specialty", title: p.specialty || p.title };
  return { badge: `${p.total_mcqs} MCQs`, title: p.title };
}

function Citations({ items }: { items: Citation[] }) {
  if (!items?.length) return null;
  return (
    <div className="practice-picker-row">
      <span className="practice-picker-label">Sources</span>
      {items.map((c, i) => (
        <span key={i} className="tos-tag" title={c.chapter || undefined}>
          <BookOpen size={11} /> {c.n ? `[${c.n}] ` : ""}{c.book_title} · p. {c.page_number || "?"}
        </span>
      ))}
    </div>
  );
}

/** Difficulty bars, weak subtopics and the missed questions of a marked session. */
function ResultDetails({ results, onOpenSubtopic }: { results: SessionResults; onOpenSubtopic: (s: string) => void }) {
  return (
    <>
      {results.weak_subtopics?.length ? (
        <div className="tos-card">
          <span className="tile-kicker">Weak subtopics in this session · revise these</span>
          <div className="practice-picker-row">
            {results.weak_subtopics.map((w) => (
              <button key={w.subtopic} type="button" className="practice-chip" onClick={() => onOpenSubtopic(w.subtopic)}>
                <FileText size={12} /> {w.subtopic} <span className="practice-chip-count">{w.missed} missed</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {results.difficulty_breakdown?.length ? (
        <div className="tos-card">
          <span className="tile-kicker">By difficulty</span>
          <div className="tos-bars">
            {results.difficulty_breakdown.map((d) => (
              <div key={d.difficulty} className="tos-bar-row">
                <span className="tos-bar-label">Level {d.difficulty}</span>
                <span className="tos-meter"><i data-tone={tone(d.accuracy)} style={{ width: `${d.accuracy}%` }} /></span>
                <span className="tos-bar-value">{d.correct}/{d.total} · {d.accuracy}%</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {results.missed_questions?.length ? (
        <div className="tos-card">
          <span className="tile-kicker" style={{ color: "var(--error)" }}>
            <XCircle size={12} style={{ verticalAlign: -2 }} /> Worth revisiting
          </span>
          {results.missed_questions.map((m, idx) => (
            <div key={idx} className="tos-missed">
              <p className="tos-missed-stem">{m.question_text}</p>
              <div className="tos-missed-keys">
                <span className="qp-tag bad">You: {m.selected_option || "no answer"}</span>
                <span className="qp-tag ok">Answer: {m.correct_option}</span>
                {m.source ? <span className="qp-tag">{m.source}</span> : null}
              </div>
              {m.explanation_markdown ? (
                <div className="prose tos-prose-sm" dangerouslySetInnerHTML={{ __html: parseMarkdown(m.explanation_markdown) }} />
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}

/** The three ways to use TOS prep, in order, with where this learner is and one recommended next step. */
function TosPath({ progress, tree, plan, signedIn, onStudy, onTest }: {
  progress: Progress | null; tree: PaperTree; plan: MockPlan | null; signedIn: boolean;
  onStudy: (topicId: string) => void; onTest: () => void;
}) {
  const practised = progress?.topics_practised ?? 0;
  const total = progress?.topics_total ?? tree.subjects.reduce((n, s) => n + s.topics.length, 0);
  const share = total ? practised / total : 0;
  const step = !signedIn || practised === 0 ? 1 : share < 0.5 ? 2 : 3;
  const next = progress?.suggestions?.[0];
  const steps = [
    { n: 1, title: "Learn a topic", text: "Refresher, then the 1–3 minute notes, for a topic you have read." },
    { n: 2, title: "Practise it", text: "Questions on that topic only. Choose answers as you go, or at the end." },
    { n: 3, title: "Test yourself", text: "A timed mini-mock per subject, then the full paper, marked at the end." },
  ];
  return (
    <div className="tos-path">
      <ol className="tos-path-steps">
        {steps.map((x) => (
          <li key={x.n} className={x.n === step ? "now" : x.n < step ? "done" : ""}>
            <span className="tos-step-n">{x.n}</span>
            <span><b>{x.title}</b><span>{x.text}</span></span>
          </li>
        ))}
      </ol>
      <div className="tos-path-next">
        <span className="tile-kicker">Your next step</span>
        {!signedIn ? (
          <span className="tile-text">Sign in to have your practice recorded and get suggestions.</span>
        ) : step === 3 || !next ? (
          <>
            <span className="tile-text">{practised} of {total} topics practised. {plan?.supplied_questions ? "Check yourself under exam conditions." : ""}</span>
            <button type="button" className="btn-primary" onClick={onTest}><Timer size={14} /> Go to mocks</button>
          </>
        ) : (
          <>
            <span className="tile-text"><b>{next.topic}</b> · {next.subject} · {next.why}</span>
            <button type="button" className="btn-primary" onClick={() => onStudy(next.topic_id)}><BookOpen size={14} /> Study this topic</button>
          </>
        )}
      </div>
    </div>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function TOSPrepView({ token, onOpenPaper, params, profile }: {
  token: string | null;
  /** Open a saved paper in the shared timed-paper screen (/api/mocks/{id}/*). */
  onOpenPaper?: (mockId: number) => void;
  /** Deep link from Today: #/tos?exam=&paper=&topic= opens a topic, &section=test goes to the mock. */
  params?: NavParams;
  /** The paper chosen in onboarding (the default when no link names one). */
  profile?: { exam: string; paper: string } | null;
}) {
  const [exams, setExams] = useState<ExamFamily[]>([]);
  const [examId, setExamId] = useState("pget");
  const [paperId, setPaperId] = useState("mbbs-p1");
  const [stream, setStream] = useState("all");
  const [paperQuery, setPaperQuery] = useState("");
  const [tree, setTree] = useState<PaperTree | null>(null);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [loadingTree, setLoadingTree] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Mock
  const [plan, setPlan] = useState<MockPlan | null>(null);
  const [mockChoice, setMockChoice] = useState<string>(FULL);
  const [auditOpen, setAuditOpen] = useState(false);
  const [mockStarting, setMockStarting] = useState(false);

  // Progress
  const [progress, setProgress] = useState<Progress | null>(null);
  const [showProgress, setShowProgress] = useState(false);

  // Workspace
  const [active, setActive] = useState<ActiveTopic | null>(null);
  const [tab, setTab] = useState<"refresher" | "notes" | "mcqs" | "results">("refresher");
  const workspaceRef = useRef<HTMLElement | null>(null);
  const testRef = useRef<HTMLElement | null>(null);
  const [refresher, setRefresher] = useState<SheetResponse | null>(null);
  const [notes, setNotes] = useState<SheetResponse | null>(null);
  const [loadingRefresher, setLoadingRefresher] = useState(false);
  const [loadingNotes, setLoadingNotes] = useState(false);

  // Practice
  const [questions, setQuestions] = useState<Question[]>([]);
  const [available, setAvailable] = useState<number | null>(null);
  const [adaptiveNote, setAdaptiveNote] = useState<string | null>(null);
  const [loadingMcqs, setLoadingMcqs] = useState(false);
  const [difficulty, setDifficulty] = useState<string>("mixed");
  const [count, setCount] = useState(10);
  const [mode, setMode] = useState<"study" | "exam">("study");
  const [qIndex, setQIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [revealed, setRevealed] = useState<Record<number, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<SessionResults | null>(null);
  const [generating, setGenerating] = useState(false);

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
  }, [token]);

  /** fetch + JSON, throwing the server's own message on failure. */
  const api = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const res = await fetch(`${API}${path}`, { ...init, headers: headers(), credentials: "include" });
    const body = await res.json().catch(() => null);
    if (!res.ok) throw new Error(errorText(res.status, body?.detail));
    return body as T;
  }, [headers]);

  const qs = (o: Record<string, string | number | null | undefined>) =>
    new URLSearchParams(Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => [k, String(v)])).toString();


  // Paper: tree, mock plan, progress
  const paperReq = useRef(0);
  const loadPaper = useCallback((ex: string, pp: string) => {
    const req = ++paperReq.current;                 // a slower answer for an earlier paper is ignored
    const current = () => req === paperReq.current;
    setLoadingTree(true);
    setTreeError(null);
    api<PaperTree>(`/api/tos/paper?${qs({ exam_id: ex, paper_id: pp })}`)
      .then((t) => {
        if (!current()) return;
        setTree(t);
        setExpanded((cur) => (Object.keys(cur).length ? cur : Object.fromEntries(t.subjects.slice(0, 2).map((x) => [x.id, true]))));
      })
      .catch((e) => { if (current()) setTreeError(e.message); })
      .finally(() => { if (current()) setLoadingTree(false); });
    setPlan(null);
    setProgress(null);
    api<MockPlan>(`/api/tos/mock/audit?${qs({ exam_id: ex, paper_id: pp })}`).then((x) => { if (current()) setPlan(x); }).catch(() => undefined);
    api<Progress>(`/api/tos/progress?${qs({ exam_id: ex, paper_id: pp })}`).then((x) => { if (current()) setProgress(x); }).catch(() => undefined);
  }, [api]);

  /** Switch paper: reset what belonged to the old one and load the new one. */
  const selectPaper = useCallback((ex: string, pp: string) => {
    setExamId(ex);
    setPaperId(pp);
    setExpanded({});
    setActive(null);
    setMockChoice(FULL);
    loadPaper(ex, pp);
  }, [loadPaper]);

  // What a deep link asks for once its paper has loaded (a topic to open, or the test section).
  const pending = useRef<{ topic?: string; section?: string } | null>(
    params?.topic || params?.section ? { topic: params.topic ? String(params.topic) : undefined, section: params.section ? String(params.section) : undefined } : null);
  const start = useRef({ params, profile });

  // Exams, then the first paper: the linked one, else the learner's own paper, else the first of their exam.
  useEffect(() => {
    api<ExamFamily[]>("/api/tos/examinations")
      .then((data) => {
        setExams(data);
        const { params: p, profile: prof } = start.current;
        const has = (ex?: string, pp?: string) => !!ex && !!pp && !!data.find((e) => e.id === ex)?.papers.some((x) => x.id === pp);
        let saved: string[] = [];
        try { saved = (localStorage.getItem("mednama_paper") || "").split("|"); } catch { /* storage unavailable */ }
        for (const [ex, pp] of [[p?.exam, p?.paper], [prof?.exam, prof?.paper], saved] as (string | undefined)[][]) {
          if (has(ex, pp)) return selectPaper(ex!, pp!);
        }
        let level = "";
        try { level = localStorage.getItem("mednama_level") || ""; } catch { /* storage unavailable */ }
        const want = level.startsWith("FCPS") ? "fcps1" : level === "MBBS" ? "mbbs" : "pget";
        const first = data.find((e) => e.id === want) || data[0];
        if (first?.papers[0]) selectPaper(first.id, first.papers[0].id);
      })
      .catch((e) => setTreeError(e.message));
  }, [api, selectPaper]);

  // ── Workspace loaders ──
  const loadPractice = useCallback((a: ActiveTopic, diff: string, n: number) => {
    setLoadingMcqs(true);
    setQIndex(0);
    setAnswers({});
    setRevealed({});
    setResults(null);
    api<{ questions: Question[]; available: number; adaptive_note: string | null }>(
      `/api/tos/topic/mcqs?${qs({ exam_id: examId, paper_id: paperId, topic_id: a.topic.id, subtopic: a.subtopic, difficulty: diff, limit: n })}`)
      .then((d) => { setQuestions(d.questions); setAvailable(d.available); setAdaptiveNote(d.adaptive_note); })
      .catch((e) => { setQuestions([]); setAvailable(null); toast.error(e.message); })
      .finally(() => setLoadingMcqs(false));
  }, [api, examId, paperId]);

  // Refresher and notes are written by the AI the first time (about a minute on the NAS) and saved after, so
  // each is fetched only when its tab is first opened, never both at once.
  const sheetKey = useRef<{ refresher?: string; notes?: string }>({});
  const loadSheet = useCallback((kind: "refresher" | "notes", a: ActiveTopic) => {
    const key = `${examId}|${paperId}|${a.topic.id}|${a.subtopic || ""}`;
    if (sheetKey.current[kind] === key) return;
    sheetKey.current[kind] = key;
    const setData = kind === "refresher" ? setRefresher : setNotes;
    const setLoading = kind === "refresher" ? setLoadingRefresher : setLoadingNotes;
    setLoading(true);
    setData(null);
    api<SheetResponse>(`/api/tos/topic/${kind}?${qs({ exam_id: examId, paper_id: paperId, topic_id: a.topic.id, subtopic: a.subtopic })}`)
      .then((d) => { if (sheetKey.current[kind] === key) setData(d); })
      .catch((e) => {
        if (sheetKey.current[kind] !== key) return;
        sheetKey.current[kind] = undefined;        // allow a retry
        setData({ available: false, label: "", markdown: "", citations: [], message: e.message });
      })
      .finally(() => { if (sheetKey.current[kind] === key || sheetKey.current[kind] === undefined) setLoading(false); });
  }, [api, examId, paperId]);

  const showTab = (t: typeof tab, a: ActiveTopic | null = active) => {
    setTab(t);
    if (a && (t === "refresher" || t === "notes")) loadSheet(t, a);
  };

  const openTopic = (subject: TOSSubject, topic: TOSTopic, subtopic: string | null = null, startTab: typeof tab = "refresher") => {
    const a = { subject, topic, subtopic };
    setActive(a);
    sheetKey.current = {};
    setRefresher(null);
    setNotes(null);
    showTab(startTab, a);
    loadPractice(a, difficulty, count);
    scrollToWorkspace.current = true;            // after the workspace renders (see the effect below)
  };

  const openTopicById = (topicId: string, startTab: typeof tab = "refresher") => {
    for (const s of tree?.subjects || []) {
      const t = s.topics.find((x) => x.id === topicId);
      if (t) return openTopic(s, t, null, startTab);
    }
  };

  // Scroll to a newly opened topic once its workspace is on the page (a click or a deep link).
  const scrollToWorkspace = useRef(false);
  useEffect(() => {
    if (!active || !scrollToWorkspace.current) return;
    scrollToWorkspace.current = false;
    requestAnimationFrame(() => workspaceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }, [active]);

  // Act on the deep link once the tree is here.
  useEffect(() => {
    const want = pending.current;
    if (!tree || !want) return;
    pending.current = null;
    if (want.topic) openTopicById(want.topic);
    else if (want.section === "test") requestAnimationFrame(() => testRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    // openTopicById is recreated each render; it is only needed once, for this tree
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree]);

  const generateMore = async () => {
    if (!active) return;
    setGenerating(true);
    try {
      const d = await api<{ generated_count: number; duplicates_skipped: number; questions: Question[] }>("/api/tos/topic/generate-mcqs", {
        method: "POST",
        body: JSON.stringify({ exam_id: examId, paper_id: paperId, topic_id: active.topic.id, subtopic: active.subtopic,
          count: 5, difficulty: /^\d$/.test(difficulty) ? Number(difficulty) : null }),
      });
      setQuestions((prev) => [...prev, ...d.questions]);
      toast.success(`${d.generated_count} new question${d.generated_count === 1 ? "" : "s"} added${d.duplicates_skipped ? ` (${d.duplicates_skipped} duplicates rejected)` : ""}.`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not write questions");
    } finally {
      setGenerating(false);
    }
  };

  const submitSession = async () => {
    if (!active || !questions.length) return;
    setSubmitting(true);
    try {
      const r = await api<SessionResults>("/api/tos/session/submit", {
        method: "POST",
        body: JSON.stringify({ exam_id: examId, paper_id: paperId, topic_id: active.topic.id, subtopic: active.subtopic, mode,
          answers: questions.map((q) => ({ mcq_id: q.id, selected_option: answers[q.id] || "" })) }),
      });
      setResults(r);
      setTab("results");
      loadPaper(examId, paperId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not mark the session");
    } finally {
      setSubmitting(false);
    }
  };

  const startMock = async (subjectId?: string | null) => {
    setMockStarting(true);
    try {
      const d = await api<{ mock_id: number; questions: number; minutes: number }>(
        `/api/tos/mock/build?${qs({ exam_id: examId, paper_id: paperId, mini_subject: subjectId })}`, { method: "POST" });
      setAuditOpen(false);
      if (onOpenPaper) onOpenPaper(d.mock_id);
      else toast.success(`Paper ready: ${d.questions} questions, ${d.minutes} min. Open it from Exams.`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not build the mock");
    } finally {
      setMockStarting(false);
    }
  };

  // ── Derived ──
  const exam = exams.find((e) => e.id === examId);
  const streams = streamsFor(examId);
  const papers = useMemo(() => (exam?.papers || []).filter((p) => inStream(p, examId, stream) && (!paperQuery.trim() ||
    `${p.title} ${p.specialty || ""}`.toLowerCase().includes(paperQuery.trim().toLowerCase()))), [exam, examId, stream, paperQuery]);

  const chooseStream = (s: string) => {
    setStream(s);
    const match = (exam?.papers || []).filter((p) => inStream(p, examId, s));
    if (match.length && !match.some((p) => p.id === paperId)) selectPaper(examId, match[0].id);
  };

  const quotas = plan?.quotas || [];
  const chosen = quotas.find((q) => q.subject_id === mockChoice) || null;
  const blueprintTotal = quotas.reduce((n, q) => n + q.quota, 0) || 1;

  // Topic finder: every word anywhere in "subject topic subtopics", any order.
  const searchTokens = useMemo(() => searchQuery.toLowerCase().split(/\s+/).filter(Boolean), [searchQuery]);
  const searching = searchTokens.length > 0;
  const visibleSubjects = useMemo(() => {
    const subjects = tree?.subjects || [];
    if (!searchTokens.length) return subjects.map((s) => ({ subject: s, topics: s.topics }));
    return subjects.map((s) => {
      const name = s.subject_name.toLowerCase();
      if (searchTokens.every((w) => name.includes(w))) return { subject: s, topics: s.topics };
      return { subject: s, topics: s.topics.filter((t) => searchTokens.every((w) => `${name} ${t.topic_name} ${t.subtopics.join(" ")}`.toLowerCase().includes(w))) };
    }).filter((x) => x.topics.length > 0);
  }, [tree, searchTokens]);
  const matchedTopicCount = visibleSubjects.reduce((n, x) => n + x.topics.length, 0);
  const allExpanded = !!tree?.subjects.length && tree.subjects.every((s) => expanded[s.id]);
  const isHit = (t: string) => searchTokens.some((w) => t.toLowerCase().includes(w));
  const highlight = (t: string): React.ReactNode => {
    if (!searchTokens.length) return t;
    const re = new RegExp(`(${searchTokens.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
    return t.split(re).map((part, i) => (i % 2 ? <mark key={i} className="tos-mark">{part}</mark> : part));
  };

  const steps: { id: typeof tab; label: string; icon: React.ReactNode }[] = [
    { id: "refresher", label: "Refresher", icon: <Lightbulb size={13} /> },
    { id: "notes", label: "Notes", icon: <FileText size={13} /> },
    { id: "mcqs", label: `Practice (${questions.length})`, icon: <ListOrdered size={13} /> },
    ...(results ? [{ id: "results" as const, label: "Results", icon: <Trophy size={13} /> }] : []),
  ];
  const signedIn = !!((typeof window !== "undefined" && localStorage.getItem("token")) || token);

  return (
    <PageShell title="TOS prep" width="wide" icon={<Compass size={24} />}
      subtitle="Work through an official Table of Specifications topic by topic (refresher, notes, practice), then sit a mock built to the same blueprint.">

      {/* ── Blueprint picker: exam → stream → paper ── */}
      <section className="tos-card" aria-label="Choose a blueprint">
        <div className="practice-picker-row">
          <span className="practice-picker-label">Exam</span>
          <div className="segmented" role="radiogroup" aria-label="Examination">
            {exams.map((e) => (
              <button key={e.id} type="button" role="radio" aria-checked={examId === e.id} title={e.title}
                className={`segmented-item ${examId === e.id ? "active" : ""}`}
                onClick={() => { setStream("all"); setPaperQuery(""); if (e.papers[0]) selectPaper(e.id, e.papers[0].id); }}>
                {e.short_name}
              </button>
            ))}
          </div>
        </div>

        <div className="tos-row-between">
          <div className="practice-picker-row" role="radiogroup" aria-label="Stream">
            <span className="practice-picker-label">{examId === "mbbs" ? "Year" : examId === "pget" ? "Stream" : "Paper"}</span>
            {streams.map((s) => (
              <button key={s.id} type="button" role="radio" aria-checked={stream === s.id}
                className={`practice-chip ${stream === s.id ? "active" : ""}`} onClick={() => chooseStream(s.id)}>
                {s.label} <span className="practice-chip-count">{(exam?.papers || []).filter((p) => inStream(p, examId, s.id)).length}</span>
              </button>
            ))}
          </div>
          {(exam?.papers.length ?? 0) > 6 ? (
            <label className="tos-search" title="Narrow the list of papers below by name">
              <Search size={13} />
              <input type="search" aria-label="Find a paper" placeholder="Find a paper…" value={paperQuery}
                onChange={(e) => setPaperQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setPaperQuery(""); }} />
            </label>
          ) : null}
        </div>

        {papers.length ? (
          <div className="tos-papers" role="radiogroup" aria-label="Paper">
            {papers.map((p) => {
              const info = paperBadge(p, examId);
              return (
                <button key={p.id} type="button" role="radio" aria-checked={paperId === p.id}
                  className={`tos-paper ${paperId === p.id ? "active" : ""} ${p.available ? "" : "unavailable"}`}
                  onClick={() => selectPaper(examId, p.id)} title={p.available ? undefined : "This blueprint has not been published or parsed yet"}>
                  <span className="tile-kicker">{info.badge}</span>
                  <span className="tos-paper-title">{info.title}</span>
                  <span className="practice-subject-count">
                    {p.available ? `${p.total_mcqs} MCQs · ${p.subjects_count} subjects${p.weighted ? "" : " · no official weighting"}` : "Blueprint unavailable"}
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="page-state">No papers match this filter.</div>
        )}
        {exam ? <p className="tos-provenance">TOS: {exam.source || exam.title} · {exam.version} · {exam.authority}</p> : null}
      </section>

      {treeError ? <div className="page-state page-error">{treeError}</div> : null}

      {/* ── This paper: numbers, mocks, progress ── */}
      {tree && !loadingTree ? (
        !tree.available ? (
          <div className="empty-state-card">
            <ClipboardList size={28} className="empty-icon" />
            <div className="empty-title">This blueprint is unavailable</div>
            <div className="empty-desc">Its TOS has not been published or parsed yet, so there is nothing to show rather than an invented syllabus.</div>
          </div>
        ) : (
        <section className="tos-card" aria-label="This paper">
          <div className="tos-row-between">
            <div>
              <span className="tile-kicker">{exam?.short_name} · {tree.weighted ? "official counts" : "no official counts in this TOS"}</span>
              <h2 className="tos-h2">{tree.title}</h2>
            </div>
            <div className="tos-stats">
              <div className="tos-stat" title="Questions on the real paper (official)"><span className="lbl">Exam paper</span><b>{tree.total_mcqs}</b></div>
              <div className="tos-stat" title="Distinct questions in medNAMA mapped to this paper's topics"><span className="lbl">In our bank</span><b>{tree.bank_mcq_count?.toLocaleString() ?? "–"}</b></div>
              <div className="tos-stat" title="Your recorded answers on this paper's topics"><span className="lbl">You answered</span><b>{tree.attempted_count}</b></div>
              <div className="tos-stat" title="Observed accuracy over your recorded answers"><span className="lbl">Your accuracy</span><b data-tone={tone(tree.accuracy)}>{tree.accuracy !== null ? `${tree.accuracy}%` : "–"}</b></div>
            </div>
          </div>

          <TosPath progress={progress} tree={tree} plan={plan} signedIn={signedIn}
            onStudy={(id) => openTopicById(id)} onTest={() => testRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })} />

          {/* Progress (separate from the blueprint: what you have done, not what the exam asks) */}
          {progress ? (
            <div className="tos-progress">
              <button type="button" className="tos-progress-head" aria-expanded={showProgress} onClick={() => setShowProgress((v) => !v)}>
                <LineChart size={15} />
                <b>Your progress</b>
                <span>{progress.topics_practised} of {progress.topics_total} topics practised · {progress.answers} answers{progress.accuracy !== null ? ` · ${progress.accuracy}% observed` : ""}</span>
                {showProgress ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
              </button>
              {showProgress ? (
                <div className="tos-progress-body">
                  <div className="tos-progress-col">
                    <span className="practice-picker-label">Weekly accuracy</span>
                    {progress.trend.length ? (
                      <div className="tos-trend">
                        {progress.trend.map((w) => (
                          <div key={w.week} title={`Week of ${w.week}: ${w.accuracy}% over ${w.answers} answers`}>
                            <i data-tone={tone(w.accuracy)} style={{ height: `${Math.max(w.accuracy, 4)}%` }} />
                            <span>{w.week.slice(5)}</span>
                          </div>
                        ))}
                      </div>
                    ) : <p className="tile-text">No answers on this paper in the last 8 weeks.</p>}
                    {progress.difficulty.length ? (
                      <div className="tos-bars">
                        {progress.difficulty.map((d) => (
                          <div key={d.difficulty} className="tos-bar-row">
                            <span className="tos-bar-label">Level {d.difficulty}</span>
                            <span className="tos-meter"><i data-tone={tone(d.accuracy)} style={{ width: `${d.accuracy}%` }} /></span>
                            <span className="tos-bar-value">{d.accuracy}% · {d.answers}</span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    <p className="tile-text">Accuracy is observed over your answers; it is only called mastery after {progress.mastery_threshold} answers on a topic. Revision sheets opened: {progress.notes_opened}.</p>
                  </div>
                  <div className="tos-progress-col">
                    <span className="practice-picker-label">Next</span>
                    {progress.suggestions.length ? progress.suggestions.map((s) => (
                      <button key={s.topic_id} type="button" className="tos-suggest" onClick={() => openTopicById(s.topic_id)}>
                        <b>{s.topic}</b><span>{s.subject} · {s.why}</span>
                      </button>
                    )) : <p className="tile-text">Practise a topic to get suggestions.</p>}
                    {progress.repeatedly_missed.length ? (
                      <>
                        <span className="practice-picker-label" style={{ marginTop: 8 }}>Missed more than once</span>
                        {progress.repeatedly_missed.slice(0, 5).map((m) => (
                          <div key={m.mcq_id} className="tos-suggest static"><b>{m.topic} · {m.misses}×</b><span>{m.stem}</span></div>
                        ))}
                      </>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>
        )
      ) : null}

      {/* ── Topic workspace ── */}
      {active ? (
        <section className="tos-card tos-workspace" ref={workspaceRef} aria-label={active.topic.topic_name}>
          <div className="tos-row-between">
            <div>
              <span className="tile-kicker">{exam?.short_name} · {active.subject.subject_name}{active.subtopic ? ` · ${active.topic.topic_name}` : ""}</span>
              <h2 className="tos-h2"><Target size={17} /> {active.subtopic || active.topic.topic_name}</h2>
            </div>
            <button type="button" className="btn-workspace" onClick={() => setActive(null)}><X size={13} /> Close topic</button>
          </div>

          {active.topic.subtopics.length ? (
            <div className="practice-picker-row" role="radiogroup" aria-label="Subtopic">
              <span className="practice-picker-label">Focus</span>
              <button type="button" className={`practice-chip ${!active.subtopic ? "active" : ""}`}
                onClick={() => openTopic(active.subject, active.topic, null, tab === "results" ? "mcqs" : tab)}>Whole topic</button>
              {active.topic.subtopic_details.map((d) => (
                <button key={d.name} type="button" className={`practice-chip ${active.subtopic === d.name ? "active" : ""}`}
                  onClick={() => openTopic(active.subject, active.topic, d.name, tab === "results" ? "mcqs" : tab)}>
                  {d.has_notes ? <FileText size={11} /> : null}{d.name}
                  {d.bank_mcq_count !== null ? <span className="practice-chip-count">{d.bank_mcq_count}</span> : null}
                </button>
              ))}
            </div>
          ) : null}

          <div className="segmented tos-steps" role="tablist">
            {steps.map((s, i) => (
              <button key={s.id} type="button" role="tab" aria-selected={tab === s.id}
                className={`segmented-item ${tab === s.id ? "active" : ""}`} onClick={() => showTab(s.id)}>
                <span className="tos-step-n">{i + 1}</span>{s.icon} {s.label}
              </button>
            ))}
          </div>

          {tab === "refresher" && (
            loadingRefresher ? (
              <div className="page-state"><Loader2 size={16} className="animate-spin" /> Writing a refresher from the textbooks. The first time takes about a minute; after that it opens at once.</div>
            ) : refresher?.available ? (
              <>
                <div className="prose tos-prose" dangerouslySetInnerHTML={{ __html: parseMarkdown(refresher.markdown) }} />
                <Citations items={refresher.citations} />
                <div className="tos-step-next"><button type="button" className="btn-workspace" onClick={() => showTab("notes")}>Next: revision notes →</button></div>
              </>
            ) : (
              <div className="tos-note warn">{refresher?.message || "No refresher for this topic."}
                {active ? <button type="button" className="practice-link" style={{ marginLeft: 8 }} onClick={() => { sheetKey.current.refresher = undefined; loadSheet("refresher", active); }}>Try again</button> : null}</div>
            )
          )}

          {tab === "notes" && (
            loadingNotes ? (
              <div className="page-state"><Loader2 size={16} className="animate-spin" /> Fetching the revision sheet. If none exists yet it is written now, which takes about a minute.</div>
            ) : notes?.available ? (
              <>
                <div className="tos-row-between">
                  <span className="practice-picker-label"><Sparkles size={12} style={{ verticalAlign: -2 }} /> 1–3 minute revision sheet</span>
                  <span className="tos-tag">{notes.origin === "seed" ? "Validated sheet" : notes.origin === "cache" ? "Saved sheet" : "Written now from the textbooks"}</span>
                </div>
                {notes.sheets?.length ? notes.sheets.map((s) => (
                  <div key={s.subtopic || "topic"} className="tos-sheet">
                    {!active.subtopic && s.subtopic && !s.markdown.slice(0, 160).toLowerCase().includes(s.subtopic.toLowerCase()) ? <h3 className="tos-sheet-title">{s.subtopic}</h3> : null}
                    <div className="prose tos-prose" dangerouslySetInnerHTML={{ __html: parseMarkdown(s.markdown) }} />
                  </div>
                )) : <div className="prose tos-prose" dangerouslySetInnerHTML={{ __html: parseMarkdown(notes.markdown) }} />}
                {!notes.sheets?.length ? <Citations items={notes.citations} /> : null}
                <div className="tos-step-next"><button type="button" className="btn-workspace" onClick={() => setTab("mcqs")}>Next: practise →</button></div>
              </>
            ) : (
              <div className="tos-note warn">{notes?.message || "No revision sheet for this topic."}
                {active ? <button type="button" className="practice-link" style={{ marginLeft: 8 }} onClick={() => { sheetKey.current.notes = undefined; loadSheet("notes", active); }}>Try again</button> : null}</div>
            )
          )}

          {tab === "mcqs" && (
            <>
              <div className="tos-row-between">
                <div className="practice-picker-row">
                  <span className="practice-picker-label">Difficulty</span>
                  <div className="segmented">
                    {["mixed", "adaptive", "1", "2", "3", "4", "5"].map((d) => (
                      <button key={d} type="button" className={`segmented-item ${difficulty === d ? "active" : ""}`}
                        title={d === "adaptive" ? "Follows your accuracy on this topic" : undefined}
                        onClick={() => { setDifficulty(d); loadPractice(active, d, count); }}>
                        {d === "mixed" ? "Mixed" : d === "adaptive" ? "Adaptive" : d}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="practice-picker-row">
                  <span className="practice-picker-label">Questions</span>
                  <div className="segmented">
                    {COUNTS.map((n) => (
                      <button key={n} type="button" className={`segmented-item ${count === n ? "active" : ""}`}
                        onClick={() => { setCount(n); loadPractice(active, difficulty, n); }}>{n}</button>
                    ))}
                  </div>
                  <span className="practice-picker-label">Answers</span>
                  <div className="segmented">
                    <button type="button" className={`segmented-item ${mode === "study" ? "active" : ""}`} title="See the answer and explanation after each question: best for learning" onClick={() => setMode("study")}>As I go</button>
                    <button type="button" className={`segmented-item ${mode === "exam" ? "active" : ""}`} title="Answers and explanations only after you finish: like the exam" onClick={() => setMode("exam")}>At the end</button>
                  </div>
                </div>
              </div>

              {!loadingMcqs && available !== null ? (
                <div className={`tos-note ${available < count ? "warn" : ""}`}>
                  {available < count
                    ? <>Only <b>{available}</b> validated question{available === 1 ? "" : "s"} for this {active.subtopic ? "subtopic" : "topic"}{difficulty !== "mixed" && difficulty !== "adaptive" ? ` at level ${difficulty}` : ""}. Questions from other topics are never added to make up the number.</>
                    : <>{available} validated questions for this {active.subtopic ? "subtopic" : "topic"}; showing {Math.min(count, questions.length)}, ones you have not answered first.</>}
                  {adaptiveNote ? <> Difficulty: {adaptiveNote}.</> : null}
                  <button type="button" className="practice-link" disabled={generating} onClick={generateMore} style={{ marginLeft: 8 }}>
                    {generating ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />} Write 5 more from the textbooks
                  </button>
                </div>
              ) : null}

              {loadingMcqs ? (
                <div className="page-state"><Loader2 size={16} className="animate-spin" /> Loading questions…</div>
              ) : questions.length ? (
                <>
                  <QuestionNavigator count={questions.length} current={qIndex} onJump={setQIndex} answered={(i) => !!answers[questions[i].id]} />
                  {(() => {
                    const q = questions[qIndex];
                    if (!q) return null;
                    const shown = mode === "study" && !!revealed[q.id];
                    return (
                      <QuestionPlayer mcq={q} token={token} selected={answers[q.id]} correct={shown ? q.correct_option : null}
                        locked={shown} animate={mode === "study"}
                        onSelect={(key) => { setAnswers((p) => ({ ...p, [q.id]: key })); if (mode === "study") setRevealed((p) => ({ ...p, [q.id]: true })); }}
                        kicker={<>Question {qIndex + 1} of {questions.length}</>}
                        aside={q.source ? <span className={`qp-tag ${q.grounding === "ai" ? "warn" : ""}`}>{q.source}</span> : null}>
                        {shown && q.explanation_markdown ? (
                          <div className="prose tos-prose-sm" dangerouslySetInnerHTML={{ __html: parseMarkdown(q.explanation_markdown) }} />
                        ) : null}
                        <div className="qp-bar">
                          <button type="button" className="btn-workspace" disabled={qIndex === 0} onClick={() => setQIndex((i) => Math.max(0, i - 1))}>Previous</button>
                          {qIndex < questions.length - 1 ? (
                            <button type="button" className="btn-workspace" onClick={() => setQIndex((i) => i + 1)}>Next</button>
                          ) : (
                            <button type="button" className="btn-primary" disabled={submitting || !signedIn} title={signedIn ? undefined : "Sign in to record results"} onClick={submitSession}>
                              {submitting ? <Loader2 size={12} className="animate-spin" /> : null} Finish and see results
                            </button>
                          )}
                        </div>
                      </QuestionPlayer>
                    );
                  })()}
                </>
              ) : (
                <div className="empty-state-card">
                  <ListOrdered size={28} className="empty-icon" />
                  <div className="empty-title">No validated questions here yet</div>
                  <div className="empty-desc">Use “Write 5 more from the textbooks” above, or choose another difficulty.</div>
                </div>
              )}
            </>
          )}

          {tab === "results" && results && (
            <>
              <SessionSummary
                kicker={<><Trophy size={12} style={{ verticalAlign: -1 }} /> {results.mode === "exam" ? "Exam mode" : "Study mode"}</>}
                title={active.subtopic || active.topic.topic_name}
                right={results.correct_count}
                total={results.total_attempted}
                answered={results.answered}
                note={results.topic_accuracy !== null
                  ? (results.topic_answers >= results.mastery_threshold
                    ? `Across all ${results.topic_answers} of your answers on this topic: ${results.topic_accuracy}%.`
                    : `Observed accuracy on this topic: ${results.topic_accuracy}% over ${results.topic_answers} answers (too few to estimate mastery; ${results.mastery_threshold} needed).`)
                  : undefined}
                actions={[
                  { label: "Practise again", primary: true, onClick: () => { loadPractice(active, difficulty, count); setTab("mcqs"); } },
                  { label: "Review notes", onClick: () => showTab("notes") },
                  { label: `${active.subject.subject_name} mini-mock`, onClick: () => startMock(active.subject.id) },
                ]}
              />
              <ResultDetails results={results} onOpenSubtopic={(s) => openTopic(active.subject, active.topic, s, "notes")} />
            </>
          )}
        </section>
      ) : null}

      {/* ── Syllabus ── */}
      {tree?.available !== false ? (
      <section aria-label="Syllabus" className="tos-syllabus">
        <div>
          <span className="tile-kicker"><span className="tos-step-n">1</span> Learn · <span className="tos-step-n">2</span> Practise</span>
          <h2 className="tos-h2"><Layers size={17} /> Topics in this paper</h2>
          <p className="tile-text">Pick a topic you have already read. Each one opens in three steps: a refresher, a 1–3 minute revision sheet, then practice questions on that topic only.</p>
        </div>

        <div className="tos-finder">
          <label className="tos-search tos-search-lg">
            <Search size={15} />
            <input type="search" aria-label="Find a topic" value={searchQuery} placeholder={`Find a topic, e.g. "brachial plexus" or "asthma"`}
              onChange={(e) => setSearchQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setSearchQuery(""); }} />
            {searching ? (
              <button type="button" className="tos-search-clear" aria-label="Clear search" onClick={() => setSearchQuery("")}><X size={14} /></button>
            ) : null}
          </label>
          {searching ? (
            <span className="tos-finder-note" role="status">
              {matchedTopicCount ? <>{matchedTopicCount} topic{matchedTopicCount === 1 ? "" : "s"} in {visibleSubjects.length} subject{visibleSubjects.length === 1 ? "" : "s"}</> : "No matches"}
            </span>
          ) : tree?.subjects.length ? (
            <button type="button" className="practice-link" onClick={() => {
              setExpanded(allExpanded ? {} : Object.fromEntries(tree.subjects.map((s) => [s.id, true])));
            }}>{allExpanded ? "Collapse all" : "Expand all"}</button>
          ) : null}
        </div>

        {loadingTree ? (
          <div className="page-state"><Loader2 size={16} className="animate-spin" /> Loading the blueprint…</div>
        ) : visibleSubjects.length ? (
          <div className="tos-subjects">
            {visibleSubjects.map(({ subject, topics }) => {
              const open = searching || !!expanded[subject.id];
              const quota = subject.official_mcq_count;
              const quotaRow = quotas.find((q) => q.subject_id === subject.id);
              return (
                <div key={subject.id} className={`practice-subject tos-subject ${open ? "open" : ""}`}>
                  <div className="practice-subject-head">
                    <button type="button" className="practice-subject-pick" aria-expanded={open} disabled={searching}
                      onClick={() => setExpanded((p) => ({ ...p, [subject.id]: !open }))}>
                      <span className="tos-subject-line">
                        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                        <span className="practice-subject-name">{highlight(subject.subject_name)}</span>
                      </span>
                      <span className="practice-subject-count">
                        {searching && topics.length < subject.topics.length ? `${topics.length} of ${subject.topics.length} topics` : `${subject.topics.length} topics`}
                        {quota !== null ? ` · ${quota} on the paper` : " · no official count"}
                        {subject.bank_mcq_count !== null ? ` · ${subject.bank_mcq_count.toLocaleString()} in bank` : ""}
                        {subject.topics_with_questions !== null && subject.topics_with_questions < subject.topics.length
                          ? ` · ${subject.topics_with_questions}/${subject.topics.length} topics have questions` : ""}
                      </span>
                    </button>
                    <div className="tos-subject-end">
                      <span className="tos-acc" title={subject.accuracy !== null ? `Observed over ${subject.attempted_count} answers` : "Not practised yet"}>
                        <span className="tos-meter"><i data-tone={tone(subject.accuracy)} style={{ width: `${subject.accuracy ?? 0}%` }} /></span>
                        {subject.accuracy !== null ? `${subject.accuracy}%` : "–"}
                      </span>
                      <button type="button" className="btn-workspace tos-btn-sm" disabled={mockStarting || !signedIn || !quotaRow?.mini_questions}
                        title={quotaRow ? `${quotaRow.mini_questions} questions · ${quotaRow.mini_minutes} min` : undefined}
                        onClick={() => startMock(subject.id)}>
                        <Timer size={12} /> Mini-mock
                      </button>
                    </div>
                  </div>

                  {open && (
                    <div className="tos-topics">
                      {topics.map((topic) => {
                        const hits = searching ? topic.subtopic_details.filter((d) => isHit(d.name)) : [];
                        const subs = searching ? [...hits, ...topic.subtopic_details.filter((d) => !isHit(d.name))] : topic.subtopic_details;
                        const shown = Math.max(5, hits.length);
                        const empty = topic.bank_mcq_count === 0;
                        return (
                          <div key={topic.id} className={`tos-topic ${empty ? "empty" : ""}`}>
                            <div className="tos-topic-main">
                              <button type="button" className="tos-topic-name" onClick={() => openTopic(subject, topic)}>
                                {highlight(topic.topic_name)}
                                {topic.has_notes ? <FileText size={12} aria-label="Revision notes available" className="tos-has-notes" /> : null}
                              </button>
                              {subs.length ? (
                                <span className="tos-subtopics">
                                  {subs.slice(0, shown).map((d) => (
                                    <button key={d.name} type="button" className={`tos-tag tos-subchip ${searching && isHit(d.name) ? "hit" : ""}`}
                                      title={`${d.bank_mcq_count ?? "?"} questions${d.has_notes ? " · revision sheet" : ""}${d.accuracy !== null ? ` · ${d.accuracy}% observed` : ""}`}
                                      onClick={() => openTopic(subject, topic, d.name)}>
                                      {highlight(d.name)}
                                      {d.bank_mcq_count !== null ? <span className="practice-chip-count">{d.bank_mcq_count}</span> : null}
                                    </button>
                                  ))}
                                  {subs.length > shown ? <span className="practice-chip-count">+{subs.length - shown} more</span> : null}
                                </span>
                              ) : null}
                            </div>
                            <span className="practice-subject-count tos-topic-meta">
                              {topic.bank_mcq_count === null ? "not mapped" : empty ? "no questions yet" : `${topic.bank_mcq_count.toLocaleString()} Qs`}
                              {topic.accuracy !== null ? <b data-tone={tone(topic.accuracy)} title={topic.mastery_estimable ? "Enough answers to estimate mastery" : "Observed accuracy (few answers)"}> · {topic.accuracy}%</b> : null}
                            </span>
                            <button type="button" className={`btn-workspace tos-btn-sm ${active?.topic.id === topic.id ? "tos-btn-on" : ""}`}
                              onClick={() => openTopic(subject, topic)}>
                              {active?.topic.id === topic.id ? "Open" : "Study"} →
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : searching ? (
          <div className="empty-state-card">
            <Search size={26} className="empty-icon" />
            <div className="empty-title">No topic matches &ldquo;{searchQuery.trim()}&rdquo;</div>
            <div className="empty-desc">Try one key word (e.g. &ldquo;plexus&rdquo; instead of a full phrase), or pick another paper above.</div>
            <button type="button" className="btn-workspace" style={{ alignSelf: "center", marginTop: 12 }} onClick={() => setSearchQuery("")}>Clear search</button>
          </div>
        ) : tree ? (
          <div className="page-state">This paper lists no topics in its blueprint.</div>
        ) : null}
      </section>
      ) : null}

      {/* ── Step 3: test yourself (mocks built to the blueprint) ── */}
      {tree && !loadingTree && tree.available ? (
        <section className="tos-card" aria-label="Test yourself" ref={testRef}>
          <div>
            <span className="tile-kicker"><span className="tos-step-n">3</span> Test yourself</span>
            <h2 className="tos-h2"><Timer size={17} /> Mocks built to this blueprint</h2>
            <p className="tile-text">Timed and marked at the end, like the exam. Start with one subject (a mini-mock) once you have practised its topics; sit the full paper when most subjects are done.</p>
          </div>
          {!tree.tagged ? (
            <div className="tos-note warn">This paper&apos;s questions are not mapped to its topics yet, so bank counts and mocks are unavailable. An admin can run <code>scripts/tag_tos.py</code>.</div>
          ) : quotas.length ? (
            <>
              <div className="tos-blueprint">
                <div className="tos-row-between">
                  <span className="practice-picker-label">Blueprint · tap a subject for its mini-mock</span>
                  <span className="tos-legend">
                    <span><i data-tone="ready" /> Bank covers it</span>
                    <span><i data-tone="short" /> Short</span>
                    {!tree.weighted || quotas.some((q) => q.quota_source === "internal") ? <span><i className="hatch" /> Our allocation, not official</span> : null}
                  </span>
                </div>
                <div className="tos-blueprint-bar" role="radiogroup" aria-label="Subjects by share of the paper">
                  {quotas.map((q) => {
                    const share = q.quota / blueprintTotal;
                    const cover = Math.min(1, q.available_in_bank / Math.max(q.quota, 1));
                    return (
                      <button key={q.subject_id} type="button" role="radio" aria-checked={mockChoice === q.subject_id}
                        className={`tos-seg ${mockChoice === q.subject_id ? "active" : ""} ${mockChoice === FULL ? "in-full" : ""} ${q.quota_source === "internal" ? "internal" : ""}`}
                        data-tone={cover >= 1 ? "ready" : "short"} style={{ flexGrow: q.quota }}
                        title={`${q.subject_name}: ${q.quota} of ${blueprintTotal} (${Math.round(share * 100)}%, ${q.quota_source}) · ${q.available_in_bank.toLocaleString()} in bank`}
                        onClick={() => setMockChoice(mockChoice === q.subject_id ? FULL : q.subject_id)}>
                        <i style={{ height: `${Math.max(cover * 100, 6)}%` }} />
                        {share >= 0.07 ? <span>{q.quota}</span> : null}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="practice-picker-row" role="radiogroup" aria-label="Mock">
                <span className="practice-picker-label">Mock</span>
                <button type="button" role="radio" aria-checked={mockChoice === FULL} className={`practice-chip ${mockChoice === FULL ? "active" : ""}`}
                  disabled={!plan?.supplied_questions} onClick={() => setMockChoice(FULL)}>
                  <Timer size={12} /> Full paper <span className="practice-chip-count">{plan?.supplied_questions}</span>
                </button>
                {quotas.map((q) => (
                  <button key={q.subject_id} type="button" role="radio" aria-checked={mockChoice === q.subject_id}
                    className={`practice-chip ${mockChoice === q.subject_id ? "active" : ""}`} disabled={q.mini_questions === 0}
                    onClick={() => setMockChoice(q.subject_id)}>
                    <span className="tos-dot" data-tone={q.sufficient ? "ready" : "short"} />
                    {q.subject_name} <span className="practice-chip-count">{q.mini_questions}</span>
                  </button>
                ))}
              </div>

              <div className="tos-launch">
                <div className="tos-launch-text">
                  <b>{chosen ? `${chosen.subject_name} mini-mock` : `Full ${tree.total_mcqs}-question paper`}</b>
                  <span>
                    {chosen
                      ? `${chosen.mini_questions} questions · ${chosen.mini_minutes} min${chosen.official_quota ? ` · ${chosen.official_quota} on the real paper` : " · our size, the TOS prints no count"}`
                      : `${plan?.supplied_questions} questions · ${plan?.minutes} min · ${plan?.compliance_note}`}
                  </span>
                </div>
                <div className="page-actions">
                  <button type="button" className="btn-workspace" onClick={() => setAuditOpen(true)}><ClipboardList size={13} /> Coverage audit</button>
                  <button type="button" className="btn-primary" disabled={mockStarting || !signedIn || (chosen ? chosen.mini_questions === 0 : !plan?.supplied_questions)}
                    title={signedIn ? undefined : "Sign in to sit a mock"} onClick={() => startMock(chosen?.subject_id)}>
                    {mockStarting ? <Loader2 size={14} className="animate-spin" /> : <Timer size={14} />} Start mock
                  </button>
                </div>
              </div>
            </>
          ) : null}

        </section>
      ) : null}

      {/* ── Coverage audit ── */}
      {auditOpen && plan ? (
        <div className="modal-backdrop" onClick={() => setAuditOpen(false)}>
          <div className="modal-panel tos-audit" role="dialog" aria-label="Blueprint coverage audit" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <span className="tile-kicker">Coverage audit</span>
                <h3 className="tos-h2">{plan.title}</h3>
              </div>
              <button type="button" className="modal-close-btn" aria-label="Close" onClick={() => setAuditOpen(false)}><X /></button>
            </div>
            <div className="tos-audit-body">
              <div className="tos-stats">
                <div className="tos-stat"><span className="lbl">Exam paper</span><b>{plan.official_total_mcqs}</b></div>
                <div className="tos-stat"><span className="lbl">Mock would have</span><b>{plan.supplied_questions}</b></div>
                <div className="tos-stat"><span className="lbl">In our bank</span><b>{plan.bank_total_available.toLocaleString()}</b></div>
                <div className="tos-stat"><span className="lbl">Status</span><b data-tone={plan.is_compliant ? "good" : "bad"}>{plan.is_compliant ? "Official distribution met" : "Not fully TOS-compliant"}</b></div>
              </div>
              <div className={`tos-note ${plan.is_compliant ? "" : "warn"}`}>{plan.compliance_note}</div>
              <div className="tos-bars">
                {plan.quotas.map((q) => (
                  <div key={q.subject_id} className="tos-bar-row">
                    <span className="tos-bar-label" title={q.subject_name}>{q.subject_name}</span>
                    <span className="tos-meter"><i data-tone={q.sufficient ? "good" : "bad"} style={{ width: `${Math.min(100, (q.available_in_bank / Math.max(q.quota, 1)) * 100)}%` }} /></span>
                    <span className="tos-bar-value">
                      {q.supplied}/{q.quota} <span className={`tos-src ${q.quota_source}`}>{q.quota_source}</span>
                      {q.deviation ? <span style={{ color: "var(--error)" }}> · {q.deviation} short</span> : null}
                    </span>
                  </div>
                ))}
              </div>
              <p className="tile-text">Official counts come from the published TOS. &ldquo;Internal&rdquo; is our own allocation, used only where the TOS prints no count. Bank counts are questions mapped to each subject&apos;s topics.</p>
            </div>
            <div className="tos-audit-foot">
              <button type="button" className="btn-workspace" onClick={() => setAuditOpen(false)}>Close</button>
              <button type="button" className="btn-primary" disabled={mockStarting || !plan.supplied_questions || !signedIn} onClick={() => startMock(null)}>
                <Timer size={14} /> Start full mock
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </PageShell>
  );
}
