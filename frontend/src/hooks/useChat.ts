"use client";

import { useState, useCallback, RefObject } from "react";
import { Message, AnswerResponse } from "@/types";
import { API } from "@/lib/constants";
import { proxySafeFetch } from "@/lib/proxyFetch";

interface UseChatParams {
  token: string | null;
  activeConversationId: number | null;
  setActiveConversationId: (id: number | null) => void;
  fetchConversations: () => void;
  messagesEndRef: RefObject<HTMLDivElement | null>;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  /** F2: restrict retrieval to a book and/or chapter. */
  scope?: { book_id: number | null; chapter: string | null };
  /** Study level that sets answer depth: undergraduate | fcps1 | fcps2 (null = general exam prep). */
  level?: string | null;
  /** "Tutor me": Socratic, one question at a time. */
  tutor?: boolean;
}

// The answer is streamed (Server-Sent Events): the backend sends progress
// events and a heartbeat every few seconds, so a healthy request is never idle.
// Give up only if nothing at all arrives for IDLE_TIMEOUT_MS, or the whole
// request exceeds REQUEST_TIMEOUT_MS.
const REQUEST_TIMEOUT_MS = 240_000;
const IDLE_TIMEOUT_MS = 45_000;

export const CHAT_STAGE_TEXT: Record<string, string> = {
  received: "Question received...",
  searching: "Searching your textbooks (rewriting the question into textbook terms)...",
  generating: "Writing a cited answer from the retrieved passages...",
};

/** Final payload of a chat turn (same shape as POST /api/chat/query). */
interface ChatTurnResult {
  conversation_id: number;
  conversation_title: string;
  answer: AnswerResponse;
}

/** Read a text/event-stream body and return the payload of the final "answer" event. */
async function readChatStream(
  res: Response,
  onStage: (stage: string) => void,
  onActivity: () => void
): Promise<ChatTurnResult> {
  if (!res.body) throw new Error("Server returned an empty response.");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    onActivity();
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      if (!dataLines.length) continue; // heartbeat comment
      const data = JSON.parse(dataLines.join("\n"));
      if (event === "stage") onStage(data.stage);
      else if (event === "answer") return data;
      else if (event === "error") {
        throw new Error(
          data?.detail ? `Server error (HTTP ${data.status ?? 500}): ${data.detail}` : "Server error while answering."
        );
      }
    }
  }
  throw new Error("The connection closed before the answer arrived. Please try again.");
}

const API_HOST = String(API).replace(/^https?:\/\//, "");

/** Try to read FastAPI's `detail` (or a JSON `message`) from an error response body. */
async function readServerDetail(res: Response): Promise<string | null> {
  if (!res) return null;
  try {
    const data = await res.json();
    if (data && typeof data.detail === "string") return data.detail;
    if (data && data.message && typeof data.message === "string") return data.message;
  } catch {
    /* body isn't JSON — fall through */
  }
  return null;
}

/** Classify a chat failure into a self-diagnosing message for the UI. */
function describeChatError(err: unknown, timedOut: boolean): string {
  if (timedOut) {
    return (
      "The server stopped responding before the answer arrived (no data for " +
      `${Math.round(IDLE_TIMEOUT_MS / 1000)}s, or over ${Math.round(REQUEST_TIMEOUT_MS / 1000)}s in total). ` +
      "Check the backend is running, then try again."
    );
  }
  if (err instanceof SyntaxError) {
    return "Server returned an unreadable response (invalid data). Try again.";
  }
  const msg = err instanceof Error ? err.message : String(err);
  const lower = msg.toLowerCase();
  if (
    lower.includes("failed to fetch") ||
    lower.includes("load failed") ||
    lower.includes("networkerror") ||
    lower.includes("network error") ||
    lower.includes("connection")
  ) {
    return (
      `Backend unreachable — ${API_HOST} didn't respond to the request. ` +
      "Check that the API server is running (local uvicorn or Railway) and reachable from the browser."
    );
  }
  return msg || "Search failed.";
}

export function useChat({
  token,
  activeConversationId,
  setActiveConversationId,
  fetchConversations,
  messagesEndRef,
  inputRef,
  messages,
  setMessages,
  scope,
  level,
  tutor = false,
}: UseChatParams) {
  const [inputValue, setInputValue] = useState("");
  const [isSearching, setIsSearching] = useState(false);

  const getHeaders = useCallback((): HeadersInit => {
    const t = localStorage.getItem("token") || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  const sendQuery = useCallback(
    async (q: string) => {
      if (!q.trim() || isSearching) return;
      const queryText = q.trim();
      setInputValue("");
      setIsSearching(true);

      const timeStr = new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

      const uid = `u-${Date.now()}`;
      const tid = `t-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        { id: uid, type: "user", content: queryText, timestamp: timeStr },
        { id: tid, type: "thinking" },
      ]);

      // Abort when the stream goes silent (dead backend) or the overall cap is
      // hit, so the user gets a clear message instead of "Failed to fetch".
      // NOTE: timedOut/timers must live OUTSIDE the try so the catch block can
      // read them (try/catch are separate block scopes).
      let timedOut = false;
      let timeoutId: ReturnType<typeof setTimeout> | undefined;
      let idleId: ReturnType<typeof setTimeout> | undefined;
      try {
        const controller = new AbortController();
        const abortWithTimeout = () => {
          timedOut = true;
          controller.abort();
        };
        timeoutId = setTimeout(abortWithTimeout, REQUEST_TIMEOUT_MS);
        const bumpIdle = () => {
          if (idleId) clearTimeout(idleId);
          idleId = setTimeout(abortWithTimeout, IDLE_TIMEOUT_MS);
        };
        bumpIdle();

      let responseData: ChatTurnResult;
      try {
        const res = await proxySafeFetch(`${API}/api/chat/query/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...getHeaders() },
          credentials: "include",
          signal: controller.signal,
          body: JSON.stringify({
            query: queryText,
            conversation_id: activeConversationId,
            book_id: scope?.book_id ?? null,
            chapter: scope?.chapter ?? null,
            level: level ?? null,
            mode: tutor ? "tutor" : null,
          }),
        });

        if (!res.ok) {
          const detail = await readServerDetail(res);
          throw new Error(
            detail
              ? `Server error (HTTP ${res.status}): ${detail}`
              : `Server error (HTTP ${res.status}). The API returned no usable error detail.`
          );
        }

        responseData = await readChatStream(
          res,
          (stage) =>
            setMessages((prev) => prev.map((m) => (m.id === tid && m.type === "thinking" ? { ...m, stage } : m))),
          bumpIdle
        );
      } finally {
        clearTimeout(timeoutId);
        if (idleId) clearTimeout(idleId);
      }

        const convId = responseData.conversation_id;
        const data: AnswerResponse = responseData.answer;
        const isNewChat = activeConversationId === null;

        if (isNewChat) {
          setActiveConversationId(convId);
        }

        const words = data.answer_markdown.split(" ");
        let currentText = "";
        let wordIdx = 0;

        setMessages((prev) =>
          prev.map((m) =>
            m.id === tid
              ? {
                  id: tid,
                  type: "ai",
                  answer: { answer_markdown: "", citations: [], figures: [] },
                  query: queryText,
                  timestamp: timeStr,
                }
              : m
          )
        );

        const streamInterval = setInterval(() => {
          if (wordIdx < words.length) {
            currentText += (wordIdx === 0 ? "" : " ") + words[wordIdx];
            setMessages((prev) =>
              prev.map((m) =>
                m.id === tid
                  ? {
                      id: tid,
                      type: "ai",
                      answer: { answer_markdown: currentText, citations: [], figures: [] },
                      query: queryText,
                      timestamp: timeStr,
                    }
                  : m
              )
            );
            wordIdx++;
          } else {
            clearInterval(streamInterval);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === tid ? { id: tid, type: "ai", answer: data, query: queryText, timestamp: timeStr } : m
              )
            );
            setIsSearching(false);
            if (isNewChat) {
              fetchConversations();
            }
            inputRef.current?.focus();
          }
        }, 15);
      } catch (err: any) {
        if (timeoutId) clearTimeout(timeoutId);
        if (idleId) clearTimeout(idleId);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === tid
              ? { id: tid, type: "error", errorMsg: describeChatError(err, timedOut), timestamp: timeStr }
              : m
          )
        );
        setIsSearching(false);
        inputRef.current?.focus();
      }
    },
    [isSearching, getHeaders, activeConversationId, fetchConversations, scope, level, tutor]
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendQuery(inputValue);
    }
  };

  return {
    inputValue,
    setInputValue,
    isSearching,
    sendQuery,
    handleKeyDown,
  };
}
