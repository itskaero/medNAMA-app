"use client";

import React from "react";
import { ReportsPanel } from "@/components/ReportsPanel";
import { ReadinessCard } from "@/components/ReadinessCard";
import {
  BookOpen,
  Stethoscope,
  GraduationCap,
  MessageSquare,
  ChevronDown,
  ChevronUp,
  Loader2,
} from "lucide-react";

interface DashboardViewProps {
  username: string | null;
  stats: any;
  isLoadingStats: boolean;
  openAccordionCategory: string | null;
  setOpenAccordionCategory: (cat: string | null) => void;
  setActiveView: (view: any) => void;
  setSelectedTopic: (topic: any) => void;
  handleReviewPreviousQuiz: (attemptId: number) => void;
  /** Admins see the "Reported answers" review queue. */
  isAdmin?: boolean;
  token?: string | null;
}

export default function DashboardView({
  username,
  stats,
  isLoadingStats,
  openAccordionCategory,
  setOpenAccordionCategory,
  setActiveView,
  setSelectedTopic,
  handleReviewPreviousQuiz,
  isAdmin = false,
  token = null,
}: DashboardViewProps) {
  return (
    <div className="dashboard-view" role="region" aria-label="Dashboard metrics">
      <div className="dashboard-header">

        <h1 className="dashboard-title">
          Welcome back, <span>Dr. {username}</span>
        </h1>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", marginTop: 4 }}>
          Access textbook grounding and board exam practice modules.
        </p>
      </div>

      <ReadinessCard token={token} onOpenDailyDose={() => setActiveView("daily")} />

      {/* Bento Grid Stats */}
      <div className="dashboard-grid">
        <div className="stat-card">
          <div className="stat-icon">
            <BookOpen size={20} />
          </div>
          <div className="stat-details">
            <span className="stat-value">{stats ? stats.total_books : "..."}</span>
            <span className="stat-label">Textbooks</span>
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-icon teal">
            <GraduationCap size={20} />
          </div>
          <div className="stat-details">
            <span className="stat-value">{stats ? stats.total_mcqs.toLocaleString() : "..."}</span>
            <span className="stat-label">Board MCQs</span>
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-icon green">
            <Stethoscope size={20} />
          </div>
          <div className="stat-details">
            <span className="stat-value">{stats ? stats.average_score + "%" : "0%"}</span>
            <span className="stat-label">Avg Accuracy</span>
          </div>
        </div>
      </div>

      {/* Workspace Cards */}
      <div className="workspace-section">
        <div className="workspace-card">
          <h3 className="workspace-title">Textbook Q&A Engine</h3>
          <p className="workspace-desc">
            Ask complex clinical questions and retrieve grounded answers verified by cross-referencing all
            loaded textbooks. Features inline citations and image extraction.
          </p>
          <button className="btn-workspace" onClick={() => setActiveView("chat")}>
            Open Workspace
          </button>
        </div>

        <div className="workspace-card quiz-card">
          <h3 className="workspace-title">Practice Exam Center</h3>
          <p className="workspace-desc">
            Self-assess your clinical knowledge across our database of 12,000+ board exam questions. Access
            instant results and detailed on-demand RAG explanations.
          </p>
          <button
            className="btn-workspace"
            onClick={() => {
              setSelectedTopic(null);
              setActiveView("quiz");
            }}
          >
            Start Practice
          </button>
        </div>
      </div>

      {/* Recent Practice History */}
      {stats && stats.recent_attempts && stats.recent_attempts.length > 0 && (
        <div className="recent-attempts-container">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <h3 className="practice-title" style={{ fontSize: "1rem" }}>
                Recent Practice History
              </h3>
              <p className="practice-subtitle">Review your previous quiz submissions and RAG grounding</p>
            </div>
            <span className="topic-count" style={{ fontSize: "0.75rem" }}>
              {stats.recent_attempts.length} attempts logged
            </span>
          </div>

          <div className="attempts-list">
            {stats.recent_attempts.map((att: any, idx: number) => {
              const dateObj = new Date(att.completed_at || att.started_at);
              const dateStr = dateObj.toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              });

              const accuracy =
                att.total_questions > 0
                  ? Math.round((att.score / att.total_questions) * 100)
                  : 0;

              return (
                <div key={idx} className="attempt-row-card">
                  <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                    <span style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "0.9rem" }}>
                      {att.category}
                    </span>
                    <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{dateStr}</span>
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-4)" }}>
                    <div style={{ textAlign: "right" }}>
                      <span
                        style={{
                          display: "block",
                          fontSize: "0.95rem",
                          fontWeight: 700,
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-primary)",
                        }}
                      >
                        {att.score} / {att.total_questions}
                      </span>
                      <span
                        style={{
                          fontSize: "0.72rem",
                          fontWeight: 600,
                          color: accuracy >= 70 ? "var(--sea-green)" : "var(--teal)",
                        }}
                      >
                        {accuracy}% Accuracy
                      </span>
                    </div>

                    <button
                      className="btn-workspace"
                      style={{ padding: "6px 14px", fontSize: "0.78rem" }}
                      onClick={() => handleReviewPreviousQuiz(att.id)}
                    >
                      Review
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Topics Practice Explorer Accordion */}
      <div className="practice-section" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
        <div
          className="practice-header"
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
          <div>
            <h3 className="practice-title">Practice Explorer</h3>
            <p className="practice-subtitle">Select a subject category to launch a custom board practice quiz</p>
          </div>

          <button
            className="btn-workspace"
            style={{ padding: "6px 14px", fontSize: "0.78rem" }}
            onClick={() => {
              setOpenAccordionCategory(
                openAccordionCategory ? null : stats?.categories?.[0]?.main_category || null
              );
            }}
          >
            {openAccordionCategory ? "Collapse All" : "Quick Expand"}
          </button>
        </div>

        {isLoadingStats ? (
          <div style={{ textAlign: "center", padding: "40px", color: "var(--text-muted)" }}>
            <Loader2
              size={20}
              style={{ margin: "0 auto 8px", display: "inline-block", animation: "spin 1s linear infinite" }}
            />
            <span style={{ display: "block", fontSize: "0.8rem", marginTop: 8 }}>Loading categories...</span>
          </div>
        ) : !stats || !stats.categories || stats.categories.length === 0 ? (
          <div
            style={{ color: "var(--text-muted)", fontSize: "0.85rem", textAlign: "center", padding: "40px" }}
          >
            No practice categories found.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
            {stats.categories.map((cat: any, idx: number) => {
              const isOpen = openAccordionCategory === cat.main_category;
              const totalCategoryMCQs = cat.sub_categories.reduce(
                (sum: number, s: any) => sum + s.count,
                0
              );

              return (
                <div key={idx} style={{ display: "flex", flexDirection: "column" }}>
                  <button
                    className={`accordion-header ${isOpen ? "active" : ""}`}
                    onClick={() => setOpenAccordionCategory(isOpen ? null : cat.main_category)}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <span
                        style={{
                          fontSize: "0.88rem",
                          fontWeight: 600,
                          color: isOpen ? "var(--teal)" : "var(--text-primary)",
                        }}
                      >
                        {cat.main_category}
                      </span>
                      <span
                        className="topic-count"
                        style={{
                          background: "var(--surface-1)",
                          padding: "2px 8px",
                          borderRadius: "10px",
                          fontSize: "0.72rem",
                        }}
                      >
                        {totalCategoryMCQs} MCQs
                      </span>
                    </div>
                    {isOpen ? (
                      <ChevronUp size={16} style={{ color: "var(--teal)" }} />
                    ) : (
                      <ChevronDown size={16} style={{ color: "var(--text-muted)" }} />
                    )}
                  </button>

                  {isOpen && (
                    <div className="accordion-content">
                      <div className="topics-grid" role="list">
                        {cat.sub_categories.map((sub: any, subIdx: number) => (
                          <button
                            key={subIdx}
                            className="topic-item-card"
                            role="listitem"
                            onClick={() => {
                              setSelectedTopic({
                                name: sub.name,
                                count: sub.count,
                                main_category: cat.main_category,
                              });
                              setActiveView("quiz");
                            }}
                          >
                            <span className="topic-name" title={sub.name}>
                              {sub.name}
                            </span>
                            <span className="topic-count">{sub.count} MCQs</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {isAdmin ? <ReportsPanel token={token} /> : null}
    </div>
  );
}
