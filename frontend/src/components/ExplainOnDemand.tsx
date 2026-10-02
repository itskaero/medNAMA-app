"use client";

import React, { useState } from "react";
import { BookOpen, Loader2 } from "lucide-react";
import { API } from "@/lib/constants";
import { parseMarkdown } from "@/utils/markdown";

/** For questions imported without an explanation: write one from the textbooks when asked (cached server-side). */
export function ExplainOnDemand({ mcqId, token }: { mcqId: number; token: string | null }) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [markdown, setMarkdown] = useState<string | null>(null);

  const load = async () => {
    setState("loading");
    const t = (typeof window !== "undefined" && localStorage.getItem("token")) || token;
    try {
      const res = await fetch(`${API}/api/mcqs/${mcqId}/explain`, {
        method: "POST",
        headers: t ? { Authorization: `Bearer ${t}` } : {},
        credentials: "include",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      setMarkdown(body.answer_markdown || "No explanation could be written for this question.");
      setState("done");
    } catch {
      setState("error");
    }
  };

  if (state === "done" && markdown) {
    return <div className="prose" style={{ fontSize: "0.86rem" }} dangerouslySetInnerHTML={{ __html: parseMarkdown(markdown) }} />;
  }
  return (
    <button type="button" className="btn-workspace" onClick={load} disabled={state === "loading"}
      style={{ alignSelf: "flex-start", padding: "4px 12px", fontSize: "0.76rem" }}>
      {state === "loading" ? <Loader2 size={12} className="animate-spin" /> : <BookOpen size={12} />}{" "}
      {state === "loading" ? "Checking the textbooks…" : state === "error" ? "Could not explain: try again" : "Explain from the textbooks"}
    </button>
  );
}
