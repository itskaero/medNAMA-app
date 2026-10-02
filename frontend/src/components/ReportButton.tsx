"use client";

import React, { useState } from "react";
import { Flag, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

const QUICK_REASONS = ["Wrong fact", "Wrong citation / page", "Outdated guideline", "Incomplete answer"];

/**
 * "Report wrong answer" control for chat answers and MCQs. Opens a small inline
 * form; the report lands in the admin review queue (dashboard).
 */
export function ReportButton({
  kind,
  token,
  mcqId,
  question,
  answerExcerpt,
}: {
  kind: "chat" | "mcq";
  token: string | null;
  mcqId?: number | null;
  question?: string;
  answerExcerpt?: string;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  const submit = async () => {
    if (!reason.trim()) {
      toast.error("Say briefly what is wrong.");
      return;
    }
    setSending(true);
    try {
      const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
      const res = await fetch(`${API}/api/reports`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
        credentials: "include",
        body: JSON.stringify({
          kind,
          reason: reason.trim(),
          mcq_id: mcqId ?? null,
          question: question ?? null,
          answer_excerpt: answerExcerpt ? answerExcerpt.slice(0, 4000) : null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error((body && body.detail) || `HTTP ${res.status}`);
      }
      setSent(true);
      setOpen(false);
      toast.success("Thanks — reported for review.");
    } catch (e) {
      toast.error(`Could not send report: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSending(false);
    }
  };

  if (sent) {
    return (
      <span style={{ fontSize: "0.68rem", color: "var(--text-muted)", display: "inline-flex", alignItems: "center", gap: "4px" }}>
        <Flag size={10} /> Reported
      </span>
    );
  }

  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        className="btn-workspace"
        style={{ display: "flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.72rem" }}
        onClick={() => setOpen((v) => !v)}
        title="Report a wrong or badly cited answer"
        aria-expanded={open}
      >
        <Flag size={10} />
        <span>Report</span>
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label="Report this answer"
          style={{
            position: "absolute",
            bottom: "calc(100% + 6px)",
            right: 0,
            zIndex: 30,
            width: "min(300px, 80vw)",
            padding: "10px",
            borderRadius: "10px",
            border: "1px solid var(--border-light)",
            background: "var(--surface-2, var(--surface-3))",
            boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
            display: "flex",
            flexDirection: "column",
            gap: "8px",
          }}
        >
          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
            {QUICK_REASONS.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setReason((prev) => (prev ? prev : r))}
                style={{
                  fontSize: "0.66rem",
                  padding: "2px 8px",
                  borderRadius: "999px",
                  border: "1px solid var(--border-light)",
                  background: "var(--surface-3)",
                  color: "var(--text-secondary)",
                  cursor: "pointer",
                }}
              >
                {r}
              </button>
            ))}
          </div>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="What is wrong? (e.g. the dose is outdated)"
            rows={3}
            style={{
              width: "100%",
              resize: "vertical",
              fontSize: "0.78rem",
              padding: "6px 8px",
              borderRadius: "8px",
              border: "1px solid var(--border-light)",
              background: "var(--surface-3)",
              color: "var(--text-primary)",
            }}
          />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "6px" }}>
            <button type="button" className="btn-workspace" style={{ padding: "4px 10px", fontSize: "0.72rem" }} onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-workspace"
              style={{ padding: "4px 10px", fontSize: "0.72rem", display: "flex", alignItems: "center", gap: "4px" }}
              onClick={submit}
              disabled={sending}
            >
              {sending ? <Loader2 size={10} className="animate-spin" /> : <Flag size={10} />}
              Send
            </button>
          </div>
        </div>
      ) : null}
    </span>
  );
}
