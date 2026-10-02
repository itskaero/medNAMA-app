"use client";

import React from "react";
import { API } from "@/lib/constants";

/** Images that belong to a question (e.g. an ECG or X-ray in a past-paper stem). */
export function QuestionMedia({ ids, token }: { ids?: number[] | null; token: string | null }) {
  if (!ids || ids.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      {ids.map((id) => (
        <a key={id} href={`${API}/api/mcq-media/${id}`} target="_blank" rel="noopener noreferrer"
          style={{ border: "1px solid var(--border-light)", borderRadius: "12px", overflow: "hidden", background: "#0d0f13", display: "block" }}>
          <img src={`${API}/api/mcq-media/${id}`} alt="Image for this question"
            style={{ width: "100%", maxHeight: "380px", objectFit: "contain", display: "block" }} />
        </a>
      ))}
    </div>
  );
}

/** "Asked in 2023, 2025" for past-paper questions. */
export function PaperYears({ years }: { years?: number[] | null }) {
  if (!years || years.length === 0) return null;
  return (
    <span title="Past-paper years this question appeared in"
      style={{ fontSize: "0.7rem", color: "#d97706", background: "rgba(245,158,11,0.12)", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 }}>
      Past paper {years.join(", ")}
    </span>
  );
}
