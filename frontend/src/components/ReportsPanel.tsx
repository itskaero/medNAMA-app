"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Flag, Check, X, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

interface AnswerReport {
  id: number;
  kind: "chat" | "mcq";
  mcq_id: number | null;
  question: string | null;
  answer_excerpt: string | null;
  reason: string;
  status: "open" | "resolved" | "dismissed";
  username: string | null;
  created_at: string | null;
}

/** Admin review queue for "Report wrong answer" flags. */
export function ReportsPanel({ token }: { token: string | null }) {
  const [filter, setFilter] = useState<"open" | "all">("open");
  const [reports, setReports] = useState<AnswerReport[] | null>(null); // null = loading
  const [refreshKey, setRefreshKey] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);

  const headers = useCallback((): HeadersInit => {
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/reports?status_filter=${filter}`, { headers: headers(), credentials: "include" })
      .then((res) => (res.ok ? res.json() : []))
      .then((data: AnswerReport[]) => {
        if (!cancelled) setReports(data);
      })
      .catch(() => {
        if (!cancelled) setReports([]);
      });
    return () => {
      cancelled = true;
    };
  }, [filter, headers, refreshKey]);

  const load = () => setRefreshKey((k) => k + 1);

  const setStatus = async (id: number, status: AnswerReport["status"]) => {
    const res = await fetch(`${API}/api/reports/${id}`, {
      method: "PATCH",
      headers: { ...headers(), "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ status }),
    });
    if (!res.ok) {
      toast.error("Could not update the report.");
      return;
    }
    load();
  };

  const list = reports ?? [];
  const openCount = list.filter((r) => r.status === "open").length;

  return (
    <section
      aria-label="Reported answers"
      style={{
        marginTop: "var(--sp-6, 24px)",
        padding: "16px",
        borderRadius: "14px",
        border: "1px solid var(--border-light)",
        background: "var(--surface-2, var(--surface-3))",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px", marginBottom: "10px" }}>
        <h3 style={{ margin: 0, fontSize: "0.95rem", display: "flex", alignItems: "center", gap: "6px", color: "var(--text-primary)" }}>
          <Flag size={14} style={{ color: "var(--error)" }} />
          Reported answers
          {filter === "open" ? (
            <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", fontWeight: 400 }}>({openCount} open)</span>
          ) : null}
        </h3>
        <div style={{ display: "flex", gap: "4px" }}>
          {(["open", "all"] as const).map((f) => (
            <button
              key={f}
              type="button"
              className="btn-workspace"
              onClick={() => {
                setReports(null);
                setFilter(f);
              }}
              style={{
                padding: "3px 10px",
                fontSize: "0.7rem",
                borderColor: filter === f ? "var(--sky)" : undefined,
                color: filter === f ? "var(--sky)" : undefined,
              }}
            >
              {f === "open" ? "Open" : "All"}
            </button>
          ))}
        </div>
      </div>

      {reports === null ? (
        <p style={{ fontSize: "0.8rem", color: "var(--text-muted)", margin: 0 }}>Loading…</p>
      ) : !list.length ? (
        <p style={{ fontSize: "0.8rem", color: "var(--text-muted)", margin: 0 }}>
          {filter === "open" ? "No open reports." : "No reports yet."}
        </p>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "8px" }}>
          {list.map((r) => (
            <li
              key={r.id}
              style={{
                padding: "10px 12px",
                borderRadius: "10px",
                border: "1px solid var(--border)",
                background: "var(--surface-3)",
                opacity: r.status === "open" ? 1 : 0.65,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: "8px", alignItems: "flex-start" }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {r.kind === "mcq" ? `MCQ #${r.mcq_id}` : "Chat answer"} · {r.username ?? "?"} ·{" "}
                    {r.created_at ? new Date(r.created_at).toLocaleString() : ""} · {r.status}
                  </div>
                  <div style={{ fontSize: "0.82rem", color: "var(--text-primary)", marginTop: "2px" }}>{r.reason}</div>
                  {r.question ? (
                    <div style={{ fontSize: "0.76rem", color: "var(--text-secondary)", marginTop: "4px" }}>
                      <strong>Q:</strong> {r.question}
                    </div>
                  ) : null}
                  {r.answer_excerpt ? (
                    <button
                      type="button"
                      onClick={() => setExpanded(expanded === r.id ? null : r.id)}
                      style={{ background: "none", border: "none", padding: 0, marginTop: "4px", color: "var(--sky)", fontSize: "0.72rem", cursor: "pointer" }}
                    >
                      {expanded === r.id ? "Hide answer" : "Show answer"}
                    </button>
                  ) : null}
                  {expanded === r.id && r.answer_excerpt ? (
                    <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.72rem", color: "var(--text-secondary)", margin: "6px 0 0", fontFamily: "inherit" }}>
                      {r.answer_excerpt}
                    </pre>
                  ) : null}
                </div>
                <div style={{ display: "flex", gap: "4px", flexShrink: 0 }}>
                  {r.status === "open" ? (
                    <>
                      <button type="button" className="btn-workspace" title="Mark resolved" onClick={() => setStatus(r.id, "resolved")} style={{ padding: "4px 8px" }}>
                        <Check size={12} />
                      </button>
                      <button type="button" className="btn-workspace" title="Dismiss" onClick={() => setStatus(r.id, "dismissed")} style={{ padding: "4px 8px" }}>
                        <X size={12} />
                      </button>
                    </>
                  ) : (
                    <button type="button" className="btn-workspace" title="Reopen" onClick={() => setStatus(r.id, "open")} style={{ padding: "4px 8px" }}>
                      <RotateCcw size={12} />
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
