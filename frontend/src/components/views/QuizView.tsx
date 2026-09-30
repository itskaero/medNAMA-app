"use client";

import React from "react";
import {
  GraduationCap,
  Clock,
  Loader2,
  Check,
  X,
  Bookmark,
  Sparkles,
  Zap,
  HeartPulse,
  Wind,
  Brain,
  MessageSquare,
  Send,
  Stethoscope,
  Plus,
  Play,
  FileText,
  Trash2,
  ChevronUp,
  ChevronDown,
  Search,
  BookOpen,
  HelpCircle,
  ArrowRight,
  ArrowLeft,
  AlertTriangle,
  RotateCcw,
  Layers,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { AnswerResponse, Figure, Book } from "@/types";
import { API } from "@/lib/constants";
import { proxySafeFetch } from "@/lib/proxyFetch";
import { toast } from "sonner";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import ExplanationPanel from "@/components/ExplanationPanel";
import BasicDropdown from "@/components/ui/basic-dropdown";
import { PaperYears, QuestionMedia } from "@/components/QuestionMedia";
import { ArchiveBadges, PastPaperExtras } from "@/components/PastPaperExtras";
import HardenPanel, { HARDEN_MAX_QUESTIONS, HARDEN_STORAGE_KEY, HardenLevel, hardenBlockReason, previewLine, useHardenPreview } from "@/components/HardenPanel";
import PracticePicker, { EMPTY_SCOPE, PracticeScope, describeScope, scopeActive } from "@/components/PracticePicker";

// Quiz generation runs as a background job: the start request returns at once
// and the page polls its status, so no proxy/browser timeout can cut it off.
const QUIZ_POLL_INTERVAL_MS = 2_500;
// FCPS-I style paper: ~1.2 min per single-best-answer question.
// Mock presets. CPSP FCPS-I: two papers of 100 single-best-answer MCQs, 2 hours each,
// 75% pass mark, no negative marking; the full preset reproduces one paper.
const MOCK_PRESETS = [
  { key: "short", title: "FCPS mock (short)", questions: 50, minutes: 60 },
  { key: "full", title: "Full FCPS-I paper", questions: 100, minutes: 120 },
] as const;
const QUIZ_MAX_WAIT_MS = 6 * 60_000;

interface QuizViewProps {
  // quiz flow
  quizStep: "config" | "taker" | "summary";
  setQuizStep: (s: "config" | "taker" | "summary") => void;
  quizMCQs: any[];
  quizCurrentIdx: number;
  setQuizCurrentIdx: (idx: number | ((prev: number) => number)) => void;
  quizSelectedAnswers: { [key: number]: string };
  quizConfidence?: { [key: number]: "sure" | "unsure" | "guess" };
  setQuizConfidence?: React.Dispatch<React.SetStateAction<{ [key: number]: "sure" | "unsure" | "guess" }>>;
  quizAttemptId: number | null;
  quizIsLoading: boolean;
  quizIsSubmitting: boolean;
  // config
  quizConfigCategories: string[];
  setQuizConfigCategories: React.Dispatch<React.SetStateAction<string[]>>;
  quizConfigSubCategories: string[];
  setQuizConfigSubCategories: React.Dispatch<React.SetStateAction<string[]>>;
  quizConfigNumQuestions: number;
  setQuizConfigNumQuestions: (n: number) => void;
  quizConfigTimerMode: "none" | "session" | "per_question";
  setQuizConfigTimerMode: (m: "none" | "session" | "per_question") => void;
  quizConfigTimerValue: number;
  setQuizConfigTimerValue: (n: number) => void;
  quizConfigExcludeMastered: boolean;
  setQuizConfigExcludeMastered: (v: boolean) => void;
  quizConfigFeedbackMode: "tutor" | "board";
  setQuizConfigFeedbackMode: (m: "tutor" | "board") => void;
  quizConfigStep: 1 | 2 | 3;
  setQuizConfigStep: (s: 1 | 2 | 3) => void;
  quizTimerCountdown: number;
  setQuizTimerCountdown: (n: number) => void;
  // timer / summary
  summaryReviewIdx: number | null;
  setSummaryReviewIdx: (idx: number | null) => void;
  quizSecondsElapsed: number;
  quizTimerActive: boolean;
  // ux animation
  lastSelectedChoice: string | null;
  setLastSelectedChoice: (v: string | null) => void;
  isCorrectSelection: boolean | null;
  setIsCorrectSelection: (v: boolean | null) => void;
  // explanation
  explanationMCQId: number | null;
  setExplanationMCQId: (id: number | null) => void;
  explanationData: AnswerResponse | null;
  explanationLoading: boolean;
  explanationError: string | null;
  // handlers
  handleStartQuiz: () => void;
  practiceScope: PracticeScope;
  setPracticeScope: (s: PracticeScope) => void;
  handleSubmitQuiz: () => void;
  handleSelectOption: (key: string) => void;
  handleStartDrill?: () => void;
  fetchExplanation: (mcqId: number) => void;
  formatTime: (totalSec: number) => string;
  // other
  stats: any;
  bookmarkedMcqs: any[];
  token: string | null;
  isAdmin?: boolean;                 // shared sets can only be deleted by an admin
  toggleBookmarkMCQ: (mcqId: number) => void;
  setActiveView: (view: any) => void;
  onFigureClick: (fig: Figure) => void;
  books?: Book[];
  getHeaders?: () => HeadersInit;
  startAiCustomQuiz?: (quizSetId: string) => Promise<void>;
  startQuizWith?: (filters: Record<string, unknown>, label?: string, returnTo?: string) => Promise<void>;
  lastRun?: { filters: Record<string, unknown>; label: string; returnTo?: string; total: number; unseen: number; batch: number } | null;
}

interface AiQuizSetSummary {
  quiz_set_id: string;
  quiz_set_title: string;
  question_count: number;
  topic: string;
  difficulty?: number | null;
}

interface StudioMessage {
  id: string;
  sender: "user" | "ai";
  content?: string;
  isError?: boolean;
  retryPrompt?: string;
  quizResult?: {
    quiz_set_id: string;
    quiz_set_title: string;
    total_questions: number;
    difficulty?: number | null;
    mcqs: Array<{
      id: number;
      question_text: string;
      options: Record<string, string>;
      correct_option: string;
      difficulty?: number | null;
      explanation_markdown?: string;
    }>;
  };
  timestamp: string;
}

// What a finished background job (AI quiz or hardening) returns, plus the result
// shape the chat cards and Manual-builder harden card render.
interface QuizJobResult {
  quiz_set_id: string;
  quiz_set_title: string;
  total_questions: number;
  difficulty?: number | null;
  duplicates_skipped?: number;
  failed_batches?: number;
  grounding?: string;
  mcqs: Array<{
    id: number;
    question_text: string;
    options: Record<string, string>;
    correct_option: string;
    difficulty?: number | null;
    explanation_markdown?: string;
  }>;
}

// Mock Builder categories, grouped: the exam banks, sets written for this student, and non-FCPS exams.
const CATEGORY_GROUPS: { title: string; match: (c: string) => boolean }[] = [
  { title: "Question banks", match: () => true },
  { title: "Made for you", match: (c) => ["AI MCQs", "Concept re-test", "Past-paper twists", "Look-alikes", "Spot the diagnosis", "High-yield", "Hardened MCQs"].includes(c) },
  { title: "Other exams", match: (c) => ["English", "NTS MCQ bank", "NTS mocks"].includes(c) },
];
const SMALL_CATEGORY = 20;   // sets smaller than this sit behind "More"
const categoryCount = (c: any): number => c.sub_categories.reduce((sum: number, s: any) => sum + s.count, 0);
function groupCategories(categories: any[]): { title: string; items: any[] }[] {
  const groups = CATEGORY_GROUPS.map((g) => ({ title: g.title, items: [] as any[] }));
  for (const c of categories) {
    const i = CATEGORY_GROUPS.findIndex((g, k) => k > 0 && g.match(c.main_category));
    groups[i < 0 ? 0 : i].items.push(c);
  }
  return groups.filter((g, k) => k === 0 || g.items.length);
}

export default function QuizView({
  quizStep,
  setQuizStep,
  quizMCQs,
  quizCurrentIdx,
  setQuizCurrentIdx,
  quizSelectedAnswers,
  quizConfidence = {},
  setQuizConfidence,
  quizIsLoading,
  quizIsSubmitting,
  quizConfigCategories,
  setQuizConfigCategories,
  quizConfigSubCategories,
  setQuizConfigSubCategories,
  quizConfigNumQuestions,
  setQuizConfigNumQuestions,
  quizConfigTimerMode,
  setQuizConfigTimerMode,
  quizConfigTimerValue,
  setQuizConfigTimerValue,
  quizConfigExcludeMastered,
  setQuizConfigExcludeMastered,
  quizConfigFeedbackMode,
  setQuizConfigFeedbackMode,
  quizConfigStep,
  setQuizConfigStep,
  quizTimerCountdown,
  setQuizTimerCountdown,
  summaryReviewIdx,
  setSummaryReviewIdx,
  quizSecondsElapsed,
  lastSelectedChoice,
  setLastSelectedChoice,
  isCorrectSelection,
  setIsCorrectSelection,
  explanationMCQId,
  setExplanationMCQId,
  explanationData,
  explanationLoading,
  explanationError,
  handleStartQuiz,
  practiceScope,
  setPracticeScope,
  handleSubmitQuiz,
  handleSelectOption,
  handleStartDrill,
  fetchExplanation,
  formatTime,
  stats,
  bookmarkedMcqs,
  token,
  isAdmin = false,
  toggleBookmarkMCQ,
  setActiveView,
  onFigureClick,
  books = [],
  getHeaders,
  startAiCustomQuiz,
  startQuizWith,
  lastRun,
}: QuizViewProps) {
  // Segmented control and generation states inside QuizView
  const [showSmallCategories, setShowSmallCategories] = React.useState(false);
  // Rules step: the questions as written, or AI-hardened versions (difficulty 4 or 5) prepared before the session.
  const [hardenLevel, setHardenLevel] = React.useState<HardenLevel>(0);
  // Practice by subject/topic: the picked scope, its size, and the "new angle" (twists) style.
  const scopeOn = scopeActive(practiceScope);
  const [scopeCount, setScopeCount] = React.useState(0);
  const [newAngle, setNewAngle] = React.useState(false);
  const twistsAllowed = scopeOn && practiceScope.sources.includes("past");
  React.useEffect(() => { if (!twistsAllowed && newAngle) setNewAngle(false); }, [twistsAllowed, newAngle]);
  const pickScope = (sc: PracticeScope) => {
    setPracticeScope(sc);
    if (scopeActive(sc)) { setQuizConfigCategories([]); setQuizConfigSubCategories([]); }
  };
  const clearScope = () => setPracticeScope({ ...EMPTY_SCOPE, sources: practiceScope.sources });
  const hardenBlocked = scopeOn
    ? (quizConfigNumQuestions > HARDEN_MAX_QUESTIONS ? `AI difficulty is available for sessions of ${HARDEN_MAX_QUESTIONS} questions or fewer.` : null)
    : hardenBlockReason(quizConfigCategories, quizConfigSubCategories, quizConfigNumQuestions);
  React.useEffect(() => { if (hardenBlocked && hardenLevel) setHardenLevel(0); }, [hardenBlocked, hardenLevel]);
  // A harden job still running from before a reload: offer to reopen its progress on the Review step.
  const [pendingHarden, setPendingHarden] = React.useState<4 | 5 | null>(null);
  React.useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(HARDEN_STORAGE_KEY) || "null");
      if (saved?.jobId) setPendingHarden(saved.difficulty === 5 ? 5 : 4);
    } catch {}
  }, []);
  const { preview: hardenPreview } = useHardenPreview(getHeaders ?? (() => ({})), getHeaders && hardenLevel && !hardenBlocked ? (scopeOn
    ? { scope: practiceScope, num_questions: quizConfigNumQuestions, difficulty: hardenLevel }
    : {
      categories: quizConfigCategories.length ? quizConfigCategories : undefined,
      sub_categories: quizConfigSubCategories.length ? quizConfigSubCategories : undefined,
      num_questions: quizConfigNumQuestions, difficulty: hardenLevel,
    }) : null);
  const startHardened = (quizSetId: string, fillIds?: number[]) => startQuizWith?.({
    quiz_set_id: quizSetId, include_ids: fillIds?.length ? fillIds : undefined,
    num_questions: quizConfigNumQuestions, prefer_unseen: false, exclude_mastered: false,
    timer_mode: quizConfigTimerMode, timer_value: quizConfigTimerValue, feedback_mode: quizConfigFeedbackMode,
  }, `Harder (${hardenLevel}/5) · ${scopeOn ? describeScope(practiceScope) : (quizConfigSubCategories.length ? quizConfigSubCategories : quizConfigCategories).join(", ")}`);
  // New angle: the twists written from the selection's past-paper questions.
  const startTwists = () => startQuizWith?.({
    scope: practiceScope, twists: true, num_questions: quizConfigNumQuestions, prefer_unseen: true, exclude_mastered: false,
    timer_mode: quizConfigTimerMode, timer_value: quizConfigTimerValue, feedback_mode: quizConfigFeedbackMode,
  }, `New angle · ${describeScope(practiceScope)}`);
  const [builderMode, setBuilderMode] = React.useState<"manual" | "ai_assistant" | "saved_history">("manual");
  const [promptInput, setPromptInput] = React.useState("");
  const [selectedBookId, setSelectedBookId] = React.useState<number | "all">("all");
  const [isGenerating, setIsGenerating] = React.useState(false);
  const [aiDifficulty, setAiDifficulty] = React.useState(3); // 1-5, sent to the AI quiz generator
  const [aiCount, setAiCount] = React.useState(5); // F3 — multiples of 5 only: 5/10/15/20
  const [aiProfile, setAiProfile] = React.useState<"fcps" | "usmle" | "quick">("fcps"); // FCPS/USMLE = 5 options A-E
  // Tap-a-topic chips in the AI MCQs tab (the bank's subtopic labels, like the Manual builder pills).
  const [pickedTopic, setPickedTopic] = React.useState<string | null>(null);
  // "Harder versions (AI)": rewrite existing questions into harder statements & options.
  const [generatingNote, setGeneratingNote] = React.useState("Searching your textbooks and drafting board-style MCQs...");
  const [quizHistory, setQuizHistory] = React.useState<AiQuizSetSummary[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = React.useState(false);
  const [historySearch, setHistorySearch] = React.useState("");
  const [expandedQuizId, setExpandedQuizId] = React.useState<string | null>(null);
  const [showQuitModal, setShowQuitModal] = React.useState(false);

  const suggestionChips = [
    {
      icon: Zap,
      label: "5 MCQs from Guyton Page 120",
      query: "5 MCQs from Guyton Page 120",
    },
    {
      icon: HeartPulse,
      label: "Cardiovascular Physiology Board Exam",
      query: "Cardiovascular Physiology Board Exam",
    },
    {
      icon: Wind,
      label: "Pulmonary Gas Exchange & Ventilation",
      query: "Pulmonary Gas Exchange & Ventilation",
    },
    {
      icon: Brain,
      label: "Central Nervous System Neuroanatomy",
      query: "Central Nervous System Neuroanatomy",
    },
  ];

  const filteredHistory = React.useMemo(() => {
    if (!historySearch.trim()) return quizHistory;
    const q = historySearch.toLowerCase();
    return quizHistory.filter(
      (item) =>
        item.quiz_set_title.toLowerCase().includes(q) ||
        (item.topic && item.topic.toLowerCase().includes(q))
    );
  }, [quizHistory, historySearch]);

  // Tap-a-topic chips for the AI MCQs tab: the bank's real subtopics (with counts),
  // exactly what the Manual builder pills show, most-covered first.
  const allTopicChips = React.useMemo(() => {
    const byName = new Map<string, number>();
    for (const cat of stats?.categories || []) {
      for (const sub of cat?.sub_categories || []) {
        const name = String(sub?.name || "").trim();
        if (!name) continue;
        byName.set(name, (byName.get(name) || 0) + Number(sub?.count || 0));
      }
    }
    return Array.from(byName.entries())
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 60);
  }, [stats]);

  const [studioMessages, setStudioMessages] = React.useState<StudioMessage[]>([
    {
      id: "welcome-1",
      sender: "ai",
      content:
        "Welcome to AI Quiz Studio! Ask me to generate custom board-style MCQs from any textbook topic, chapter, or exact page number.",
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    },
  ]);

  const studioMessagesEndRef = React.useRef<HTMLDivElement>(null);

  // Fetch AI quiz history
  const fetchQuizHistory = async () => {
    if (!token || !getHeaders) return;
    setIsLoadingHistory(true);
    try {
      const res = await fetch(`${API}/api/chat/ai-quizzes`, {
        headers: getHeaders(),
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setQuizHistory(data);
      }
    } catch (e) {
      console.error("Failed to fetch quiz history:", e);
    } finally {
      setIsLoadingHistory(false);
    }
  };

  React.useEffect(() => {
    if (token && builderMode === "saved_history") {
      fetchQuizHistory();
    }
  }, [token, builderMode]);

  React.useEffect(() => {
    if (builderMode === "ai_assistant") {
      studioMessagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [studioMessages, isGenerating, builderMode]);

  // Poll a background job until it finishes. Transient network errors
  // (e.g. a proxy restart) are tolerated; only a real job failure or the overall
  // wait cap ends it.
  const waitForJob = async (
    jobId: string, path: string,
    opts: { maxWaitMs?: number; onProgress?: (p: { done: number; total: number; kept: number }) => void } = {},
  ): Promise<QuizJobResult> => {
    if (!getHeaders) throw new Error("Not signed in.");
    const started = Date.now();
    let networkFailures = 0;
    while (Date.now() - started < (opts.maxWaitMs ?? QUIZ_MAX_WAIT_MS)) {
      await new Promise((r) => setTimeout(r, QUIZ_POLL_INTERVAL_MS));
      let res: Response;
      try {
        res = await fetch(`${API}${path}/${encodeURIComponent(jobId)}`, {
          headers: getHeaders(),
          credentials: "include",
        });
      } catch {
        if (++networkFailures >= 10) throw new Error("Lost contact with the server while the job was running.");
        continue;
      }
      networkFailures = 0;
      const body = (await res.json().catch(() => null)) as any;
      if (!res.ok) {
        if (res.status >= 500) continue; // proxy hiccup; keep polling
        throw new Error((body && body.detail) || `Job status check failed (HTTP ${res.status}).`);
      }
      if (body?.progress && opts.onProgress) opts.onProgress(body.progress);
      if (body?.status === "done") return body.result;
      if (body?.status === "failed") throw new Error(body.detail || "The job failed.");
    }
    throw new Error(
      "The job is still running after several minutes. It will appear in Saved History when it finishes."
    );
  };

  const waitForQuizJob = async (jobId: string): Promise<any> =>
    waitForJob(jobId, "/api/chat/generate-ai-quiz/jobs");

  // Phase 6 — turn a missed question into a spaced-repetition flashcard.
  const [flashcardSavedIds, setFlashcardSavedIds] = React.useState<number[]>([]);
  const handleMakeFlashcard = async (mcq: any) => {
    if (!getHeaders) return;
    const answerText = mcq.options?.[mcq.correct_option] ?? "";
    const explanation =
      explanationMCQId === mcq.id && explanationData?.answer_markdown ? explanationData.answer_markdown : "";
    const back = `**${mcq.correct_option}. ${answerText}**${explanation ? `\n\n${explanation.slice(0, 1500)}` : ""}`;
    try {
      const res = await fetch(`${API}/api/flashcards`, {
        method: "POST",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          front: mcq.question_text,
          back,
          topic: mcq.sub_category || mcq.main_category || "Missed MCQ",
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFlashcardSavedIds((prev) => [...prev, mcq.id]);
      toast.success("Saved to flashcards (Study Corner).");
    } catch (e: any) {
      toast.error(`Could not save flashcard: ${e.message || e}`);
    }
  };

  // Handle AI Quiz Generation inside config screen
  const handleGenerate = async (customPrompt?: string) => {
    const queryPrompt = customPrompt || promptInput;
    if (!queryPrompt.trim()) {
      toast.error("Please enter a topic or page prompt.");
      return;
    }
    if (!getHeaders) return;

    const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

    // Only append a new user message if this is not a direct retry of a prompt already in the thread
    if (!customPrompt || !studioMessages.some((m) => m.content === customPrompt)) {
      setStudioMessages((prev) => [
        ...prev,
        {
          id: `user-${Date.now()}`,
          sender: "user",
          content: queryPrompt,
          timestamp: timeStr,
        },
      ]);
    }

    setPromptInput("");
    setIsGenerating(true);

    // Idempotency key: a repeated start (double click, retry) returns the same job.
    const requestId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

    try {
      const startRes = await proxySafeFetch(`${API}/api/chat/generate-ai-quiz/jobs`, {
        method: "POST",
        headers: {
          ...getHeaders(),
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({
          prompt: queryPrompt,
          book_id: selectedBookId === "all" ? null : selectedBookId,
          difficulty: aiDifficulty,
          count: aiCount,
          exam_profile: aiProfile,
          request_id: requestId,
        }),
      });
      const started = (await startRes.json().catch(() => null)) as any;
      if (!startRes.ok || !started?.job_id) {
        throw new Error((started && started.detail) || `Failed to start quiz generation (HTTP ${startRes.status}).`);
      }

      const data = await waitForQuizJob(started.job_id);

      fetchQuizHistory();
      const dupNote =
        data && typeof data.duplicates_skipped === "number" && data.duplicates_skipped > 0
          ? `\n\n_${data.duplicates_skipped} question${data.duplicates_skipped === 1 ? " was" : "s were"} skipped because they repeated existing MCQs on this topic._`
          : "";
      const g = data?.grounding_counts;
      const groundingNote =
        g && typeof g.book === "number"
          ? `\n\n${g.book} from your textbooks (cited book & page)${g.ai ? `, ${g.ai} from AI clinical knowledge (labelled, no page)` : ""}.`
          : "";
      setStudioMessages((prev) => [
        ...prev,
        {
          id: `ai-${Date.now()}`,
          sender: "ai",
          content: `I've generated **${data.quiz_set_title}** containing ${data.total_questions} MCQs.${groundingNote}${dupNote}`,
          quizResult: data,
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
    } catch (err: any) {
      setStudioMessages((prev) => [
        ...prev,
        {
          id: `ai-err-${Date.now()}`,
          sender: "ai",
          isError: true,
          retryPrompt: queryPrompt,
          content: err.message || "Failed to retrieve textbook context or generate questions.",
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
    } finally {
      setIsGenerating(false);
    }
  };

  // Start a harden job and wait for it: AI rewrites existing questions (either explicit
  // seed_ids or the current topic selection) into harder statements & options at a chosen
  // difficulty, keeping the same facts and correct answers.
  const startHarden = async (payload: Record<string, unknown>): Promise<QuizJobResult> => {
    if (!getHeaders) throw new Error("Not signed in.");
    const requestId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const startRes = await proxySafeFetch(`${API}/api/chat/harden/jobs`, {
      method: "POST",
      headers: { ...getHeaders(), "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ ...payload, request_id: requestId }),
    });
    const started = (await startRes.json().catch(() => null)) as { job_id?: string; detail?: string } | null;
    if (!startRes.ok || !started?.job_id) {
      throw new Error((started && started.detail) || `Failed to start hardening (HTTP ${startRes.status}).`);
    }
    // Hardening checks every rewrite against the textbooks: a 20-question set can take half an hour on the NAS.
    return waitForJob(started.job_id, "/api/chat/harden/jobs", {
      maxWaitMs: 30 * 60_000,
      onProgress: (p) => setGeneratingNote(`Rewriting to a harder difficulty: checked ${p.done} of ${p.total}, ${p.kept} kept so far...`),
    });
  };

  const handleHardenFromSet = async (set: StudioMessage["quizResult"]) => {
    const ids = (set?.mcqs || []).map((m) => Number(m?.id)).filter(Boolean).slice(0, 20);
    if (!ids.length) {
      toast.error("No questions in that set to rewrite.");
      return;
    }
    const target = Math.max(4, aiDifficulty) as 4 | 5;
    const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setStudioMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        sender: "user",
        content: `Rewrite this set harder (difficulty ${target}/5): ${set?.quiz_set_title || "previous set"}`,
        timestamp: timeStr,
      },
    ]);
    setGeneratingNote("Rewriting these questions to a harder difficulty (same facts, tougher statements and options)...");
    setIsGenerating(true);
    try {
      const data = await startHarden({
        seed_ids: ids,
        num_questions: Math.max(5, Math.min(20, Math.round(ids.length / 5) * 5)),
        difficulty: target,
        label: String(set?.quiz_set_title || "practice").replace(/^(AI\s*)?/i, ""),
      });
      setStudioMessages((prev) => [
        ...prev,
        {
          id: `ai-${Date.now()}`,
          sender: "ai",
          content:
            `I've rewritten **${data.quiz_set_title}** into ${data.total_questions} harder versions - the same facts and ` +
            `correct answers, but tougher statements and options at difficulty ${data.difficulty}/5. It is saved in Quiz History.`,
          quizResult: data,
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
      fetchQuizHistory();
    } catch (err) {
      setStudioMessages((prev) => [
        ...prev,
        {
          id: `ai-err-${Date.now()}`,
          sender: "ai",
          isError: true,
          content: err instanceof Error ? err.message : String(err),
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
    } finally {
      setIsGenerating(false);
      setGeneratingNote("Searching your textbooks and drafting board-style MCQs...");
    }
  };

  // Handle Delete Quiz Set
  const handleDeleteQuizSet = async (quizSetId: string, title: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!getHeaders) return;
    try {
      const res = await fetch(`${API}/api/chat/ai-quizzes/${quizSetId}`, {
        method: "DELETE",
        headers: getHeaders(),
        credentials: "include",
      });
      if (res.ok) {
        toast.success(`Deleted "${title}" quiz set.`);
        fetchQuizHistory();
      } else {
        toast.error("Failed to delete quiz set.");
      }
    } catch {
      toast.error("An error occurred during deletion.");
    }
  };

  // ─── Stage 1: Configure ───────────────────────────────────────────────────
  if (quizStep === "config") {
    const mainCategories = stats?.categories || [];

    const toggleCategory = (catName: string) => {
      clearScope();
      setQuizConfigCategories((prev) => {
        const isSelected = prev.includes(catName);
        let newCats = [];
        if (isSelected) {
          newCats = prev.filter((c) => c !== catName);
        } else {
          newCats = [...prev, catName];
        }
        const targetCatObj = mainCategories.find((c: any) => c.main_category === catName);
        if (targetCatObj) {
          const subNames = targetCatObj.sub_categories.map((s: any) => s.name);
          setQuizConfigSubCategories((subPrev) => {
            if (isSelected) {
              return subPrev.filter((s) => !subNames.includes(s));
            } else {
              return Array.from(new Set([...subPrev, ...subNames]));
            }
          });
        }
        return newCats;
      });
    };

    const subCategoryOptions = mainCategories.reduce((acc: any[], cat: any) => {
      if (quizConfigCategories.length === 0 || quizConfigCategories.includes(cat.main_category)) {
        cat.sub_categories.forEach((sub: any) => {
          if (!acc.some((s: any) => s.name === sub.name)) {
            acc.push({ ...sub, main_category: cat.main_category });
          }
        });
      }
      return acc;
    }, []);

    const toggleSubCategory = (subName: string) => {
      clearScope();
      setQuizConfigSubCategories((prev) => {
        const isSelected = prev.includes(subName);
        let newSubs = [];
        if (isSelected) {
          newSubs = prev.filter((s) => s !== subName);
        } else {
          newSubs = [...prev, subName];
        }
        const targetSub = subCategoryOptions.find((s: any) => s.name === subName);
        if (targetSub && !isSelected) {
          setQuizConfigCategories((catPrev) => {
            if (!catPrev.includes(targetSub.main_category)) {
              return [...catPrev, targetSub.main_category];
            }
            return catPrev;
          });
        }
        return newSubs;
      });
    };

    const totalSystemMCQs = mainCategories.reduce(
      (sum: number, c: any) =>
        sum + c.sub_categories.reduce((s: number, sub: any) => s + sub.count, 0),
      0
    );

    let activeSubMCQs = 0;
    if (scopeOn) {
      activeSubMCQs = scopeCount;
    } else if (quizConfigSubCategories.length > 0) {
      activeSubMCQs = subCategoryOptions
        .filter((s: any) => quizConfigSubCategories.includes(s.name))
        .reduce((sum: number, s: any) => sum + s.count, 0);
    } else {
      if (quizConfigCategories.length > 0) {
        activeSubMCQs = mainCategories
          .filter((c: any) => quizConfigCategories.includes(c.main_category))
          .reduce(
            (sum: number, c: any) =>
              sum + c.sub_categories.reduce((s: number, sub: any) => s + sub.count, 0),
            0
          );
      } else {
        activeSubMCQs = totalSystemMCQs;
      }
    }

    const coveragePercent = totalSystemMCQs > 0 ? Math.round((activeSubMCQs / totalSystemMCQs) * 100) : 0;
    const strokeDashoffset = 314.16 - (coveragePercent / 100) * 314.16;

    const handleNextStep = () => {
      if (quizConfigStep === 1) {
        const activeSubCount =
          quizConfigSubCategories.length > 0
            ? quizConfigSubCategories.length
            : quizConfigCategories.length > 0
            ? subCategoryOptions.length
            : totalSystemMCQs;
        if (activeSubCount === 0) {
          toast.warning("Please select at least one category or subtopic to proceed.");
          return;
        }
        setQuizConfigStep(2);
      } else if (quizConfigStep === 2) {
        setQuizConfigStep(3);
      }
    };

    const handlePrevStep = () => {
      if (quizConfigStep === 2) {
        setQuizConfigStep(1);
      } else if (quizConfigStep === 3) {
        setQuizConfigStep(2);
      }
    };

    return (
      <div className="dashboard-view" role="region" aria-label="Practice Settings">
        <div
          className="dashboard-header"
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
          <div>
            <h1 className="dashboard-title">Practice</h1>
            <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--text-secondary)" }}>Set up a practice session: topics, rules, then review.</p>
          </div>
          <button className="btn-workspace" onClick={() => setActiveView("dashboard")}>
            Back to Dashboard
          </button>
        </div>

        {/* Horizontal Stepper Indicator */}
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            gap: "var(--sp-4)",
            margin: "var(--sp-4) 0 var(--sp-6)",
            padding: "var(--sp-3) var(--sp-4)",
            background: "var(--surface-2)",
            border: "1px solid var(--border-light)",
            borderRadius: "var(--r-xl)",
          }}
        >
          {[1, 2, 3].map((step, i) => (
            <React.Fragment key={step}>
              {i > 0 && <div style={{ width: "40px", height: "1px", background: "var(--border-light)" }} />}
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span
                  style={{
                    width: "24px",
                    height: "24px",
                    borderRadius: "50%",
                    background: quizConfigStep === step ? "var(--sky)" : "var(--surface-3)",
                    color: quizConfigStep === step ? "#000" : "var(--text-secondary)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontWeight: 600,
                    fontSize: "0.75rem",
                  }}
                >
                  {quizConfigStep > step ? "✓" : step}
                </span>
                <span
                  style={{
                    fontSize: "0.82rem",
                    fontWeight: quizConfigStep === step ? 600 : 500,
                    color: quizConfigStep === step ? "var(--text-primary)" : "var(--text-muted)",
                  }}
                >
                  {["Topics", "Rules", "Review"][i]}
                </span>
              </div>
            </React.Fragment>
          ))}
        </div>

        {pendingHarden && quizConfigStep !== 3 ? (
          <div role="status" style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", margin: "0 0 var(--sp-4)",
            padding: "10px 14px", borderRadius: "12px", border: "1px solid var(--teal)", background: "rgba(76, 217, 100, 0.06)", fontSize: "0.8rem" }}>
            <Loader2 size={14} className="animate-spin" style={{ color: "var(--teal)" }} />
            <span style={{ flex: 1 }}>Harder versions are being prepared in the background.</span>
            <button type="button" className="btn-workspace" style={{ padding: "4px 12px", fontSize: "0.76rem" }}
              onClick={() => { setHardenLevel(pendingHarden); setPendingHarden(null); setQuizConfigStep(3); }}>
              Show progress
            </button>
          </div>
        ) : null}

        {/* Step 1: Topics */}
        {quizConfigStep === 1 && (
          <div key="step-1" className="step-transition-wrapper quiz-config-form-col">
            <div>
              <h3 className="practice-title" style={{ fontSize: "1.15rem", fontWeight: 600 }}>
                Select Practice Topics
              </h3>
              <p className="practice-subtitle" style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: "4px" }}>
                Choose main subject categories, consult the AI Generator Assistant, or select a previously saved custom quiz.
              </p>
            </div>

            {/* Segmented Control Mode Tabs */}
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: "6px",
              background: "var(--surface-2)",
              border: "1px solid var(--border-light)",
              borderRadius: "14px",
              padding: "5px",
              marginBottom: "24px",
              width: "100%"
            }}>
              <button
                type="button"
                onClick={() => setBuilderMode("manual")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "8px",
                  padding: "10px 12px",
                  fontSize: "0.83rem",
                  fontWeight: builderMode === "manual" ? 700 : 500,
                  color: builderMode === "manual" ? "var(--sky)" : "var(--text-secondary)",
                  background: builderMode === "manual" ? "var(--surface-1)" : "transparent",
                  border: builderMode === "manual" ? "1px solid var(--sky)" : "1px solid transparent",
                  borderRadius: "10px",
                  cursor: "pointer",
                  boxShadow: builderMode === "manual" ? "0 2px 10px rgba(48, 197, 255, 0.18)" : "none",
                  transition: "all 0.2s ease"
                }}
              >
                <Plus size={14} style={{ color: builderMode === "manual" ? "var(--sky)" : "var(--text-muted)" }} />
                <span>Manual Builder</span>
              </button>

              <button
                type="button"
                onClick={() => setBuilderMode("ai_assistant")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "8px",
                  padding: "10px 12px",
                  fontSize: "0.83rem",
                  fontWeight: builderMode === "ai_assistant" ? 700 : 500,
                  color: builderMode === "ai_assistant" ? "var(--sky)" : "var(--text-secondary)",
                  background: builderMode === "ai_assistant" ? "var(--surface-1)" : "transparent",
                  border: builderMode === "ai_assistant" ? "1px solid var(--sky)" : "1px solid transparent",
                  borderRadius: "10px",
                  cursor: "pointer",
                  boxShadow: builderMode === "ai_assistant" ? "0 2px 10px rgba(48, 197, 255, 0.18)" : "none",
                  transition: "all 0.2s ease"
                }}
              >
                <Sparkles size={14} style={{ color: builderMode === "ai_assistant" ? "var(--sky)" : "var(--text-muted)" }} />
                <span>AI MCQs</span>
              </button>

              <button
                type="button"
                onClick={() => setBuilderMode("saved_history")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "8px",
                  padding: "10px 12px",
                  fontSize: "0.83rem",
                  fontWeight: builderMode === "saved_history" ? 700 : 500,
                  color: builderMode === "saved_history" ? "var(--sky)" : "var(--text-secondary)",
                  background: builderMode === "saved_history" ? "var(--surface-1)" : "transparent",
                  border: builderMode === "saved_history" ? "1px solid var(--sky)" : "1px solid transparent",
                  borderRadius: "10px",
                  cursor: "pointer",
                  boxShadow: builderMode === "saved_history" ? "0 2px 10px rgba(48, 197, 255, 0.18)" : "none",
                  transition: "all 0.2s ease"
                }}
              >
                <Clock size={14} style={{ color: builderMode === "saved_history" ? "var(--sky)" : "var(--text-muted)" }} />
                <span>Quiz History</span>
              </button>
            </div>

            {/* F4 — drill previously-missed questions */}
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "14px" }}>
              <button
                type="button"
                className="btn-workspace"
                onClick={handleStartDrill}
                disabled={quizIsLoading}
                title="Practice only the questions you've answered incorrectly before"
                style={{ display: "inline-flex", alignItems: "center", gap: "6px", padding: "6px 14px", fontSize: "0.76rem" }}
              >
                {quizIsLoading ? (
                  <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
                ) : (
                  <RotateCcw size={12} />
                )}
                Drill Missed Questions
              </button>
            </div>

            {/* Mode Content Container with Fixed Min-Height & Transition Animations */}
            <div style={{ minHeight: "440px", display: "flex", flexDirection: "column", position: "relative" }}>
              <AnimatePresence mode="wait">
                <motion.div
                  key={builderMode}
                  initial={{ opacity: 0, y: 8, filter: "blur(3px)" }}
                  animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, y: -8, filter: "blur(3px)" }}
                  transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
                  style={{ flex: 1, display: "flex", flexDirection: "column" }}
                >
                  {/* Mode 1: Manual Builder */}
                  {builderMode === "manual" && (
                    <>
                      {/* Phase 6 — one-click FCPS-style mock papers using the existing timed + board modes */}
                      <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}>
                        {MOCK_PRESETS.map((preset) => (
                          <button
                            key={preset.key}
                            type="button"
                            onClick={() => {
                              setQuizConfigCategories([]);
                              setQuizConfigSubCategories([]);
                              clearScope();
                              setQuizConfigNumQuestions(Math.max(1, Math.min(preset.questions, totalSystemMCQs || preset.questions)));
                              setQuizConfigTimerMode("session");
                              setQuizConfigTimerValue(preset.minutes);
                              setQuizConfigFeedbackMode("board");
                              setQuizConfigExcludeMastered(false);
                              setQuizConfigStep(3);
                              if (totalSystemMCQs && totalSystemMCQs < preset.questions) {
                                toast.warning(`Only ${totalSystemMCQs} questions in the bank yet; the paper will use all of them.`);
                              } else {
                                toast.success(`${preset.title} set up: review the settings and start.`);
                              }
                            }}
                            style={{
                              flex: "1 1 240px",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "space-between",
                              gap: "12px",
                              padding: "12px 14px",
                              borderRadius: "12px",
                              border: "1px dashed var(--sky)",
                              background: "rgba(48,197,255,0.06)",
                              color: "var(--text-primary)",
                              cursor: "pointer",
                              textAlign: "left",
                            }}
                            title="All subjects, unseen questions first, one session timer, answers revealed at the end (75% pass line)"
                          >
                            <span style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                              <span style={{ fontWeight: 700, fontSize: "0.86rem" }}>{preset.title}</span>
                              <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>
                                {preset.questions} questions · {preset.minutes} min · all subjects · board mode · unseen first
                              </span>
                            </span>
                            <Clock size={16} style={{ color: "var(--sky)", flexShrink: 0 }} />
                          </button>
                        ))}
                      </div>

                      {getHeaders ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)", marginBottom: "var(--sp-2)" }}>
                          <label style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                            FCPS Part 1 by subject &amp; topic
                          </label>
                          <PracticePicker getHeaders={getHeaders} scope={practiceScope} setScope={pickScope} onSize={setScopeCount} />
                        </div>
                      ) : null}

                      <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)", opacity: scopeOn ? 0.55 : 1 }}>
                        <label style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                          Other question sets {scopeOn ? <span style={{ textTransform: "none", fontWeight: 400 }}>(picking one replaces the subject selection)</span> : null}
                        </label>
                        {groupCategories(mainCategories).map((group) => {
                          const shown = showSmallCategories ? group.items : group.items.filter((c: any) => categoryCount(c) >= SMALL_CATEGORY);
                          const hidden = group.items.length - shown.length;
                          return (
                            <div key={group.title} style={{ display: "flex", flexDirection: "column", gap: "6px", marginBottom: "10px" }}>
                              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>{group.title}</div>
                              <div className="config-category-grid" role="group" aria-label={group.title}>
                                {group.title === CATEGORY_GROUPS[0].title ? (
                                  <button
                                    type="button"
                                    className={`config-category-card ${quizConfigCategories.length === 0 && !scopeOn ? "active" : ""}`}
                                    onClick={() => { setQuizConfigCategories([]); setQuizConfigSubCategories([]); clearScope(); }}
                                  >
                                    <span className="config-category-title">Mixed Practice (All)</span>
                                    <span className="config-category-subtitle">Every subject you can practise</span>
                                  </button>
                                ) : null}
                                {shown.map((c: any) => {
                                  const isSelected = quizConfigCategories.includes(c.main_category);
                                  return (
                                    <button
                                      key={c.main_category}
                                      type="button"
                                      className={`config-category-card ${isSelected ? "active" : ""}`}
                                      onClick={() => toggleCategory(c.main_category)}
                                    >
                                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", width: "100%", gap: "12px" }}>
                                        <span className="config-category-title" title={c.main_category} style={{ textAlign: "left", flex: 1 }}>{c.main_category}</span>
                                        <div style={{ flexShrink: 0, marginTop: "2px" }}>
                                          <Checkbox checked={isSelected} readOnly />
                                        </div>
                                      </div>
                                      <span className="config-category-subtitle">{c.sub_categories.length} subtopics · {categoryCount(c).toLocaleString()} MCQs</span>
                                    </button>
                                  );
                                })}
                              </div>
                              {hidden > 0 ? (
                                <button type="button" className="btn-workspace" style={{ padding: "3px 10px", fontSize: "0.72rem" }}
                                  onClick={() => setShowSmallCategories(true)}>
                                  More ({hidden} small set{hidden > 1 ? "s" : ""})
                                </button>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>

                      <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)", marginTop: "24px" }}>
                        <label style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                          Sub-Category Topics
                        </label>
                        <div className="config-sub-pills-list">
                          <button
                            type="button"
                            className={`config-sub-pill ${quizConfigSubCategories.length === 0 ? "active" : ""}`}
                            onClick={() => setQuizConfigSubCategories([])}
                          >
                            All Subtopics
                          </button>
                          {subCategoryOptions.map((s: any, i: number) => {
                            const isSelected = quizConfigSubCategories.includes(s.name);
                            return (
                              <button
                                key={i}
                                type="button"
                                className={`config-sub-pill ${isSelected ? "active" : ""}`}
                                onClick={() => toggleSubCategory(s.name)}
                              >
                                {isSelected ? "✓ " : ""}{s.name} ({s.count})
                              </button>
                            );
                          })}
                        </div>
                      </div>


                      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "auto", paddingTop: "var(--sp-4)" }}>
                        <button className="btn-primary" onClick={handleNextStep}>
                          <span>Configure Session Rules</span>
                          <ArrowRight size={16} style={{ marginLeft: "4px" }} />
                        </button>
                      </div>
                    </>
                  )}

                  {/* Mode 2: AI Assistant Generator */}
                  {builderMode === "ai_assistant" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "16px", minHeight: "400px", background: "var(--surface-1)", border: "1px solid var(--border-light)", borderRadius: "var(--r-xl)", padding: "16px", flex: 1 }}>
                      {/* Chat Message Stream */}
                      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "16px", maxHeight: "350px", minHeight: "220px", paddingRight: "4px" }}>
                        {studioMessages.length === 1 ? (
                          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", padding: "24px 12px" }}>
                            <h4 style={{ fontFamily: "var(--font-serif)", fontSize: "1.3rem", color: "var(--text-primary)", marginBottom: "8px" }}>
                              Generate clinical board <em>quizzes</em>
                            </h4>
                            <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)", maxWidth: "400px", lineHeight: "1.5", marginBottom: "20px" }}>
                              Specify a page or topic to build board-style vignettes grounded in textbooks.
                            </p>
                            <div className="suggestion-grid" style={{ maxWidth: "540px", gridTemplateColumns: "1fr 1fr" }}>
                              {suggestionChips.map((s, i) => {
                                const Icon = s.icon;
                                return (
                                  <button
                                    key={i}
                                    type="button"
                                    className="suggestion-chip"
                                    onClick={() => {
                                      setPromptInput(s.query);
                                      handleGenerate(s.query);
                                    }}
                                    style={{ padding: "10px", fontSize: "0.78rem" }}
                                  >
                                    <Icon size={12} className="text-[var(--sky)] shrink-0" style={{ marginTop: "2px" }} />
                                    <span style={{ textAlign: "left" }}>{s.label}</span>
                                  </button>
                                );
                              })}
                            </div>
                          </div>
                        ) : (
                          <div className="chat-message-thread" style={{ padding: 0 }}>
                            {studioMessages.map((msg) =>
                              msg.sender === "user" ? (
                                <div key={msg.id} className="user-message" style={{ margin: "4px 0" }}>
                                  <div className="user-bubble" style={{ padding: "10px 14px", fontSize: "0.85rem" }}>
                                    <div>{msg.content}</div>
                                    {msg.timestamp && (
                                      <div style={{ fontSize: "0.62rem", opacity: 0.7, marginTop: "4px", textAlign: "right" }}>
                                        {msg.timestamp}
                                      </div>
                                    )}
                                  </div>
                                </div>
                              ) : (
                                <div key={msg.id} className="ai-message" style={{ margin: "4px 0" }}>
                                  <div className="ai-body">
                                    <div className="ai-editorial-header" style={{ marginBottom: "6px" }}>
                                      <Stethoscope size={12} className="ai-editorial-icon" />
                                      <span className="ai-editorial-name">Dr. MedNama</span>
                                    </div>
                                    {msg.isError ? (
                                      <div className="workspace-card quiz-error-card" style={{ marginTop: "6px", background: "rgba(239, 68, 68, 0.08)", border: "1px solid rgba(239, 68, 68, 0.25)", borderRadius: "var(--r-lg)", padding: "14px", display: "flex", flexDirection: "column", gap: "10px" }}>
                                        <div style={{ display: "flex", alignItems: "flex-start", gap: "8px", color: "#ef4444", fontSize: "0.82rem" }}>
                                          <AlertTriangle size={16} className="shrink-0" style={{ marginTop: "2px" }} />
                                          <div>
                                            <span style={{ fontWeight: 600, display: "block", marginBottom: "2px" }}>Quiz Generation Failed</span>
                                            <span style={{ color: "var(--text-secondary)", fontSize: "0.8rem" }}>{msg.content}</span>
                                          </div>
                                        </div>
                                        {msg.retryPrompt && (
                                          <button
                                            type="button"
                                            onClick={() => handleGenerate(msg.retryPrompt)}
                                            disabled={isGenerating}
                                            className="btn-secondary py-1.5 px-3 text-xs font-bold rounded-lg flex items-center gap-1.5"
                                            style={{ alignSelf: "flex-start", background: "var(--surface-3)", border: "1px solid var(--border-light)", color: "var(--text-primary)", cursor: isGenerating ? "not-allowed" : "pointer" }}
                                          >
                                            <RotateCcw size={13} className={isGenerating ? "animate-spin" : ""} />
                                            <span>Retry Quiz Generation</span>
                                          </button>
                                        )}
                                      </div>
                                    ) : (
                                      <div className="prose text-xs text-[var(--text-primary)] leading-relaxed">
                                        <p>{msg.content}</p>
                                      </div>
                                    )}

                                    {msg.quizResult && (
                                      <div className="workspace-card quiz-card" style={{ marginTop: "12px", background: "var(--surface-2)", border: "1px solid var(--border-light)", borderRadius: "var(--r-lg)", padding: "14px", display: "flex", flexDirection: "column", gap: "8px" }}>
                                        <div className="flex justify-between items-start">
                                          <div>
                                            <div className="workspace-badge" style={{ display: "inline-flex", background: "var(--sky-dim)", color: "var(--sky)", fontSize: "0.65rem", padding: "1px 6px" }}>
                                              {msg.quizResult.total_questions} MCQs Ready
                                            </div>
                                            {!!msg.quizResult.difficulty && (
                                              <div className="workspace-badge" style={{ display: "inline-flex", background: "var(--surface-3)", color: "var(--text-secondary)", fontSize: "0.65rem", padding: "1px 6px", marginLeft: "6px" }}>
                                                Difficulty {msg.quizResult.difficulty}/5
                                              </div>
                                            )}
                                            <h4 style={{ fontSize: "0.9rem", fontWeight: 700, marginTop: "4px" }}>
                                              {msg.quizResult.quiz_set_title}
                                            </h4>
                                          </div>
                                        </div>

                                        <div style={{ display: "flex", gap: "8px", marginTop: "4px" }}>
                                          <button
                                            type="button"
                                            onClick={() => {
                                              if (startAiCustomQuiz) {
                                                startAiCustomQuiz(msg.quizResult!.quiz_set_id);
                                              }
                                            }}
                                            className="btn-primary py-2 px-3 text-xs font-bold rounded-lg flex items-center gap-1"
                                            style={{ minHeight: "36px" }}
                                          >
                                            <Play size={12} fill="currentColor" />
                                            <span>Practice Now</span>
                                          </button>
                                          <button
                                            type="button"
                                            onClick={() => handleHardenFromSet(msg.quizResult)}
                                            disabled={isGenerating}
                                            className="btn-secondary py-2 px-3 text-xs font-bold rounded-lg flex items-center gap-1"
                                            style={{ minHeight: "36px", background: "var(--surface-3)", border: "1px solid var(--border-light)", color: "var(--teal)", cursor: isGenerating ? "not-allowed" : "pointer" }}
                                            title="Rewrite the same facts and answers at a harder difficulty"
                                          >
                                            {isGenerating ? <Loader2 size={12} className="animate-spin" /> : <Layers size={12} />}
                                            <span>{isGenerating ? "Rewriting…" : "Make harder (AI)"}</span>
                                          </button>
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                </div>
                              )
                            )}
                            {isGenerating && (
                              <div className="ai-message" style={{ margin: "8px 0" }}>
                                <div className="ai-body" style={{ background: "var(--surface-2)", border: "1px solid var(--border-light)", borderRadius: "var(--r-lg)", padding: "14px" }}>
                                  <div className="ai-editorial-header" style={{ marginBottom: "8px", display: "flex", alignItems: "center", gap: "6px" }}>
                                    <Sparkles size={14} className="ai-editorial-icon text-[var(--sky)] animate-pulse" />
                                    <span className="ai-editorial-name" style={{ fontWeight: 600, fontSize: "0.8rem", color: "var(--sky)" }}>Dr. MedNama (Generating Quiz...)</span>
                                  </div>
                                  <div style={{ display: "flex", alignItems: "center", gap: "10px", color: "var(--text-secondary)", fontSize: "0.82rem" }}>
                                    <Loader2 size={16} className="animate-spin text-[var(--sky)] shrink-0" />
                                    <span>{generatingNote}</span>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>

                      {allTopicChips.length ? (
                        <div style={{ marginTop: "10px" }}>
                          <div style={{ fontSize: "0.66rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "6px" }}>
                            Or tap a topic from your bank
                          </div>
                          <div className="config-sub-pills-list" style={{ maxHeight: "112px", overflowY: "auto", paddingRight: "2px" }}>
                            {allTopicChips.map((c) => (
                              <button
                                key={c.name}
                                type="button"
                                className={`config-sub-pill ${pickedTopic === c.name ? "active" : ""}`}
                                title={`Generate MCQs on ${c.name}`}
                                onClick={() => {
                                  setPickedTopic(c.name);
                                  handleGenerate(c.name);
                                }}
                              >
                                {pickedTopic === c.name ? "✓ " : ""}{c.name} ({c.count})
                              </button>
                            ))}
                          </div>
                        </div>
                      ) : null}

                      {/* Chat Input */}
                      <div className="chat-composer-box" style={{ padding: "8px 12px", background: "var(--surface-2)", borderRadius: "var(--r-md)", border: "1px solid var(--border-light)" }}>
                        <textarea
                          className="input-box"
                          placeholder="Ask AI to generate MCQs from a textbook chapter..."
                          rows={2}
                          value={promptInput}
                          onChange={(e) => setPromptInput(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                              e.preventDefault();
                              handleGenerate();
                            }
                          }}
                          style={{ minHeight: "44px", fontSize: "0.82rem", background: "transparent", border: "none" }}
                        />

                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "6px" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
                            <div style={{ width: "180px" }}>
                              <BasicDropdown
                                items={[
                                  { value: "all", label: "All Books" },
                                  ...(books?.filter((b) => b.status === "ready").map((b) => ({
                                    value: b.id.toString(),
                                    label: b.title,
                                  })) || []),
                                ]}
                                value={selectedBookId.toString()}
                                onChange={(val) => setSelectedBookId(val === "all" ? "all" : Number(val))}
                                ariaLabel="Select book for mock quiz"
                                dropUp={true}
                              />
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                              <span style={{ fontSize: "0.66rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em", marginRight: "2px" }}>
                                Difficulty
                              </span>
                              {[1, 2, 3, 4, 5].map((d) => (
                                <button
                                  key={d}
                                  type="button"
                                  title={`Difficulty ${d}/5`}
                                  onClick={() => setAiDifficulty(d)}
                                  style={{
                                    width: "24px",
                                    height: "24px",
                                    borderRadius: "6px",
                                    border: "1px solid",
                                    borderColor: aiDifficulty === d ? "var(--sky)" : "var(--border-light)",
                                    background: aiDifficulty === d ? "rgba(48,197,255,0.12)" : "var(--surface-3)",
                                    color: aiDifficulty === d ? "var(--sky)" : "var(--text-secondary)",
                                    cursor: "pointer",
                                    fontWeight: 700,
                                    fontSize: "0.72rem",
                                    transition: "all var(--dur-fast)",
                                  }}
                                >
                                  {d}
                                </button>
                              ))}
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                              <span style={{ fontSize: "0.66rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em", marginRight: "2px" }}>
                                Count
                              </span>
                              {[5, 10, 15, 20].map((num) => (
                                <button
                                  key={num}
                                  type="button"
                                  title={`${num} questions`}
                                  onClick={() => setAiCount(num)}
                                  style={{
                                    width: "28px",
                                    height: "24px",
                                    borderRadius: "6px",
                                    border: "1px solid",
                                    borderColor: aiCount === num ? "var(--sky)" : "var(--border-light)",
                                    background: aiCount === num ? "rgba(48,197,255,0.12)" : "var(--surface-3)",
                                    color: aiCount === num ? "var(--sky)" : "var(--text-secondary)",
                                    cursor: "pointer",
                                    fontWeight: 700,
                                    fontSize: "0.7rem",
                                    transition: "all var(--dur-fast)",
                                  }}
                                >
                                  {num}
                                </button>
                              ))}
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                              <span style={{ fontSize: "0.66rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em", marginRight: "2px" }}>
                                Style
                              </span>
                              {([["fcps", "FCPS (A–E)", "CPSP single best answer, 5 options"], ["usmle", "USMLE (A–E)", "Long clinical vignette, 5 options"], ["quick", "Quick recall (A–D)", "Short rapid-recall drill, 4 options"]] as const).map(([val, label, hint]) => (
                                <button
                                  key={val}
                                  type="button"
                                  title={hint}
                                  onClick={() => setAiProfile(val)}
                                  style={{
                                    height: "24px",
                                    padding: "0 8px",
                                    borderRadius: "6px",
                                    border: "1px solid",
                                    borderColor: aiProfile === val ? "var(--sky)" : "var(--border-light)",
                                    background: aiProfile === val ? "rgba(48,197,255,0.12)" : "var(--surface-3)",
                                    color: aiProfile === val ? "var(--sky)" : "var(--text-secondary)",
                                    cursor: "pointer",
                                    fontWeight: 700,
                                    fontSize: "0.7rem",
                                    transition: "all var(--dur-fast)",
                                  }}
                                >
                                  {label}
                                </button>
                              ))}
                            </div>
                          </div>
                          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                            <span className="keyboard-send-hint" style={{ fontSize: "0.68rem" }}>⏎ to generate</span>
                            <button
                              type="button"
                              className="send-btn"
                              onClick={() => handleGenerate()}
                              disabled={isGenerating || !promptInput.trim()}
                              style={{ width: "26px", height: "26px" }}
                            >
                              {isGenerating ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                <Sparkles size={12} />
                              )}
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Mode 3: Saved History */}
                  {builderMode === "saved_history" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px", minHeight: "350px", background: "var(--surface-1)", border: "1px solid var(--border-light)", borderRadius: "var(--r-xl)", padding: "16px", flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", background: "var(--surface-2)", border: "1px solid var(--border-light)", borderRadius: "var(--r-md)", padding: "6px 12px" }}>
                        <Search size={13} className="text-[var(--text-muted)]" />
                        <input
                          type="text"
                          placeholder="Search saved custom sets..."
                          value={historySearch}
                          onChange={(e) => setHistorySearch(e.target.value)}
                          style={{ background: "transparent", border: "none", fontSize: "0.78rem", color: "var(--text-primary)", outline: "none", width: "100%" }}
                        />
                      </div>

                      <div style={{ display: "flex", flexDirection: "column", gap: "2px", overflowY: "auto", flex: 1, maxHeight: "300px" }}>
                        {isLoadingHistory ? (
                          <div className="chat-history-loading">Loading saved history...</div>
                        ) : quizHistory.length === 0 ? (
                          <div style={{ padding: "32px", textAlign: "center", color: "var(--text-muted)", fontSize: "0.82rem" }}>
                            No saved custom quiz sets found.
                          </div>
                        ) : (
                          filteredHistory.map((qSet) => (
                            <div
                              key={qSet.quiz_set_id}
                              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 12px", borderRadius: "var(--r-md)", background: "var(--surface-2)", border: "1px solid var(--border-light)" }}
                            >
                              <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                                <span style={{ fontWeight: 600, fontSize: "0.84rem", color: "var(--text-primary)" }}>{qSet.quiz_set_title}</span>
                                <span style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                                  {qSet.question_count} MCQs · {qSet.topic || "Custom Quiz"}
                                  {qSet.difficulty ? ` · Difficulty ${qSet.difficulty}/5` : ""}
                                </span>
                              </div>
                              <div style={{ display: "flex", gap: "6px" }}>
                                <button
                                  type="button"
                                  className="btn-primary"
                                  style={{ padding: "4px 12px", fontSize: "0.75rem", minHeight: "30px" }}
                                  onClick={() => {
                                    if (startAiCustomQuiz) {
                                      startAiCustomQuiz(qSet.quiz_set_id);
                                    }
                                  }}
                                >
                                  Practice
                                </button>
                                {isAdmin ? (
                                  <button
                                    type="button"
                                    className="chat-delete-btn"
                                    style={{ position: "static", transform: "none", opacity: 1, display: "inline-flex", alignItems: "center", justifyContent: "center", width: "28px", height: "28px", borderRadius: "6px", color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer", alignSelf: "center" }}
                                    onClick={(e) => handleDeleteQuizSet(qSet.quiz_set_id, qSet.quiz_set_title, e)}
                                    title="Delete set (for everyone)"
                                    aria-label="Delete custom set"
                                  >
                                    <Trash2 size={12} />
                                  </button>
                                ) : null}
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  )}
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
        )}

        {/* Step 2: Rules */}
        {quizConfigStep === 2 && (
          <div key="step-2" className="step-transition-wrapper quiz-config-form-col">
            <div>
              <h3 className="practice-title" style={{ fontSize: "1.15rem", fontWeight: 600 }}>Set Practice Rules</h3>
              <p className="practice-subtitle" style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: "4px" }}>
                Customize count, timer countdowns, feedback style, and content filtering.
              </p>
            </div>

            {/* Question count */}
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
              <label style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Number of Questions
              </label>
              <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginTop: "4px" }}>
                <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
                  {[5, 10, 20, 50].map((num) => (
                    <button
                      key={num}
                      type="button"
                      style={{
                        flex: 1, padding: "10px", borderRadius: "var(--r-md)", border: "1px solid",
                        borderColor: quizConfigNumQuestions === num ? "var(--teal)" : "var(--border-light)",
                        background: quizConfigNumQuestions === num ? "rgba(48, 197, 255, 0.08)" : "var(--surface-3)",
                        color: quizConfigNumQuestions === num ? "var(--teal)" : "var(--text-secondary)",
                        cursor: "pointer", fontWeight: 600, fontSize: "0.85rem", transition: "all var(--dur-fast)"
                      }}
                      onClick={() => setQuizConfigNumQuestions(Math.min(num, activeSubMCQs || 10))}
                    >
                      {num}
                    </button>
                  ))}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px", background: "var(--surface-3)", padding: "16px", borderRadius: "var(--r-md)", border: "1px solid var(--border-light)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                    <span>Selected Count: <strong style={{ color: "var(--sky)", fontSize: "0.9rem" }}>{quizConfigNumQuestions}</strong></span>
                    <span>Max Pool: <strong>{activeSubMCQs}</strong></span>
                  </div>
                  <Slider
                    min={1}
                    max={activeSubMCQs || 10}
                    value={[quizConfigNumQuestions]}
                    onValueChange={(val) => {
                      if (Array.isArray(val)) setQuizConfigNumQuestions(val[0]);
                      else if (typeof val === "number") setQuizConfigNumQuestions(val);
                    }}
                    style={{ marginTop: "6px" }}
                  />
                </div>
              </div>
            </div>

            {/* Difficulty: as written, or AI-hardened versions prepared before the session */}
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
              <label style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Difficulty
              </label>
              <div style={{ display: "flex", gap: "8px" }}>
                {([{ level: 0, label: "As written" }, ...(twistsAllowed ? [{ level: -1, label: "New angle (twists)" }] : []),
                   { level: 4, label: "Harder · 4/5 (AI)" }, { level: 5, label: "Brutal · 5/5 (AI)" }] as { level: number; label: string }[]).map((d) => {
                  const off = d.level > 0 && !!hardenBlocked;
                  const on = d.level === -1 ? newAngle : !newAngle && hardenLevel === d.level;
                  return (
                    <button
                      key={d.level}
                      type="button"
                      disabled={off}
                      title={off ? hardenBlocked ?? "" : d.level === -1 ? "Twists already written from these past-paper questions: same concept, a different ask (never the original answer)" : d.level ? "The AI rewrites the questions with harder statements and options (same fact, same answer) before the session starts" : "The questions exactly as in the bank"}
                      style={{
                        flex: 1, padding: "10px", borderRadius: "var(--r-md)", border: "1px solid",
                        borderColor: on ? "var(--teal)" : "var(--border-light)",
                        background: on ? "rgba(48, 197, 255, 0.08)" : "var(--surface-3)",
                        color: on ? "var(--teal)" : "var(--text-secondary)",
                        cursor: off ? "not-allowed" : "pointer", opacity: off ? 0.5 : 1, fontWeight: 600, fontSize: "0.8rem",
                      }}
                      onClick={() => {
                        if (d.level === -1) { setNewAngle(true); setHardenLevel(0); }
                        else { setNewAngle(false); setHardenLevel(d.level as HardenLevel); }
                      }}
                    >
                      {d.label}
                    </button>
                  );
                })}
              </div>
              {hardenBlocked ? (
                <span style={{ fontSize: "0.74rem", color: "var(--text-muted)" }}>{hardenBlocked}</span>
              ) : hardenLevel ? (
                <span style={{ fontSize: "0.74rem", color: "var(--text-secondary)" }}>
                  {hardenPreview ? `${previewLine(hardenPreview, quizConfigNumQuestions)}.` : "Working out which questions to rewrite…"}
                  {" "}They are prepared on the next step, before the session starts.
                </span>
              ) : null}
            </div>

            {/* Timer */}
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
              <label style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Timer Mode
              </label>
              <div style={{ display: "flex", gap: "8px" }}>
                {[{ mode: "none", label: "No Timer" }, { mode: "session", label: "Session Limit" }, { mode: "per_question", label: "Per-Question Limit" }].map((t) => (
                  <button
                    key={t.mode}
                    type="button"
                    style={{
                      flex: 1, padding: "10px", borderRadius: "var(--r-md)", border: "1px solid",
                      borderColor: quizConfigTimerMode === t.mode ? "var(--sky)" : "var(--border-light)",
                      background: quizConfigTimerMode === t.mode ? "rgba(48, 197, 255, 0.08)" : "var(--surface-3)",
                      color: quizConfigTimerMode === t.mode ? "var(--sky)" : "var(--text-secondary)",
                      cursor: "pointer", fontWeight: 600, fontSize: "0.8rem", transition: "all var(--dur-fast)"
                    }}
                    onClick={() => { setQuizConfigTimerMode(t.mode as any); setQuizConfigTimerValue(t.mode === "session" ? 30 : 60); }}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              {quizConfigTimerMode === "session" && (
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
                  <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>Session duration (minutes):</span>
                  <input type="number" min={1} value={quizConfigTimerValue} onChange={(e) => setQuizConfigTimerValue(parseInt(e.target.value) || 30)}
                    style={{ width: "80px", background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)", padding: "8px 12px", borderRadius: "var(--r-md)", fontSize: "0.85rem", outline: "none" }} />
                </div>
              )}
              {quizConfigTimerMode === "per_question" && (
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
                  <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>Seconds per question:</span>
                  <input type="number" min={5} value={quizConfigTimerValue} onChange={(e) => setQuizConfigTimerValue(parseInt(e.target.value) || 60)}
                    style={{ width: "80px", background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)", padding: "8px 12px", borderRadius: "var(--r-md)", fontSize: "0.85rem", outline: "none" }} />
                </div>
              )}
            </div>

            {/* Feedback Mode */}
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
              <label style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Feedback Style
              </label>
              <div style={{ display: "flex", gap: "8px" }}>
                {[{ mode: "tutor", label: "Tutor Mode (Instant Explanations)" }, { mode: "board", label: "Board Exam Mode (No Explanations until end)" }].map((f) => (
                  <button
                    key={f.mode}
                    type="button"
                    style={{
                      flex: 1, padding: "10px", borderRadius: "var(--r-md)", border: "1px solid",
                      borderColor: quizConfigFeedbackMode === f.mode ? "var(--sky)" : "var(--border-light)",
                      background: quizConfigFeedbackMode === f.mode ? "rgba(48, 197, 255, 0.08)" : "var(--surface-3)",
                      color: quizConfigFeedbackMode === f.mode ? "var(--sky)" : "var(--text-secondary)",
                      cursor: "pointer", fontWeight: 600, fontSize: "0.8rem", transition: "all var(--dur-fast)"
                    }}
                    onClick={() => setQuizConfigFeedbackMode(f.mode as any)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Skip Mastered */}
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "16px", background: "var(--surface-3)", borderRadius: "var(--r-md)", border: "1px solid var(--border-light)" }}>
              <label htmlFor="excludeMastered" style={{ fontSize: "0.82rem", fontWeight: 500, color: "var(--text-secondary)", cursor: "pointer", flex: 1 }}>
                Skip questions I've already answered correctly in past sessions
              </label>
              <Switch id="excludeMastered" checked={quizConfigExcludeMastered} onCheckedChange={setQuizConfigExcludeMastered} />
            </div>

            <div style={{ display: "flex", justifyContent: "space-between", marginTop: "var(--sp-4)" }}>
              <button className="btn-workspace" onClick={handlePrevStep} style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                <ArrowLeft size={16} />
                <span>Back to Topics</span>
              </button>
              <button className="btn-primary" onClick={handleNextStep}>
                <span>Review Configuration</span>
                <ArrowRight size={16} style={{ marginLeft: "4px" }} />
              </button>
            </div>
          </div>
        )}

        {/* Step 3: Review & Launch */}
        {quizConfigStep === 3 && (
          <div key="step-3" className="step-transition-wrapper quiz-config-split-layout">
            <div className="quiz-config-form-col">
              <div>
                <h3 className="practice-title" style={{ fontSize: "1.15rem", fontWeight: 600 }}>Confirm Practice Exam</h3>
                <p className="practice-subtitle" style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: "4px" }}>
                  Review your setup parameters before initiating the session.
                </p>
              </div>

              <div className="diagnostics-details" style={{ borderTop: "none", paddingTop: 0 }}>
                {[
                  ["Active Subjects", quizConfigCategories.length === 0 ? "Mixed Practice (All)" : quizConfigCategories.join(", ")],
                  ["Subtopics Checked", quizConfigSubCategories.length === 0 ? "All Available Topics" : `${quizConfigSubCategories.length} Topics`],
                  ["Question Count", `${quizConfigNumQuestions} questions`],
                  ["Difficulty", hardenLevel ? `Harder versions (AI), ${hardenLevel}/5` : "As written"],
                  ["Timer Mode", quizConfigTimerMode === "none" ? "Un-timed (Stopwatch)" : quizConfigTimerMode === "session" ? `Session Countdown (${quizConfigTimerValue}m)` : `Per-Question Limit (${quizConfigTimerValue}s)`],
                  ["Feedback Style", quizConfigFeedbackMode === "tutor" ? "Tutor Mode (Instant Explanations)" : "Board Exam Mode (Delayed Feedback)"],
                  ["Skip Mastered Qs", quizConfigExcludeMastered ? "Enabled" : "Disabled"],
                ].map(([label, value]) => (
                  <div key={label} className="diagnostics-detail-row" style={{ padding: "10px 0", borderBottom: "1px solid var(--border-light)" }}>
                    <span className="diagnostics-detail-label">{label}</span>
                    <span className="diagnostics-detail-value" style={{ textAlign: "right", maxWidth: "250px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value}</span>
                  </div>
                ))}
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", marginTop: "var(--sp-4)" }}>
                <button className="btn-workspace" onClick={() => setQuizConfigStep(2)} style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                  <ArrowLeft size={16} />
                  <span>Back to Rules</span>
                </button>
                {hardenLevel ? null : <button className="btn-primary" disabled={quizIsLoading} onClick={newAngle ? startTwists : handleStartQuiz} style={{ padding: "10px 28px" }}>
                  {quizIsLoading ? (
                    <Loader2 size={16} className="spinner" style={{ animation: "spin 1s linear infinite" }} />
                  ) : (
                    <>
                      <span>Start Practice Exam</span>
                      <ArrowRight size={16} style={{ marginLeft: "4px" }} />
                    </>
                  )}
                </button>}
              </div>
              {hardenLevel && getHeaders ? (
                <div style={{ marginTop: "var(--sp-3)" }}>
                  <HardenPanel
                    getHeaders={getHeaders}
                    categories={quizConfigCategories}
                    subCategories={quizConfigSubCategories}
                    scope={scopeOn ? (practiceScope as unknown as Record<string, unknown>) : null}
                    numQuestions={quizConfigNumQuestions}
                    difficulty={hardenLevel}
                    onStart={startHardened}
                    onSaved={fetchQuizHistory}
                  />
                </div>
              ) : null}
            </div>

            {/* Diagnostics */}
            <div className="config-diagnostics-col">
              <div className="config-diagnostics-card">
                <div style={{ textAlign: "center" }}>
                  <h4 style={{ fontWeight: 600, fontSize: "0.9rem", color: "var(--text-primary)" }}>Clinical Ingestion Status</h4>
                  <p style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "2px" }}>Diagnostics for selected config</p>
                </div>
                <div className="diagnostics-gauge-container">
                  <svg className="diagnostics-gauge-svg" width="120" height="120" viewBox="0 0 120 120">
                    <circle className="diagnostics-gauge-bg" cx="60" cy="60" r="50" />
                    <circle className="diagnostics-gauge-fill" cx="60" cy="60" r="50" strokeDasharray="314.16" strokeDashoffset={strokeDashoffset} />
                  </svg>
                  <div className="diagnostics-gauge-text">
                    <span className="diagnostics-gauge-val">{coveragePercent}%</span>
                    <span className="diagnostics-gauge-lbl">Coverage</span>
                  </div>
                </div>
                <div className="diagnostics-details">
                  {[
                    ["Config Scope", quizConfigCategories.length === 0 ? "Full Library" : "Category"],
                    ["Subtopics Active", `${subCategoryOptions.length > 0 ? (quizConfigSubCategories.length === 0 ? subCategoryOptions.length : quizConfigSubCategories.length) : 0} Topics`],
                    ["Pool Question Count", `${activeSubMCQs} MCQs`],
                    ["Est. Session Duration", `${Math.round(quizConfigNumQuestions * 1.5)} mins`],
                  ].map(([label, value]) => (
                    <div key={label} className="diagnostics-detail-row">
                      <span className="diagnostics-detail-label">{label}</span>
                      <span className="diagnostics-detail-value" style={{ textTransform: "capitalize" }}>{value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ─── Stage 2: Active Taker ────────────────────────────────────────────────
  if (quizStep === "taker") {
    const currentMCQ = quizMCQs[quizCurrentIdx];
    if (!currentMCQ) return null;

    const isAnswered = quizSelectedAnswers[currentMCQ.id] !== undefined;
    const selectedChoice = quizSelectedAnswers[currentMCQ.id];
    const optionKeys = Object.keys(currentMCQ.options).sort();

    const currentCorrectCount = quizMCQs.slice(0, quizCurrentIdx + 1).reduce((acc, q) => {
      const choice = quizSelectedAnswers[q.id];
      if (choice === undefined) return acc;
      return acc + (choice === q.correct_option ? 1 : 0);
    }, 0);
    const answeredCount = Object.keys(quizSelectedAnswers).length;
    const liveAccuracy = answeredCount > 0 ? Math.round((currentCorrectCount / answeredCount) * 100) : 100;

    const handleQuitQuiz = () => {
      setShowQuitModal(true);
    };

    return (
      <div className="dashboard-view" role="region" aria-label="Practice Mode">
        <div className="dashboard-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div className="dashboard-eyebrow">
              <GraduationCap size={12} style={{ marginRight: 6 }} />
              Practice Session
            </div>
            <h1 className="dashboard-title">{currentMCQ.sub_category || currentMCQ.main_category || "Board Exam Practice"}</h1>
          </div>
          <button className="btn-workspace" style={{ borderColor: "#ef4444", color: "#ef4444" }} onClick={handleQuitQuiz}>
            Quit Session
          </button>
        </div>

        {/* Telemetry Bar */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: "var(--surface-2)", border: "1px solid var(--border-light)", borderRadius: "var(--r-lg)", padding: "var(--sp-3) var(--sp-4)", fontSize: "0.82rem", color: "var(--text-secondary)", marginBottom: "var(--sp-4)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            {quizConfigTimerMode === "none" ? (
              <><Clock size={14} style={{ color: "var(--teal)" }} /><span style={{ fontFamily: "var(--font-mono)", fontWeight: 500 }}>{formatTime(quizSecondsElapsed)}</span></>
            ) : quizConfigTimerMode === "session" ? (
              <><Clock size={14} style={{ color: "var(--sky)" }} /><span style={{ fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--sky)" }}>Time Remaining: {formatTime(quizTimerCountdown)}</span></>
            ) : (
              <><Clock size={14} style={{ color: "var(--teal)" }} /><span style={{ fontFamily: "var(--font-mono)", fontWeight: 500 }}>Question Timer: {quizTimerCountdown}s</span></>
            )}
          </div>
          <div style={{ display: "flex", gap: "var(--sp-4)", alignItems: "center" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <svg width="18" height="18" viewBox="0 0 20 20" style={{ transform: "rotate(-90deg)" }}>
                <circle cx="10" cy="10" r="8" fill="transparent" stroke="var(--border)" strokeWidth="2" />
                <circle cx="10" cy="10" r="8" fill="transparent" stroke="var(--teal)" strokeWidth="2"
                  strokeDasharray={2 * Math.PI * 8}
                  strokeDashoffset={2 * Math.PI * 8 * (1 - (quizCurrentIdx + 1) / quizMCQs.length)}
                  style={{ transition: "stroke-dashoffset 0.3s ease" }} />
              </svg>
              <span>Progress: <strong>{quizCurrentIdx + 1} / {quizMCQs.length}</strong></span>
            </div>
            {quizConfigFeedbackMode !== "board" && (
              <span>Accuracy: <strong style={{ color: "var(--teal)" }}>{liveAccuracy}%</strong></span>
            )}
          </div>
        </div>

        {/* Progress Bar */}
        <div style={{ width: "100%", height: "4px", background: "var(--border)", borderRadius: "2px", overflow: "hidden", marginBottom: "var(--sp-6)" }}>
          <div style={{ width: `${((quizCurrentIdx + 1) / quizMCQs.length) * 100}%`, height: "100%", background: "var(--teal)", transition: "width 0.4s var(--ease-out-expo)" }} />
        </div>

        {/* Split Layout */}
        <div className={`quiz-split-layout ${explanationMCQId !== null ? "has-explanation" : ""}`}>
          <div className="quiz-question-col">
            <div style={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--r-xl)", padding: "var(--sp-6)", display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--border-light)", paddingBottom: "12px" }}>
                <span style={{ display: "inline-flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.72rem", color: "var(--sky)", background: "var(--sky-dim)", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 }}>
                    {currentMCQ.sub_category || currentMCQ.main_category || "Board MCQ"}
                  </span>
                  <PaperYears years={currentMCQ.paper_years} />
                  <ArchiveBadges mcq={currentMCQ} />
                </span>
                <button type="button" className="chat-delete-btn"
                  style={{ position: "static", opacity: 1, color: bookmarkedMcqs.some(b => b.id === currentMCQ.id) ? "var(--teal)" : "var(--text-muted)", background: "none", border: "none", cursor: "pointer" }}
                  onClick={() => toggleBookmarkMCQ(currentMCQ.id)} title="Bookmark Question">
                  <Bookmark size={14} fill={bookmarkedMcqs.some(b => b.id === currentMCQ.id) ? "currentColor" : "none"} />
                </button>
              </div>

              <p style={{ fontSize: "1.05rem", fontWeight: 500, lineHeight: 1.6, color: "var(--text-primary)", whiteSpace: "pre-line" }}>
                {currentMCQ.question_text}
              </p>
              <QuestionMedia ids={currentMCQ.media} token={token} />

              <div className="quiz-options-list" role="radiogroup">
                {optionKeys.map((key, index) => {
                  const isCorrect = key === currentMCQ.correct_option;
                  const isSelected = key === selectedChoice;
                  let optClass = "option-button option-cascade-item";
                  if (isAnswered) {
                    if (quizConfigFeedbackMode === "board") {
                      if (isSelected) optClass += " selected-board-mode";
                    } else {
                      if (isCorrect) {
                        optClass += " correct";
                        if (lastSelectedChoice === key && isCorrectSelection) optClass += " pop-correct";
                      } else if (isSelected) {
                        optClass += " selected-wrong";
                        if (lastSelectedChoice === key && !isCorrectSelection) optClass += " shake-incorrect";
                      }
                    }
                  }
                  return (
                    <button key={`${quizCurrentIdx}-${key}`} className={optClass} role="radio" aria-checked={isSelected} disabled={isAnswered}
                      onClick={() => handleSelectOption(key)}
                      style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", textAlign: "left", animationDelay: `${index * 50}ms` }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "12px", flex: 1 }}>
                        <span className="option-badge">{key}</span>
                        <span style={{ lineHeight: 1.4, color: "var(--text-primary)" }}>{currentMCQ.options[key]}</span>
                      </div>
                      {isAnswered && isCorrect && quizConfigFeedbackMode !== "board" && <Check size={14} style={{ color: "var(--success)", flexShrink: 0, marginLeft: "8px" }} />}
                      {isAnswered && isSelected && !isCorrect && quizConfigFeedbackMode !== "board" && <X size={14} style={{ color: "var(--error)", flexShrink: 0, marginLeft: "8px" }} />}
                    </button>
                  );
                })}
              </div>

              {/* Confidence tap: a correct guess is still re-tested by the retention engine */}
              {setQuizConfidence ? (
                <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "var(--sp-3)", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>How sure are you?</span>
                  {(["sure", "unsure", "guess"] as const).map((c) => {
                    const active = (quizConfidence[currentMCQ.id] ?? "sure") === c;
                    return (
                      <button
                        key={c}
                        type="button"
                        onClick={() => setQuizConfidence((prev) => ({ ...prev, [currentMCQ.id]: c }))}
                        title={c === "guess" ? "Guessed answers come back for review even if correct" : undefined}
                        style={{
                          padding: "2px 10px", borderRadius: "999px", fontSize: "0.72rem", cursor: "pointer",
                          border: `1px solid ${active ? "var(--sky)" : "var(--border-light)"}`,
                          background: active ? "rgba(48,197,255,0.12)" : "transparent",
                          color: active ? "var(--sky)" : "var(--text-secondary)",
                        }}
                      >
                        {c === "sure" ? "Sure" : c === "unsure" ? "Unsure" : "Guess"}
                      </button>
                    );
                  })}
                </div>
              ) : null}

              {/* Past-paper question: other archives' versions, textbook check when keys disagree, Twists */}
              {isAnswered && quizConfigFeedbackMode !== "board" ? (
                <PastPaperExtras key={currentMCQ.id} mcq={currentMCQ} token={token} onFigureClick={onFigureClick} />
              ) : null}

              {/* Action Bar */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "var(--sp-4)", borderTop: "1px solid var(--border-light)", paddingTop: "var(--sp-4)" }}>
                <div>
                  {isAnswered && quizConfigFeedbackMode !== "board" && (
                    <button className="btn-workspace" style={{ borderColor: "var(--teal)", color: "var(--teal)", display: "flex", alignItems: "center", gap: "6px" }} onClick={() => fetchExplanation(currentMCQ.id)}>
                      <GraduationCap size={14} />
                      Explain Question
                    </button>
                  )}
                </div>
                <div>
                  {isAnswered && (
                    quizCurrentIdx < quizMCQs.length - 1 ? (
                      <button className="btn-primary" onClick={() => { setQuizCurrentIdx((prev) => prev + 1); setExplanationMCQId(null); setLastSelectedChoice(null); setIsCorrectSelection(null); if (quizConfigTimerMode === "per_question") setQuizTimerCountdown(quizConfigTimerValue); }} style={{ padding: "8px 24px" }}>
                        Next Question
                      </button>
                    ) : (
                      <button className="btn-primary" disabled={quizIsSubmitting} onClick={handleSubmitQuiz} style={{ padding: "8px 24px", display: "flex", alignItems: "center", gap: "6px" }}>
                        {quizIsSubmitting ? <Loader2 size={14} className="spinner" style={{ animation: "spin 1s linear infinite" }} /> : "Finish Practice"}
                      </button>
                    )
                  )}
                </div>
              </div>
            </div>
          </div>

          <ExplanationPanel forMCQId={currentMCQ?.id} explanationMCQId={explanationMCQId} setExplanationMCQId={setExplanationMCQId} explanationLoading={explanationLoading} explanationError={explanationError} explanationData={explanationData} token={token} onFigureClick={onFigureClick} />
        </div>

        {/* Custom Quit Confirmation Modal */}
        <AnimatePresence>
          {showQuitModal && (
            <div className="modal-backdrop" onClick={() => setShowQuitModal(false)}>
              <motion.div
                className="modal-panel"
                style={{ maxWidth: "440px", padding: "24px", borderRadius: "16px" }}
                initial={{ opacity: 0, scale: 0.95, y: 10 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95, y: 10 }}
                transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
                onClick={(e) => e.stopPropagation()}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "14px" }}>
                  <div style={{ width: "40px", height: "40px", borderRadius: "12px", background: "rgba(239, 68, 68, 0.12)", border: "1px solid rgba(239, 68, 68, 0.3)", display: "flex", alignItems: "center", justifyContent: "center", color: "#ef4444", flexShrink: 0 }}>
                    <AlertTriangle size={20} />
                  </div>
                  <div>
                    <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "var(--text-primary)" }}>Quit Practice Session?</h3>
                    <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "2px" }}>Confirmation required</p>
                  </div>
                </div>

                <p style={{ fontSize: "0.86rem", color: "var(--text-secondary)", lineHeight: "1.5", marginBottom: "20px" }}>
                  Are you sure you want to exit? Your current progress and unanswered questions in this practice exam will be lost.
                </p>

                <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
                  <button
                    type="button"
                    className="btn-workspace"
                    onClick={() => setShowQuitModal(false)}
                    style={{ padding: "8px 16px", fontSize: "0.84rem" }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setShowQuitModal(false);
                      setQuizStep("config");
                      setActiveView("dashboard");
                      toast.info("Practice Session Exited", {
                        description: "Your session was ended and progress reset.",
                      });
                    }}
                    style={{
                      padding: "8px 18px",
                      fontSize: "0.84rem",
                      fontWeight: 600,
                      background: "#dc2626",
                      color: "#ffffff",
                      border: "none",
                      borderRadius: "var(--r-md)",
                      cursor: "pointer",
                      boxShadow: "0 4px 12px rgba(220, 38, 38, 0.3)",
                      transition: "transform 0.15s ease",
                    }}
                  >
                    Yes, Exit Session
                  </button>
                </div>
              </motion.div>
            </div>
          )}
        </AnimatePresence>
      </div>
    );
  }

  // ─── Stage 3: Summary ─────────────────────────────────────────────────────
  if (quizStep === "summary") {
    const correctCount = quizMCQs.reduce((acc, q) => {
      const choice = quizSelectedAnswers[q.id];
      return acc + (choice === q.correct_option ? 1 : 0);
    }, 0);
    const totalCount = quizMCQs.length;
    const finalAccuracy = Math.round((correctCount / totalCount) * 100);
    const reviewIdx = summaryReviewIdx !== null ? summaryReviewIdx : 0;
    const reviewMCQ = quizMCQs[reviewIdx];

    return (
      <div className="dashboard-view" role="region" aria-label="Practice Results">
        <div className="dashboard-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div className="dashboard-eyebrow"><GraduationCap size={12} style={{ marginRight: 6 }} />Practice Scoreboard</div>
            <h1 className="dashboard-title">Practice Results Summary</h1>
          </div>
          <button className="btn-workspace" onClick={() => setActiveView("dashboard")}>Return to Dashboard</button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-6)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "var(--sp-4)" }}>
            <div className="stat-card" style={{ textAlign: "center", display: "flex", flexDirection: "column", justifyContent: "center", padding: "var(--sp-5)" }}>
              <div className="score-badge-circle" style={{ borderColor: finalAccuracy >= 70 ? "var(--sea-green)" : "var(--teal)" }}>
                <span style={{ fontSize: "1.75rem", fontWeight: 700, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>{finalAccuracy}%</span>
                <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginTop: 2 }}>Accuracy</span>
              </div>
            </div>
            <div className="stat-card" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
              <span className="stat-label">Score Metrics</span>
              <span className="stat-value" style={{ fontSize: "2rem" }}>
                {correctCount} <span style={{ fontSize: "1rem", color: "var(--text-muted)", fontWeight: 400 }}>/ {totalCount} Correct</span>
              </span>
              <span style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>Completed in {formatTime(quizSecondsElapsed)}</span>
            </div>
            <div className="stat-card" style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: "var(--sp-3)" }}>
              {lastRun && startQuizWith ? (
                <>
                  {/* A run through a whole selection (past papers, "Do all"): keep going batch by batch. */}
                  {(() => {
                    const left = Math.max(0, lastRun.unseen - Math.min(lastRun.batch, lastRun.unseen));
                    const size = Number(lastRun.filters.num_questions) || lastRun.batch || 50;
                    return left > 0 ? (
                      <button className="btn-primary" style={{ width: "100%" }}
                        onClick={() => startQuizWith({ ...lastRun.filters, drill_wrong: false }, lastRun.label, lastRun.returnTo)}>
                        Continue: next {Math.min(size, left)} ({left.toLocaleString()} unanswered left of {lastRun.total.toLocaleString()})
                      </button>
                    ) : (
                      <div style={{ fontSize: "0.82rem", color: "var(--sea-green)", fontWeight: 600, textAlign: "center" }}>
                        You have answered every question in this selection ({lastRun.total.toLocaleString()}).
                      </div>
                    );
                  })()}
                  {correctCount < totalCount ? (
                    <button className="btn-workspace" style={{ width: "100%" }}
                      onClick={() => startQuizWith({ ...lastRun.filters, drill_wrong: true }, `${lastRun.label} · missed`, lastRun.returnTo)}>
                      Practise my missed questions
                    </button>
                  ) : null}
                  {lastRun.returnTo ? (
                    <button className="btn-workspace" onClick={() => setActiveView(lastRun.returnTo!)} style={{ width: "100%" }}>
                      Back to {lastRun.returnTo === "pastpapers" ? "past papers" : "dashboard"}
                    </button>
                  ) : null}
                </>
              ) : (
                <>
                  <button className="btn-primary" onClick={() => setQuizStep("config")} style={{ width: "100%" }}>Start New Session</button>
                  <button className="btn-workspace" onClick={() => setActiveView("dashboard")} style={{ width: "100%" }}>Back to Dashboard</button>
                </>
              )}
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: "var(--sp-5)", marginTop: "var(--sp-2)" }}>
            <div style={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", padding: "var(--sp-5)" }}>
              <h3 className="practice-title" style={{ fontSize: "0.95rem", fontWeight: 600, marginBottom: "var(--sp-2)" }}>Clinical Review Grid</h3>
              <p className="practice-subtitle" style={{ marginBottom: "var(--sp-4)" }}>Click a question number to review your choices and load citations</p>
              <div className="review-grid" role="list">
                {quizMCQs.map((q, idx) => {
                  const ans = quizSelectedAnswers[q.id];
                  const isRight = ans === q.correct_option;
                  const isActive = idx === reviewIdx;
                  return (
                    <button key={idx} className={`review-circle-btn ${isRight ? "correct" : "incorrect"} ${isActive ? "active" : ""}`} role="listitem" onClick={() => setSummaryReviewIdx(idx)}>
                      {idx + 1}
                    </button>
                  );
                })}
              </div>
            </div>

            {reviewMCQ && (
              <div className={`quiz-split-layout ${explanationMCQId !== null ? "has-explanation" : ""}`}>
                <div className="quiz-question-col">
                  <div style={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--r-xl)", padding: "var(--sp-6)", display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--border-light)", paddingBottom: "12px", marginBottom: "12px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <span style={{ fontSize: "0.72rem", color: "var(--sky)", background: "var(--sky-dim)", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 }}>
                          {reviewMCQ.sub_category || reviewMCQ.main_category || "Board MCQ"}
                        </span>
                        <span style={{ fontSize: "0.72rem", fontWeight: 600, color: quizSelectedAnswers[reviewMCQ.id] === reviewMCQ.correct_option ? "var(--success)" : "#ef4444", background: quizSelectedAnswers[reviewMCQ.id] === reviewMCQ.correct_option ? "rgba(76, 217, 100, 0.1)" : "rgba(239, 68, 68, 0.1)", padding: "2px 8px", borderRadius: "10px" }}>
                          {quizSelectedAnswers[reviewMCQ.id] === reviewMCQ.correct_option ? "Correct" : "Incorrect"}
                        </span>
                      </div>
                      <button type="button" className="chat-delete-btn"
                        style={{ position: "static", opacity: 1, color: bookmarkedMcqs.some(b => b.id === reviewMCQ.id) ? "var(--teal)" : "var(--text-muted)", background: "none", border: "none", cursor: "pointer" }}
                        onClick={() => toggleBookmarkMCQ(reviewMCQ.id)} title="Bookmark Question">
                        <Bookmark size={14} fill={bookmarkedMcqs.some(b => b.id === reviewMCQ.id) ? "currentColor" : "none"} />
                      </button>
                    </div>

                    <p style={{ fontSize: "1rem", lineHeight: 1.6, color: "var(--text-primary)", fontWeight: 500 }}>{reviewMCQ.question_text}</p>

                    <div className="quiz-options-list">
                      {Object.keys(reviewMCQ.options).sort().map((key) => {
                        const isCorrect = key === reviewMCQ.correct_option;
                        const isSelected = key === quizSelectedAnswers[reviewMCQ.id];
                        let optClass = "option-button";
                        if (isCorrect) optClass += " correct";
                        else if (isSelected) optClass += " selected-wrong";
                        return (
                          <button key={key} className={optClass} disabled={true} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", textAlign: "left" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: "12px", flex: 1 }}>
                              <span className="option-badge">{key}</span>
                              <span style={{ lineHeight: 1.4 }}>{reviewMCQ.options[key]}</span>
                            </div>
                            {isCorrect && <Check size={14} style={{ color: "var(--success)", flexShrink: 0, marginLeft: "8px" }} />}
                            {isSelected && !isCorrect && <X size={14} style={{ color: "var(--error)", flexShrink: 0, marginLeft: "8px" }} />}
                          </button>
                        );
                      })}
                    </div>

                    <div style={{ display: "flex", justifyContent: "flex-start", borderTop: "1px solid var(--border-light)", paddingTop: "var(--sp-4)", marginTop: "var(--sp-2)" }}>
                      <button className="btn-workspace" style={{ borderColor: "var(--teal)", color: "var(--teal)", display: "flex", alignItems: "center", gap: "6px" }} onClick={() => fetchExplanation(reviewMCQ.id)}>
                        <GraduationCap size={14} />
                        Clinical Explanation
                      </button>
                      {quizSelectedAnswers[reviewMCQ.id] !== reviewMCQ.correct_option ? (
                        <button
                          className="btn-workspace"
                          style={{ marginLeft: "8px", display: "flex", alignItems: "center", gap: "6px" }}
                          disabled={flashcardSavedIds.includes(reviewMCQ.id)}
                          onClick={() => handleMakeFlashcard(reviewMCQ)}
                          title="Save this missed question to Study Corner flashcards (spaced repetition)"
                        >
                          <Layers size={14} />
                          {flashcardSavedIds.includes(reviewMCQ.id) ? "Flashcard saved" : "Make flashcard"}
                        </button>
                      ) : null}
                    </div>
                  </div>
                </div>
                <ExplanationPanel forMCQId={reviewMCQ?.id} explanationMCQId={explanationMCQId} setExplanationMCQId={setExplanationMCQId} explanationLoading={explanationLoading} explanationError={explanationError} explanationData={explanationData} token={token} onFigureClick={onFigureClick} />
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return null;
}
