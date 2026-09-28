"use client";

import React from "react";
import { GraduationCap, X, Loader2 } from "lucide-react";
import { AnswerResponse, Figure } from "@/types";
import { parseMarkdown } from "@/utils/markdown";
import { CitationsDrawer, FiguresDrawer } from "@/components";
import { ReportButton } from "@/components/ReportButton";

interface ExplanationPanelProps {
  explanationMCQId: number | null;
  setExplanationMCQId: (id: number | null) => void;
  explanationLoading: boolean;
  explanationError: string | null;
  explanationData: AnswerResponse | null;
  token: string | null;
  onFigureClick: (fig: Figure) => void;
}

export default function ExplanationPanel({
  explanationMCQId,
  setExplanationMCQId,
  explanationLoading,
  explanationError,
  explanationData,
  token,
  onFigureClick,
}: ExplanationPanelProps) {
  if (explanationMCQId === null) return null;

  return (
    <div className="explanation-inline-panel">
      <div className="explanation-inline-header">
        <h3 className="explanation-inline-title">
          <GraduationCap size={16} style={{ color: "var(--teal)" }} />
          Textbook explanation
        </h3>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <ReportButton
            kind="mcq"
            token={token}
            mcqId={explanationMCQId}
            answerExcerpt={explanationData?.answer_markdown}
          />
          <button
            className="close-btn"
            onClick={() => setExplanationMCQId(null)}
            aria-label="Close explanation panel"
          >
            <X size={16} />
          </button>
        </div>
      </div>
      <div className="explanation-inline-body">
        {explanationLoading ? (
          <div style={{ textAlign: "center", padding: "32px 16px", color: "var(--text-muted)" }}>
            <Loader2
              size={22}
              className="spinner"
              style={{ margin: "0 auto 10px", display: "inline-block", animation: "spin 1s linear infinite" }}
            />
            <span style={{ display: "block", fontSize: "0.82rem" }}>Writing the explanation from your textbooks...</span>
          </div>
        ) : explanationError ? (
          <div style={{ color: "var(--danger)", padding: "16px", fontSize: "0.82rem", textAlign: "center" }}>
            {explanationError}
          </div>
        ) : explanationData ? (
          <>
            <div
              className="prose"
              dangerouslySetInnerHTML={{ __html: parseMarkdown(explanationData.answer_markdown) }}
            />

            {explanationData.citations.length > 0 && (
              <div style={{ marginTop: "12px" }}>
                <h4
                  style={{
                    fontSize: "0.82rem",
                    fontWeight: 600,
                    color: "var(--text-primary)",
                    marginBottom: "6px",
                  }}
                >
                  Textbook References
                </h4>
                <CitationsDrawer citations={explanationData.citations} token={token} />
              </div>
            )}

            {explanationData.figures.length > 0 && (
              <div style={{ marginTop: "12px" }}>
                <FiguresDrawer
                  figures={explanationData.figures}
                  token={token}
                  onFigureClick={onFigureClick}
                />
              </div>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}
