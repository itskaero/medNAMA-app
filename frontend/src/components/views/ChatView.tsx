"use client";

import React, { RefObject, useEffect, useState } from "react";
import {
  Stethoscope,
  Send,
  MessageSquare,
  Loader2,
  Minimize2,
  Plus,
  Pin,
  Trash2,
  ChevronDown,
  X,
  BookOpen,
  ListChecks,
  FileText,
} from "lucide-react";
import { Message, Figure, Book } from "@/types";
import { AIMessage } from "@/components";
import BasicDropdown from "@/components/ui/basic-dropdown";
import { toast } from "sonner";
import { API } from "@/lib/constants";
import { groupConversations } from "@/utils/quizHelpers";

export interface ChatScope {
  book_id: number | null;
  chapter: string | null;
}

const SUGGESTIONS = [
  { icon: <span>🫀</span>, text: "Explain the Frank-Starling mechanism and what shifts the curve" },
  { icon: <span>💊</span>, text: "Mechanism and adverse effects of SGLT2 inhibitors" },
  { icon: <span>🔬</span>, text: "Nephritic vs nephrotic syndrome: key differences" },
  { icon: <span>🧠</span>, text: "Blood supply of the internal capsule and the effect of its lesions" },
];

interface ChatViewProps {
  messages: Message[];
  inputValue: string;
  setInputValue: (v: string) => void;
  isSearching: boolean;
  sendQuery: (q: string) => void;
  handleKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  conversations: any[];
  activeConversationId: number | null;
  isLoadingConversations: boolean;
  handleSelectConversation: (id: number) => void;
  handleNewChat: () => void;
  handleDeleteConversation: (id: number) => void;
  isSidebarLocked: boolean;
  setIsSidebarLocked: (v: boolean) => void;
  isSidebarHovered: boolean;
  setIsSidebarHovered: (v: boolean) => void;
  isSidebarResizing: boolean;
  sidebarWidth: number;
  startResizing: (e: React.MouseEvent) => void;
  setIsChatMinimized: (v: boolean) => void;
  setActiveView: (view: any) => void;
  /** Start a generated question set straight away (Quiz me on this chapter). */
  onStartQuizSet?: (quizSetId: string, label: string) => void;
  token: string | null;
  messagesEndRef: RefObject<HTMLDivElement | null>;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onFigureClick: (fig: Figure) => void;
  handleCreateConceptBookmark: (
    content: string,
    title?: string | null,
    page?: number | null,
    context?: string | null
  ) => void;
  // F2 — scoped retrieval
  books?: Book[];
  scope?: ChatScope;
  setScope?: (s: ChatScope) => void;
  chapters?: string[];
  fetchChapters?: (bookId: number) => void;
  // Study level for answer depth
  level?: string | null;
  setLevel?: (level: string | null) => void;
  tutor?: boolean;
  setTutor?: (on: boolean) => void;
}

/** Study-a-chapter actions: a cited high-yield summary, or 10 MCQs from this chapter only. */
function ChapterStudyActions({
  bookId,
  chapter,
  token,
  sendQuery,
  onOpenQuiz,
}: {
  bookId: number;
  chapter: string;
  token: string | null;
  sendQuery: (q: string) => void;
  onOpenQuiz?: (quizSetId: string | null, label: string) => void;
}) {
  const [quizBusy, setQuizBusy] = useState(false);

  const quizMe = async () => {
    setQuizBusy(true);
    const t = localStorage.getItem("token") || token;
    const headers: HeadersInit = { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
    try {
      const start = await fetch(`${API}/api/chat/generate-ai-quiz/jobs`, {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify({ prompt: `High-yield FCPS questions on ${chapter}`, book_id: bookId, chapter, count: 10, exam_profile: "fcps" }),
      });
      const started = await start.json().catch(() => null);
      if (!start.ok || !started?.job_id) throw new Error((started && started.detail) || `HTTP ${start.status}`);
      toast.message("Writing 10 questions from this chapter…");
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2500));
        const res = await fetch(`${API}/api/chat/generate-ai-quiz/jobs/${started.job_id}`, { headers, credentials: "include" });
        const job = await res.json().catch(() => null);
        if (job?.status === "done") {
          toast.success(`${job.result?.total_questions ?? 10} chapter questions ready.`, {
            action: onOpenQuiz ? { label: "Start now", onClick: () => onOpenQuiz(job.result?.quiz_set_id ?? null, `Chapter · ${chapter}`) } : undefined,
          });
          return;
        }
        if (job?.status === "failed") throw new Error(job.detail || "generation failed");
      }
      throw new Error("still generating; check Saved History shortly");
    } catch (e) {
      toast.error(`Chapter quiz: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setQuizBusy(false);
    }
  };

  const btn: React.CSSProperties = { display: "inline-flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.7rem" };
  return (
    <>
      <button className="btn-workspace" style={btn} title="High-yield summary of this chapter, cited to its pages"
        onClick={() => sendQuery(`Give me a high-yield FCPS summary of the chapter "${chapter}": key facts, numbers, classic associations and exam traps.`)}>
        <FileText size={10} /> Summarise chapter
      </button>
      <button className="btn-workspace" style={btn} disabled={quizBusy} onClick={quizMe} title="10 FCPS-style questions from this chapter only">
        {quizBusy ? <Loader2 size={10} className="animate-spin" /> : <ListChecks size={10} />} Quiz me on this chapter
      </button>
    </>
  );
}

export default function ChatView({
  messages,
  inputValue,
  setInputValue,
  isSearching,
  sendQuery,
  handleKeyDown,
  conversations,
  activeConversationId,
  isLoadingConversations,
  handleSelectConversation,
  handleNewChat,
  handleDeleteConversation,
  isSidebarLocked,
  setIsSidebarLocked,
  isSidebarHovered,
  setIsSidebarHovered,
  isSidebarResizing,
  sidebarWidth,
  startResizing,
  setIsChatMinimized,
  setActiveView,
  onStartQuizSet,
  token,
  messagesEndRef,
  inputRef,
  onFigureClick,
  handleCreateConceptBookmark,
  books = [],
  scope,
  setScope,
  chapters = [],
  fetchChapters,
  level = null,
  setLevel,
  tutor = false,
  setTutor,
}: ChatViewProps) {
  // Phones: the history is a drawer opened from the bar above the conversation.
  const [mobileHistory, setMobileHistory] = useState(false);
  const wide = isSidebarHovered || isSidebarResizing || isSidebarLocked || mobileHistory;

  // Scroll to bottom whenever messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="chat-view-container">
      {/* Chat Mini-Sidebar (Left) */}
      <div
        className={`chat-history-sidebar ${isSidebarLocked ? "locked" : ""} ${
          isSidebarHovered || isSidebarResizing ? "expanded" : "collapsed"
        } ${isSidebarResizing ? "resizing" : ""} ${mobileHistory ? "mobile-open" : ""}`}
        style={{ "--sidebar-width": `${sidebarWidth}px` } as React.CSSProperties}
        onMouseEnter={() => setIsSidebarHovered(true)}
        onMouseLeave={() => setIsSidebarHovered(false)}
        onClick={(e) => {
          // Picking a conversation or starting a new one closes the phone drawer.
          if (mobileHistory && (e.target as HTMLElement).closest(".chat-history-item, .new-chat-btn")) setMobileHistory(false);
        }}
      >
        <div
          className="chat-sidebar-expanded-content"
          style={{
            opacity: 1,
            pointerEvents: "auto",
            display: "flex",
            flexDirection: "column",
            height: "100%",
            width: "100%",
            padding: "12px",
            boxSizing: "border-box",
          }}
        >
          {wide ? (
            <div style={{ display: "flex", alignItems: "center", gap: "8px", width: "100%" }}>
              <button className="new-chat-btn" onClick={handleNewChat} style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden" }}>
                <Plus size={14} style={{ flexShrink: 0 }} />
                <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>New Chat</span>
              </button>
              <button
                className={`sidebar-pin-btn ${isSidebarLocked ? "active" : ""}`}
                onClick={() => setIsSidebarLocked(!isSidebarLocked)}
                title={isSidebarLocked ? "Unlock sidebar hover-collapse" : "Lock sidebar expanded"}
                style={{ flexShrink: 0 }}
              >
                <Pin size={12} style={{ transform: isSidebarLocked ? "none" : "rotate(-45deg)" }} />
              </button>
            </div>
          ) : (
            <button className="new-chat-btn-collapsed" onClick={handleNewChat} title="New Chat">
              <Plus size={16} />
            </button>
          )}

          <div className="chat-history-list" style={{ marginTop: "12px" }}>
            {isLoadingConversations ? (
              <div className="chat-history-loading">Loading...</div>
            ) : conversations.length === 0 ? (
              <div
                className="chat-history-empty"
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "8px",
                  padding: "32px 8px",
                  color: "var(--text-muted)",
                  fontSize: "0.8rem",
                  textAlign: "center",
                }}
              >
                <MessageSquare size={16} style={{ opacity: 0.3 }} />
                {wide && (
                  <span>No recent discussions</span>
                )}
              </div>
            ) : (
              (() => {
                const isExpanded = wide;
                const getMonogram = (title: string): string => {
                  if (!title) return "CH";
                  const words = title.trim().split(/\s+/);
                  if (words.length >= 2) {
                    return (words[0][0] + words[1][0]).toUpperCase();
                  }
                  return title.slice(0, 2).toUpperCase();
                };

                if (!isExpanded) {
                  return conversations.map((conv) => {
                    const monogram = getMonogram(conv.title);
                    return (
                      <div
                        key={conv.id}
                        className={`chat-history-item ${activeConversationId === conv.id ? "active" : ""}`}
                        onClick={() => handleSelectConversation(conv.id)}
                        title={conv.title}
                        style={{ padding: "6px 0", display: "flex", justifyContent: "center", alignItems: "center" }}
                      >
                        <div className="chat-monogram-badge">{monogram}</div>
                      </div>
                    );
                  });
                }

                const grouped = groupConversations(conversations);
                const renderGroupSection = (title: string, list: any[]) => {
                  if (list.length === 0) return null;
                  return (
                    <div
                      className="chat-history-group"
                      key={title}
                      style={{ display: "flex", flexDirection: "column", gap: "2px", marginTop: "12px" }}
                    >
                      <div
                        className="chat-history-group-title"
                        style={{
                          fontSize: "0.68rem",
                          textTransform: "uppercase",
                          letterSpacing: "0.08em",
                          color: "var(--text-muted)",
                          padding: "4px var(--sp-3)",
                          fontWeight: 600,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                        }}
                      >
                        {title}
                      </div>
                      {list.map((conv) => (
                        <div
                          key={conv.id}
                          className={`chat-history-item ${activeConversationId === conv.id ? "active" : ""}`}
                          onClick={() => handleSelectConversation(conv.id)}
                          style={{ display: "flex", alignItems: "center" }}
                        >
                          <MessageSquare size={13} className="chat-icon" />
                          <span
                            className="chat-title"
                            title={conv.title}
                            style={{ marginLeft: "8px", flex: 1 }}
                          >
                            {conv.title}
                          </span>
                          <button
                            className="chat-delete-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteConversation(conv.id);
                            }}
                            title="Delete Chat"
                            aria-label="Delete Chat"
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      ))}
                    </div>
                  );
                };
                return [
                  renderGroupSection("Today", grouped.today),
                  renderGroupSection("Yesterday", grouped.yesterday),
                  renderGroupSection("Older", grouped.older),
                ];
              })()
            )}
          </div>
        </div>

        {/* Vertical drag handle for resizing */}
        <div
          className="sidebar-resize-handle"
          onMouseDown={startResizing}
          title="Drag to resize chat history"
          aria-label="Resize sidebar handle"
        />
      </div>

      {mobileHistory ? <div className="chat-drawer-backdrop" onClick={() => setMobileHistory(false)} aria-hidden /> : null}

      {/* Chat Active Screen (Right) */}
      <div className="chat-active-panel" style={{ position: "relative" }}>
        <div className="chat-mobile-bar">
          <button className="btn-workspace" onClick={() => setMobileHistory(true)}><MessageSquare size={13} /> History</button>
          <button className="btn-workspace" onClick={handleNewChat}><Plus size={13} /> New chat</button>
        </div>
        {/* Minimize Button */}
        <button
          className="chat-minimize-btn"
          onClick={() => {
            setIsChatMinimized(true);
            setActiveView("dashboard");
          }}
          title="Minimize Chat"
          aria-label="Minimize Chat"
        >
          <Minimize2 size={13} />
          <span>Minimize</span>
        </button>

        {/* Conversation area */}
        <div className="conversation" role="log" aria-label="Conversation" aria-live="polite">
          {messages.length === 0 ? (
            <div className="welcome">

              <h1 className="welcome-title">
                What would you like to <em>research</em> today?
              </h1>
              <p className="welcome-body">
                Ask any medical question and receive a grounded, evidence-based answer drawn strictly from
                textbooks — with inline citations and diagrams.
              </p>
              <div className="suggestion-grid" role="list" aria-label="Suggested questions">
                {SUGGESTIONS.map((s, i) => (
                  <button
                    key={i}
                    className="suggestion-chip"
                    role="listitem"
                    onClick={() => sendQuery(s.text)}
                  >
                    <span className="suggestion-chip-icon" aria-hidden>
                      {s.icon}
                    </span>
                    {s.text}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="chat-message-thread">
              <div className="chat-date-separator">
                <span>
                  TODAY ·{" "}
                  {new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
              {messages.map((msg) =>
                msg.type === "user" ? (
                  <div key={msg.id} className="user-message">
                    <div className="user-bubble">
                      <div>{msg.content}</div>
                      {msg.timestamp && (
                        <div
                          style={{
                            fontSize: "0.68rem",
                            opacity: 0.7,
                            marginTop: "4px",
                            textAlign: "right",
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {msg.timestamp}
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <AIMessage
                    key={msg.id}
                    msg={msg}
                    token={token}
                    onFigureClick={onFigureClick}
                    onBookmarkConcept={(content) => {
                      const firstCitation = msg.answer?.citations?.[0];
                      const title = firstCitation ? firstCitation.book_title : null;
                      const page = firstCitation ? firstCitation.page_number : null;
                      handleCreateConceptBookmark(content, title, page, "RAG Chatbot");
                    }}
                  />
                )
              )}
              <div ref={messagesEndRef} aria-hidden />
            </div>
          )}
        </div>

        {/* Input bar */}
        <div className="input-area">
          {/* F2 — source scope (book + chapter scoping) */}
          <div
            style={{
              display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap",
              marginBottom: "8px", padding: "0 4px",
            }}
            aria-label="Chat source scope"
          >
            <span
              style={{
                fontSize: "0.62rem", fontWeight: 700, color: "var(--text-muted)",
                textTransform: "uppercase", letterSpacing: "0.06em",
                display: "inline-flex", alignItems: "center", gap: "4px",
              }}
            >
              <BookOpen size={11} />
              Source scope
            </span>

            {setScope && fetchChapters ? (
              <>
                <div style={{ width: "200px" }}>
                  <BasicDropdown
                    items={[
                      { value: "all", label: "All Textbooks" },
                      ...(books?.filter((b) => b.status === "ready").map((b) => ({
                        value: b.id.toString(),
                        label: b.title,
                      })) || []),
                    ]}
                    value={scope?.book_id ? scope.book_id.toString() : "all"}
                    onChange={(val) => {
                      setScope({ book_id: val === "all" ? null : Number(val), chapter: null });
                      if (val !== "all") fetchChapters(Number(val));
                    }}
                    ariaLabel="Scope retrieval to a textbook"
                  />
                </div>

                {setTutor ? (
                  <div role="radiogroup" aria-label="How Dr MedNama replies" style={{ display: "inline-flex", gap: "4px" }}
                    title="Tutor me: Dr MedNama asks you one question at a time and corrects you from the books, then gives the full answer">
                    <button type="button" role="radio" aria-checked={!tutor} className={`practice-chip ${!tutor ? "active" : ""}`}
                      onClick={() => setTutor(false)}>Answer</button>
                    <button type="button" role="radio" aria-checked={tutor} className={`practice-chip ${tutor ? "active" : ""}`}
                      onClick={() => setTutor(true)}>Tutor me</button>
                  </div>
                ) : null}

                {setLevel ? (
                  <div style={{ width: "170px" }} title="Sets the depth and focus of answers">
                    <BasicDropdown
                      items={[
                        { value: "any", label: "Level: General" },
                        { value: "undergraduate", label: "Level: MBBS" },
                        { value: "fcps1", label: "Level: FCPS-I" },
                        { value: "fcps2", label: "Level: FCPS-II" },
                      ]}
                      value={level ?? "any"}
                      onChange={(val) => setLevel(val === "any" ? null : val)}
                      ariaLabel="Study level for answers"
                    />
                  </div>
                ) : null}

                {scope?.book_id ? (
                  <div style={{ width: "220px", maxWidth: "40vw" }}>
                    <BasicDropdown
                      items={[
                        { value: "all", label: "All Chapters" },
                        ...chapters.map((c) => ({ value: c, label: c })),
                      ]}
                      value={scope.chapter ?? "all"}
                      onChange={(val) =>
                        setScope({ ...scope, chapter: val === "all" ? null : val })
                      }
                      ariaLabel="Scope retrieval to a chapter"
                    />
                  </div>
                ) : null}
              </>
            ) : null}

            {scope?.book_id ? (
              <>
                <span
                  className="model-chip-pill"
                  style={{ fontSize: "0.7rem", maxWidth: "260px", overflow: "hidden", textOverflow: "ellipsis" }}
                  title={
                    (books?.find((b) => b.id === scope.book_id)?.title || `Book ${scope.book_id}`) +
                    (scope.chapter ? ` · ${scope.chapter}` : "")
                  }
                >
                  <span className="model-chip-dot" />
                  {books?.find((b) => b.id === scope.book_id)?.title || `Book ${scope.book_id}`}
                  {scope.chapter ? ` · ${scope.chapter}` : ""}
                </span>
                <button
                  className="btn-workspace"
                  onClick={() => setScope?.({ book_id: null, chapter: null })}
                  title="Clear source scope — search all textbooks"
                  aria-label="Clear source scope"
                  style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.7rem" }}
                >
                  <X size={10} />
                  Clear
                </button>
                {scope.chapter ? (
                  <ChapterStudyActions
                    bookId={scope.book_id}
                    chapter={scope.chapter}
                    token={token}
                    sendQuery={sendQuery}
                    onOpenQuiz={(id, label) => (id && onStartQuizSet ? onStartQuizSet(id, label) : setActiveView("quiz"))}
                  />
                ) : null}
              </>
            ) : null}
          </div>

          <div className="composer-card">
            <textarea
              ref={inputRef}
              className="input-box"
              placeholder={isSearching ? "Consulting reference library…" : "Reply to Dr. MedNama…"}
              value={inputValue}
              onChange={(e) => {
                setInputValue(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
              }}
              onKeyDown={handleKeyDown}
              disabled={isSearching}
              rows={1}
              aria-label="Ask a medical question"
              aria-multiline
            />

            <div className="composer-bottom-row">
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <div className="model-chip-pill">
                  <span className="model-chip-dot" />
                  <span>Dr. MedNama · answers from your textbooks</span>
                  <ChevronDown size={10} style={{ marginLeft: "4px" }} />
                </div>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <span className="keyboard-send-hint">⏎ to send</span>
                <button
                  className="send-btn"
                  onClick={() => sendQuery(inputValue)}
                  disabled={isSearching || !inputValue.trim()}
                  aria-label="Send question"
                >
                  {isSearching ? (
                    <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
                  ) : (
                    <Send size={14} />
                  )}
                </button>
              </div>
            </div>
          </div>

          <p className="input-hint" style={{ marginTop: "8px" }}>
            Dr. MedNama can make mistakes. Verify textbook sources.
          </p>
        </div>
      </div>
    </div>
  );
}
