"use client";

import React from "react";
import { BookOpen, Bookmark, GraduationCap, Trash2, Check, Download } from "lucide-react";
import { AnswerResponse, Figure } from "@/types";
import { parseMarkdown } from "@/utils/markdown";
import ExplanationPanel from "@/components/ExplanationPanel";
import { API } from "@/lib/constants";
import { downloadAuthenticatedCSV } from "@/lib/downloadCSV";
import { toast } from "sonner";
import { CiteLink } from "@/components/CiteLink";

interface BookmarksViewProps {
  bookmarkedMcqs: any[];
  bookmarkedConcepts: any[];
  isLoadingBookmarks: boolean;
  bookmarksActiveTab: "mcq" | "concept";
  setBookmarksActiveTab: (tab: "mcq" | "concept") => void;
  toggleBookmarkMCQ: (mcqId: number) => void;
  fetchExplanation: (mcqId: number) => void;
  handleDeleteConceptBookmark: (id: number) => void;
  explanationMCQId: number | null;
  setExplanationMCQId: (id: number | null) => void;
  explanationData: AnswerResponse | null;
  explanationLoading: boolean;
  explanationError: string | null;
  token: string | null;
  onFigureClick: (fig: Figure) => void;
}

export default function BookmarksView({
  bookmarkedMcqs,
  bookmarkedConcepts,
  isLoadingBookmarks,
  bookmarksActiveTab,
  setBookmarksActiveTab,
  toggleBookmarkMCQ,
  fetchExplanation,
  handleDeleteConceptBookmark,
  explanationMCQId,
  setExplanationMCQId,
  explanationData,
  explanationLoading,
  explanationError,
  token,
  onFigureClick,
  onPractise,
}: BookmarksViewProps & { onPractise?: (filters: Record<string, unknown>, label: string) => void }) {
  return (
    <div className="dashboard-view" role="region" aria-label="Bookmarks">
      <div className="dashboard-header">
        <div className="dashboard-eyebrow">
          <Bookmark size={12} style={{ marginRight: 6 }} />
          Saved Study Materials
        </div>
        <h1 className="dashboard-title">Bookmarks</h1>
        <button
          className="btn-workspace"
          style={{ marginTop: "var(--sp-2)", display: "inline-flex", alignItems: "center", gap: "6px", padding: "6px 12px", fontSize: "0.75rem" }}
          onClick={async () => {
            const ok = await downloadAuthenticatedCSV(`${API}/api/export/bookmarks`, "mednama_bookmarks.csv", token);
            if (ok) toast.success("Bookmarks exported to CSV.");
            else toast.error("Failed to export bookmarks.");
          }}
          title="Download your saved MCQs and concept extracts as a CSV"
        >
          <Download size={12} />
          Export
        </button>
        {onPractise && bookmarkedMcqs.length ? (
          <button className="btn-primary" style={{ marginTop: "var(--sp-2)", marginLeft: "8px", padding: "6px 14px", fontSize: "0.78rem" }}
            onClick={() => onPractise({ mcq_ids: bookmarkedMcqs.slice(0, 100).map((m) => m.id), num_questions: Math.min(100, bookmarkedMcqs.length), prefer_unseen: false },
              "Bookmarked questions")}>
            Practise these {Math.min(100, bookmarkedMcqs.length)}
          </button>
        ) : null}
      </div>

      <div
        style={{
          display: "flex",
          gap: "var(--sp-4)",
          borderBottom: "1px solid var(--border-light)",
          marginBottom: "var(--sp-6)",
          paddingBottom: "var(--sp-2)",
        }}
      >
        <button
          className={`btn-workspace-nav ${bookmarksActiveTab === "mcq" ? "active" : ""}`}
          style={{
            borderBottom: bookmarksActiveTab === "mcq" ? "2px solid var(--sky)" : "none",
            borderRadius: 0,
            background: "transparent",
            padding: "6px 12px",
            width: "auto",
          }}
          onClick={() => setBookmarksActiveTab("mcq")}
        >
          Bookmarked MCQs ({bookmarkedMcqs.length})
        </button>
        <button
          className={`btn-workspace-nav ${bookmarksActiveTab === "concept" ? "active" : ""}`}
          style={{
            borderBottom: bookmarksActiveTab === "concept" ? "2px solid var(--sky)" : "none",
            borderRadius: 0,
            background: "transparent",
            padding: "6px 12px",
            width: "auto",
          }}
          onClick={() => setBookmarksActiveTab("concept")}
        >
          Concept Extracts ({bookmarkedConcepts.length})
        </button>
      </div>

      {isLoadingBookmarks ? (
        <div style={{ textAlign: "center", color: "var(--text-muted)", padding: "var(--sp-12)" }}>
          Loading bookmarks...
        </div>
      ) : bookmarksActiveTab === "mcq" ? (
        <div className={`quiz-split-layout ${explanationMCQId !== null ? "has-explanation" : ""}`}>
          <div
            className="quiz-question-col"
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--sp-4)",
              maxHeight: "calc(100vh - 250px)",
              overflowY: "auto",
              paddingRight: "4px",
            }}
          >
            {bookmarkedMcqs.length === 0 ? (
              <div className="empty-state-card">
                <Bookmark size={24} className="empty-icon" />
                <span className="empty-title">No bookmarked questions yet</span>
                <span className="empty-desc">
                  Bookmark questions during practice exams or inside the MCQ Bank to review them here.
                </span>
              </div>
            ) : (
              bookmarkedMcqs.map((mcq) => (
                <div
                  key={mcq.id}
                  style={{
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--r-xl)",
                    padding: "var(--sp-5)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "var(--sp-3)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span
                      style={{
                        fontSize: "0.72rem",
                        color: "var(--sky)",
                        background: "var(--sky-dim)",
                        padding: "2px 8px",
                        borderRadius: "10px",
                        fontWeight: 600,
                      }}
                    >
                      {mcq.main_category}
                    </span>
                    <button
                      className="chat-delete-btn"
                      style={{ position: "static", opacity: 1, color: "var(--teal)" }}
                      onClick={() => toggleBookmarkMCQ(mcq.id)}
                      title="Remove Bookmark"
                    >
                      <Bookmark size={14} fill="currentColor" />
                    </button>
                  </div>
                  <p
                    style={{
                      fontWeight: 500,
                      lineHeight: 1.5,
                      color: "var(--text-primary)",
                      fontFamily: "var(--font-serif)",
                    }}
                  >
                    {mcq.question_text}
                  </p>
                  <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
                    {Object.keys(mcq.options)
                      .sort()
                      .map((key) => {
                        const isCorrect = key === mcq.correct_option;
                        return (
                          <div
                            key={key}
                            className={`option-button ${isCorrect ? "correct" : ""}`}
                            style={{
                              cursor: "default",
                              opacity: 0.9,
                              display: "flex",
                              alignItems: "center",
                              gap: "10px",
                            }}
                          >
                            <span className="option-badge">{key}</span>
                            <span style={{ textAlign: "left", flex: 1 }}>{mcq.options[key]}</span>
                            {isCorrect && <Check size={12} style={{ color: "var(--success)" }} />}
                          </div>
                        );
                      })}
                  </div>
                  <div
                    style={{
                      borderTop: "1px solid var(--border-light)",
                      paddingTop: "var(--sp-3)",
                      display: "flex",
                      justifyContent: "flex-end",
                    }}
                  >
                    <button
                      className="btn-workspace"
                      style={{
                        borderColor: "var(--teal)",
                        color: "var(--teal)",
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                        padding: "4px 10px",
                        fontSize: "0.75rem",
                      }}
                      onClick={() => fetchExplanation(mcq.id)}
                    >
                      <GraduationCap size={12} />
                      Clinical Explanation
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
          <ExplanationPanel
            explanationMCQId={explanationMCQId}
            setExplanationMCQId={setExplanationMCQId}
            explanationLoading={explanationLoading}
            explanationError={explanationError}
            explanationData={explanationData}
            token={token}
            onFigureClick={onFigureClick}
          />
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: "var(--sp-4)",
            maxHeight: "calc(100vh - 250px)",
            overflowY: "auto",
            paddingRight: "4px",
          }}
        >
          {bookmarkedConcepts.length === 0 ? (
            <div className="empty-state-card">
              <BookOpen size={24} className="empty-icon" />
              <span className="empty-title">No textbook concepts saved yet</span>
              <span className="empty-desc">
                Click the "Save" icon on clinical chatbot answers to pin key reference extracts here.
              </span>
            </div>
          ) : (
            bookmarkedConcepts.map((item) => (
              <div
                key={item.id}
                style={{
                  background: "var(--surface-2)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--r-xl)",
                  padding: "var(--sp-5)",
                  position: "relative",
                }}
              >
                <button
                  className="chat-delete-btn"
                  style={{ position: "absolute", right: "var(--sp-4)", top: "var(--sp-4)", opacity: 1 }}
                  onClick={() => handleDeleteConceptBookmark(item.id)}
                  title="Remove Bookmark"
                >
                  <Trash2 size={14} />
                </button>

                <div
                  style={{
                    display: "flex",
                    gap: "var(--sp-2)",
                    alignItems: "center",
                    marginBottom: "var(--sp-3)",
                  }}
                >
                  <span
                    style={{
                      fontSize: "0.72rem",
                      color: "var(--sky)",
                      background: "var(--sky-dim)",
                      padding: "2px 8px",
                      borderRadius: "10px",
                      fontWeight: 600,
                    }}
                  >
                    {item.source_context || "Saved Concept"}
                  </span>
                  {item.book_title && (
                    <span
                      style={{
                        fontSize: "0.72rem",
                        color: "var(--text-secondary)",
                        fontFamily: "var(--font-serif)",
                      }}
                    >
                      {item.book_title} {item.page_number ? <CiteLink bookTitle={item.book_title} page={item.page_number}>p. {item.page_number}</CiteLink> : ""}
                    </span>
                  )}
                </div>

                <div
                  className="prose"
                  dangerouslySetInnerHTML={{ __html: parseMarkdown(item.content) }}
                  style={{ paddingRight: "30px", fontSize: "0.92rem", lineHeight: 1.6 }}
                />
                <div
                  style={{
                    fontSize: "0.68rem",
                    color: "var(--text-muted)",
                    marginTop: "12px",
                    textAlign: "right",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  Saved on {item.created_at}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
