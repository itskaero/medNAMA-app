"use client";

import { useState, useEffect, useRef } from "react";
import { toast } from "sonner";
import { AnswerResponse } from "@/types";
import { API } from "@/lib/constants";
import { formatTime } from "@/utils/quizHelpers";

interface UseQuizParams {
  token: string | null;
  getHeaders: () => HeadersInit;
  activeView: string;
  setActiveView: (view: any) => void;
  selectedTopic: any;
  stats: any;
  fetchStats: () => Promise<void>;
}

export function useQuiz({
  token,
  getHeaders,
  activeView,
  setActiveView,
  selectedTopic,
  stats,
  fetchStats,
}: UseQuizParams) {
  // Core quiz flow state
  const [quizStep, setQuizStep] = useState<"config" | "taker" | "summary">("config");
  const [quizMCQs, setQuizMCQs] = useState<any[]>([]);
  const [quizCurrentIdx, setQuizCurrentIdx] = useState(0);
  const [quizSelectedAnswers, setQuizSelectedAnswers] = useState<{ [key: number]: string }>({});
  // Retention engine: how sure the student was per question (default "sure").
  const [quizConfidence, setQuizConfidence] = useState<{ [key: number]: "sure" | "unsure" | "guess" }>({});
  const [quizAttemptId, setQuizAttemptId] = useState<number | null>(null);
  const [quizIsLoading, setQuizIsLoading] = useState(false);
  const [quizIsSubmitting, setQuizIsSubmitting] = useState(false);

  // Configuration state
  const [quizConfigCategories, setQuizConfigCategories] = useState<string[]>([]);
  const [quizConfigSubCategories, setQuizConfigSubCategories] = useState<string[]>([]);
  const [quizConfigNumQuestions, setQuizConfigNumQuestions] = useState<number>(10);
  const [quizConfigTimerMode, setQuizConfigTimerMode] = useState<"none" | "session" | "per_question">("none");
  const [quizConfigTimerValue, setQuizConfigTimerValue] = useState<number>(30);
  const [quizConfigExcludeMastered, setQuizConfigExcludeMastered] = useState<boolean>(false);
  const [quizConfigFeedbackMode, setQuizConfigFeedbackMode] = useState<"tutor" | "board">("tutor");
  const [quizConfigStep, setQuizConfigStep] = useState<1 | 2 | 3>(1);
  const [quizTimerCountdown, setQuizTimerCountdown] = useState<number>(0);

  // Summary and timer state
  const [summaryReviewIdx, setSummaryReviewIdx] = useState<number | null>(null);
  const [quizSecondsElapsed, setQuizSecondsElapsed] = useState(0);
  const [quizTimerActive, setQuizTimerActive] = useState(false);

  // UX animation states
  const [lastSelectedChoice, setLastSelectedChoice] = useState<string | null>(null);
  const [isCorrectSelection, setIsCorrectSelection] = useState<boolean | null>(null);

  // Explanation side panel state
  const [explanationMCQId, setExplanationMCQId] = useState<number | null>(null);
  const [explanationData, setExplanationData] = useState<AnswerResponse | null>(null);
  const [explanationLoading, setExplanationLoading] = useState(false);
  const [explanationError, setExplanationError] = useState<string | null>(null);

  const isReviewNavigation = useRef(false);

  // Reset quiz state on entering quiz view
  useEffect(() => {
    if (activeView === "quiz") {
      if (isReviewNavigation.current) {
        isReviewNavigation.current = false;
        return;
      }
      setQuizStep("config");
      setQuizSelectedAnswers({});
      setQuizConfidence({});
      setQuizCurrentIdx(0);
      setQuizSecondsElapsed(0);
      setQuizTimerActive(false);
      setExplanationMCQId(null);
      setExplanationData(null);
      setSummaryReviewIdx(null);

      if (selectedTopic) {
        setQuizConfigCategories([selectedTopic.main_category]);
        setQuizConfigSubCategories([selectedTopic.name]);
      } else {
        setQuizConfigCategories([]);
        setQuizConfigSubCategories([]);
      }
      setQuizConfigStep(1);
    }
  }, [activeView, selectedTopic]);

  // Stopwatch interval
  useEffect(() => {
    let interval: any = null;
    if (quizTimerActive) {
      interval = setInterval(() => {
        setQuizSecondsElapsed((s) => s + 1);
      }, 1000);
    } else {
      clearInterval(interval);
    }
    return () => clearInterval(interval);
  }, [quizTimerActive]);

  // On-demand explanation fetcher
  const fetchExplanation = async (mcqId: number) => {
    setExplanationMCQId(mcqId);
    setExplanationLoading(true);
    setExplanationError(null);
    setExplanationData(null);
    try {
      const res = await fetch(`${API}/api/mcqs/${mcqId}/explain`, {
        method: "POST",
        headers: getHeaders(),
        credentials: "include",
      });
      if (!res.ok) {
        throw new Error("Failed to generate clinical explanation from textbook library.");
      }
      const data = await res.json();
      setExplanationData({
        answer_markdown: data.answer_markdown,
        citations: data.citations || [],
        figures: data.figures || [],
      });
    } catch (err: any) {
      setExplanationError(err.message || "Failed to load explanation.");
    } finally {
      setExplanationLoading(false);
    }
  };

  // Submit and score quiz
  const handleSubmitQuiz = async () => {
    if (!quizAttemptId) return;
    setQuizIsSubmitting(true);
    setQuizTimerActive(false);

    const formattedAnswers = Object.entries(quizSelectedAnswers).map(([mcqId, option]) => ({
      mcq_id: parseInt(mcqId),
      selected_option: option,
      confidence: quizConfidence[parseInt(mcqId)] ?? "sure",
    }));

    try {
      const res = await fetch(`${API}/api/quizzes/${quizAttemptId}/submit`, {
        method: "POST",
        headers: {
          ...getHeaders(),
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({ answers: formattedAnswers }),
      });
      if (!res.ok) {
        throw new Error("Failed to finalize results on server.");
      }
      setQuizStep("summary");
      setSummaryReviewIdx(0);
      fetchStats();
      toast.success("Practice Session Completed!", {
        description: "Your performance score and explanations are ready for review.",
      });
    } catch (err: any) {
      toast.error(err.message || "Failed to submit answers.", { duration: Infinity });
      setQuizTimerActive(true);
    } finally {
      setQuizIsSubmitting(false);
    }
  };

  // Countdown timer interval
  useEffect(() => {
    let interval: any = null;
    if (quizTimerActive && quizConfigTimerMode !== "none") {
      interval = setInterval(() => {
        setQuizTimerCountdown((prev) => {
          if (prev <= 1) {
            clearInterval(interval);
            if (quizConfigTimerMode === "session") {
              toast.warning("Time is up! Your practice session is being submitted.");
              handleSubmitQuiz();
            } else if (quizConfigTimerMode === "per_question") {
              const nextIdx = quizCurrentIdx + 1;
              if (nextIdx < quizMCQs.length) {
                setQuizCurrentIdx(nextIdx);
                setExplanationMCQId(null);
                setLastSelectedChoice(null);
                setIsCorrectSelection(null);
                return quizConfigTimerValue;
              } else {
                toast.warning("Time is up for the final question! Submitting your answers.");
                handleSubmitQuiz();
              }
            }
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    } else {
      clearInterval(interval);
    }
    return () => clearInterval(interval);
  }, [quizTimerActive, quizConfigTimerMode, quizConfigTimerValue, quizCurrentIdx, quizMCQs.length]);

  // Lifted option selector supporting animations
  const handleSelectOption = (key: string) => {
    const currentMCQ = quizMCQs[quizCurrentIdx];
    if (!currentMCQ) return;
    const isAnswered = quizSelectedAnswers[currentMCQ.id] !== undefined;
    if (isAnswered) return;

    const isCorrect = key === currentMCQ.correct_option;
    setQuizSelectedAnswers((prev) => ({
      ...prev,
      [currentMCQ.id]: key,
    }));

    setLastSelectedChoice(key);
    setIsCorrectSelection(isCorrect);

    if (quizConfigFeedbackMode !== "board" && !isCorrect) {
      fetchExplanation(currentMCQ.id);
    }
  };

  // Launch quiz attempt
  const handleStartQuiz = async () => {
    setQuizIsLoading(true);
    try {
      const res = await fetch(`${API}/api/quizzes/start`, {
        method: "POST",
        headers: {
          ...getHeaders(),
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({
          categories: quizConfigCategories.length === 0 ? null : quizConfigCategories,
          sub_categories: quizConfigSubCategories.length === 0 ? null : quizConfigSubCategories,
          num_questions: quizConfigNumQuestions,
          exclude_mastered: quizConfigExcludeMastered,
          timer_mode: quizConfigTimerMode,
          timer_value: quizConfigTimerValue,
          feedback_mode: quizConfigFeedbackMode,
        }),
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.detail || "No questions found matching configuration filters.");
      }
      const data = await res.json();
      setQuizMCQs(data.mcqs);
      setQuizAttemptId(data.quiz_attempt_id);
      setQuizCurrentIdx(0);
      setQuizSelectedAnswers({});
      setQuizConfidence({});
      setQuizStep("taker");
      setQuizSecondsElapsed(0);

      if (data.timer_mode === "session") {
        setQuizTimerCountdown(data.timer_value * 60);
      } else if (data.timer_mode === "per_question") {
        setQuizTimerCountdown(data.timer_value);
      } else {
        setQuizTimerCountdown(0);
      }
      setQuizTimerActive(true);
      toast.success("Practice Exam Started", {
        description: `${data.mcqs.length} board-style MCQs loaded into session.`,
      });
    } catch (err: any) {
      toast.error(err.message || "Failed to generate quiz attempt.", { duration: Infinity });
    } finally {
      setQuizIsLoading(false);
    }
  };

  // Review a previous quiz attempt
  const handleReviewPreviousQuiz = async (attemptId: number) => {
    setQuizIsLoading(true);
    try {
      const res = await fetch(`${API}/api/quizzes/${attemptId}`, {
        method: "GET",
        headers: getHeaders(),
        credentials: "include",
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.detail || "Failed to load quiz details for review.");
      }
      const data = await res.json();
      setQuizMCQs(data.questions || []);
      setQuizSelectedAnswers(data.selected_answers || {});
      setQuizAttemptId(data.id);
      setQuizCurrentIdx(0);
      setSummaryReviewIdx(0);
      setQuizSecondsElapsed(0);
      setQuizTimerActive(false);
      setQuizStep("summary");
      isReviewNavigation.current = true;
      setActiveView("quiz");
    } catch (err: any) {
      toast.error(err.message || "Failed to load quiz attempt details.", { duration: Infinity });
    } finally {
      setQuizIsLoading(false);
    }
  };

  // Keyboard shortcut listener
  useEffect(() => {
    if (activeView !== "quiz" || quizStep !== "taker" || quizMCQs.length === 0) return;

    const currentMCQ = quizMCQs[quizCurrentIdx];
    if (!currentMCQ) return;

    const isAnswered = quizSelectedAnswers[currentMCQ.id] !== undefined;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (document.activeElement?.tagName === "INPUT" || document.activeElement?.tagName === "TEXTAREA") {
        return;
      }

      const key = e.key.toUpperCase();
      const optionKeys = Object.keys(currentMCQ.options).sort();

      if (!isAnswered) {
        if (["A", "B", "C", "D", "E"].includes(key)) {
          if (optionKeys.includes(key)) {
            handleSelectOption(key);
          }
        } else if (["1", "2", "3", "4", "5"].includes(key)) {
          const idx = parseInt(key) - 1;
          if (idx >= 0 && idx < optionKeys.length) {
            handleSelectOption(optionKeys[idx]);
          }
        }
      } else {
        if (e.key === "Enter" || e.key === "ArrowRight") {
          if (quizCurrentIdx < quizMCQs.length - 1) {
            setQuizCurrentIdx((prev) => prev + 1);
            setExplanationMCQId(null);
            setLastSelectedChoice(null);
            setIsCorrectSelection(null);
          } else if (!quizIsSubmitting) {
            handleSubmitQuiz();
          }
        }

        if (key === "E") {
          if (explanationMCQId === null) {
            fetchExplanation(currentMCQ.id);
          } else {
            setExplanationMCQId(null);
          }
        }
      }

      if (key === "Q") {
        const confirmQuit = window.confirm(
          "Are you sure you want to quit this practice session? Your progress will not be saved."
        );
        if (confirmQuit) {
          setActiveView("dashboard");
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [activeView, quizStep, quizCurrentIdx, quizMCQs, quizSelectedAnswers, quizIsSubmitting, explanationMCQId]);

  // Launch a practice session built from previously-missed questions
  const handleStartDrill = async () => {
    setQuizIsLoading(true);
    try {
      const res = await fetch(`${API}/api/quizzes/start`, {
        method: "POST",
        headers: {
          ...getHeaders(),
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({
          drill_wrong: true,
          num_questions: Math.min(quizConfigNumQuestions || 10, 50),
          exclude_mastered: false,
          timer_mode: quizConfigTimerMode,
          timer_value: quizConfigTimerValue,
          feedback_mode: quizConfigFeedbackMode,
        }),
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.detail || "No missed questions found.");
      }
      const data = await res.json();
      setQuizMCQs(data.mcqs);
      setQuizAttemptId(data.quiz_attempt_id);
      setQuizCurrentIdx(0);
      setQuizSelectedAnswers({});
      setQuizConfidence({});
      setQuizStep("taker");
      setQuizSecondsElapsed(0);
      if (data.timer_mode === "session") setQuizTimerCountdown(data.timer_value * 60);
      else if (data.timer_mode === "per_question") setQuizTimerCountdown(data.timer_value);
      else setQuizTimerCountdown(0);
      setQuizTimerActive(true);
      toast.success("Missed-Question Drill Started", {
        description: `${data.mcqs.length} previously-missed MCQs loaded.`,
      });
    } catch (err: any) {
      toast.error(err.message || "Failed to start drill session.", { duration: Infinity });
    } finally {
      setQuizIsLoading(false);
    }
  };

  // Launch custom AI-generated quiz set
  const startAiCustomQuiz = async (quizSetId: string) => {
    setQuizIsLoading(true);
    try {
      const res = await fetch(`${API}/api/quizzes/start`, {
        method: "POST",
        headers: {
          ...getHeaders(),
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({
          quiz_set_id: quizSetId,
          num_questions: 25,
          timer_mode: "none",
          timer_value: 0,
          exclude_mastered: false,
          feedback_mode: "tutor",
        }),
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.detail || "Failed to start custom quiz session.");
      }
      const data = await res.json();
      setQuizMCQs(data.mcqs);
      setQuizAttemptId(data.quiz_attempt_id);
      setQuizCurrentIdx(0);
      setQuizSelectedAnswers({});
      setQuizConfidence({});
      setQuizStep("taker");
      setQuizSecondsElapsed(0);
      setQuizTimerActive(true);
      setActiveView("quiz");
      toast.success("AI Practice Quiz Initialized", {
        description: `${data.mcqs.length} custom MCQs prepared for your practice session.`,
      });
    } catch (err: any) {
      toast.error(err.message || "Failed to start quiz.", { duration: Infinity });
    } finally {
      setQuizIsLoading(false);
    }
  };

  return {
    // core
    quizStep,
    setQuizStep,
    quizMCQs,
    setQuizMCQs,
    quizCurrentIdx,
    setQuizCurrentIdx,
    quizSelectedAnswers,
    setQuizSelectedAnswers,
    quizConfidence,
    setQuizConfidence,
    quizAttemptId,
    setQuizAttemptId,
    quizIsLoading,
    quizIsSubmitting,
    // config
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
    // timer / summary
    summaryReviewIdx,
    setSummaryReviewIdx,
    quizSecondsElapsed,
    quizTimerActive,
    setQuizTimerActive,
    // ux animation
    lastSelectedChoice,
    setLastSelectedChoice,
    isCorrectSelection,
    setIsCorrectSelection,
    // explanation
    explanationMCQId,
    setExplanationMCQId,
    explanationData,
    explanationLoading,
    explanationError,
    // handlers
    handleStartQuiz,
    handleStartDrill,
    startAiCustomQuiz,
    handleSubmitQuiz,
    handleSelectOption,
    handleReviewPreviousQuiz,
    fetchExplanation,
    formatTime,
  };
}
