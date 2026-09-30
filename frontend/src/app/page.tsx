"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { Stethoscope, Menu } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

import { Figure } from "../types";
import { API } from "@/lib/constants";

// ─── Hooks ──────────────────────────────────────────────────────────────────
import { useTheme } from "@/hooks/useTheme";
import { useAuth } from "@/hooks/useAuth";
import { useSidebar } from "@/hooks/useSidebar";
import { useLibrary } from "@/hooks/useLibrary";
import { useStats } from "@/hooks/useStats";
import { useConversations } from "@/hooks/useConversations";
import { useChat } from "@/hooks/useChat";
import { useBookmarks } from "@/hooks/useBookmarks";
import { useMCQBank } from "@/hooks/useMCQBank";
import { useQuiz } from "@/hooks/useQuiz";
import { useStudy } from "@/hooks/useStudy";

// ─── Layout Components ───────────────────────────────────────────────────────
import AuthCard from "@/components/layout/AuthCard";
import AppSidebar from "@/components/layout/AppSidebar";
import LightboxModal from "@/components/layout/LightboxModal";
import MinimizedChatWidget from "@/components/layout/MinimizedChatWidget";

// ─── View Components ─────────────────────────────────────────────────────────
import DashboardView from "@/components/views/DashboardView";
import MCQBankView from "@/components/views/MCQBankView";
import BookmarksView from "@/components/views/BookmarksView";
import StatsView from "@/components/views/StatsView";
import QuizView from "@/components/views/QuizView";
import ChatView from "@/components/views/ChatView";
import StudyView from "@/components/views/StudyView";
import DailyDoseView from "@/components/views/DailyDoseView";
import RefereeView from "@/components/views/RefereeView";
import UsersView from "@/components/views/UsersView";
import OfflineView from "@/components/views/OfflineView";
import ChangePasswordDialog from "@/components/ChangePasswordDialog";
import DuelView from "@/components/views/DuelView";
import WeeklyMockView from "@/components/views/WeeklyMockView";
import LookalikesView from "@/components/views/LookalikesView";
import PastPapersView from "@/components/views/PastPapersView";
import RapidReviewView, { ReviewScope } from "@/components/views/RapidReviewView";
import ReviseView from "@/components/views/ReviseView";
import PageViewer from "@/components/PageViewer";

// ─── Main Component ──────────────────────────────────────────────────────────
export default function Home() {
  // ── Shared navigation state ────────────────────────────────────────────────
  const [activeView, setActiveView] = useState<
    "dashboard" | "chat" | "quiz" | "mcq-bank" | "bookmarks" | "stats" | "study" | "daily" | "referee" | "users" | "offline" | "duel" | "mock" | "lookalikes" | "sprint" | "pastpapers" | "paper" | "review" | "revise"
  >("dashboard");
  const [selectedTopic, setSelectedTopic] = useState<any>(null);
  const [timedMockId, setTimedMockId] = useState<number | null>(null);
  const [reviewScope, setReviewScope] = useState<ReviewScope | null>(null);
  const openRapidReview = (scope: ReviewScope) => {
    setReviewScope(scope);
    setActiveView("review");
  };
  const [isChatMinimized, setIsChatMinimized] = useState(false);
  const [quickReplyVal, setQuickReplyVal] = useState("");
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [openAccordionCategory, setOpenAccordionCategory] = useState<string | null>(null);
  const [lightboxFig, setLightboxFig] = useState<Figure | null>(null);
  // F2 — chat source scope
  const [chatScope, setChatScope] = useState<{ book_id: number | null; chapter: string | null }>({
    book_id: null,
    chapter: null,
  });
  const [chatChapters, setChatChapters] = useState<string[]>([]);
  // Shared duel links open straight into the duel: /?duel=CODE
  const [duelCode, setDuelCode] = useState<string | null>(null);
  useEffect(() => {
    // Read after mount (not during render) so server and client markup match.
    const timer = setTimeout(() => {
      const code = new URLSearchParams(window.location.search).get("duel");
      if (code) {
        setDuelCode(code);
        setActiveView("duel");
      }
    }, 0);
    return () => clearTimeout(timer);
  }, []);
  // Study level (answer depth). Per-browser preference; null = general exam prep.
  const [chatLevel, setChatLevelState] = useState<string | null>(null);
  useEffect(() => {
    try {
      setChatLevelState(localStorage.getItem("mednama_level"));
    } catch {
      /* storage unavailable */
    }
  }, []);
  const setChatLevel = useCallback((level: string | null) => {
    setChatLevelState(level);
    try {
      if (level) localStorage.setItem("mednama_level", level);
      else localStorage.removeItem("mednama_level");
    } catch {
      /* storage unavailable */
    }
  }, []);

  // ── Refs ───────────────────────────────────────────────────────────────────
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const lastFetchedFilters = useRef({ category: "all", search: "" });

  // ── Hooks ──────────────────────────────────────────────────────────────────
  const { theme, setTheme } = useTheme();

  const {
    token,
    username,
    role,
    isAuthLoading,
    authError,
    authUsername,
    setAuthUsername,
    authPassword,
    setAuthPassword,
    authRole,
    setAuthRole,
    isRegisterMode,
    setIsRegisterMode,
    authInvite,
    setAuthInvite,
    registrationMode,
    mounted,
    handleAuthSubmit,
    handleLogout,
  } = useAuth();

  const {
    sidebarWidth,
    isSidebarResizing,
    isSidebarHovered,
    setIsSidebarHovered,
    isSidebarLocked,
    setIsSidebarLocked,
    startResizing,
  } = useSidebar();

  const [showPasswordDialog, setShowPasswordDialog] = useState(false);
  // Opened with no connection (an installed app on the bus): go straight to the Offline pack.
  useEffect(() => {
    if (typeof navigator !== "undefined" && !navigator.onLine) setActiveView("offline");
  }, []);

  // getHeaders — needed by multiple hooks
  const getHeaders = useCallback((): HeadersInit => {
    const t = localStorage.getItem("token") || token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [token]);

  const { stats, isLoadingStats, isLoadingDetailedStats, detailedStats, fetchStats, fetchDetailedStats } =
    useStats({ token, getHeaders, activeView });

  const {
    books,
    setBooks,
    isLoadingBooks,
    booksError,
    uploading,
    uploadError,
    fileRef,
    fetchBooks,
    handleFileUpload,
    handleDeleteBook,
    preloadDashboardData,
  } = useLibrary({ token, getHeaders, handleLogout });

  const {
    conversations,
    activeConversationId,
    setActiveConversationId,
    isLoadingConversations,
    messages,
    setMessages,
    fetchConversations,
    handleSelectConversation,
    handleNewChat,
    handleDeleteConversation,
  } = useConversations({ token });

  const { inputValue, setInputValue, isSearching, sendQuery, handleKeyDown } = useChat({
    token,
    activeConversationId,
    setActiveConversationId,
    fetchConversations,
    messagesEndRef,
    inputRef,
    messages,
    setMessages,
    scope: chatScope,
    level: chatLevel,
  });

  // F2 — fetch distinct chapter headings when the user scopes chat to a book
  const fetchChatChapters = useCallback(
    (bookId: number) => {
      const savedToken = token || localStorage.getItem("token");
      if (!savedToken) return;
      setChatChapters([]);
      fetch(`${API}/api/books/${bookId}/chapters`, {
        headers: { Authorization: `Bearer ${savedToken}` },
        credentials: "include",
      })
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error("Failed to load chapters"))))
        .then((data) => setChatChapters(data.chapters || []))
        .catch((err) => console.error(err));
    },
    [token]
  );

  const {
    bookmarkedMcqs,
    bookmarkedConcepts,
    isLoadingBookmarks,
    bookmarksActiveTab,
    setBookmarksActiveTab,
    fetchBookmarks,
    toggleBookmarkMCQ: toggleBookmarkMCQBase,
    handleCreateConceptBookmark,
    handleDeleteConceptBookmark,
  } = useBookmarks({ token });

  const {
    mcqsList,
    setMcqsList,
    mcqTotal,
    isLoadingMcqs,
    isLoadingMore,
    loadMoreMcqs,
    mcqSearchText,
    setMcqSearchText,
    mcqFilterCategory,
    setMcqFilterCategory,
    fetchMcqs,
  } = useMCQBank({ token });

  // Bind toggleBookmarkMCQ so it also updates mcqsList
  const toggleBookmarkMCQ = useCallback(
    (mcqId: number) => {
      toggleBookmarkMCQBase(mcqId, setMcqsList);
    },
    [toggleBookmarkMCQBase, setMcqsList]
  );

  const quiz = useQuiz({
    token,
    getHeaders,
    activeView,
    setActiveView,
    selectedTopic,
    stats,
    fetchStats,
  });

  const study = useStudy({ token, getHeaders });

  // ── Derived values ─────────────────────────────────────────────────────────
  const isAdmin = role === "admin";
  const initials = username ? username.slice(0, 2).toUpperCase() : "DR";

  // ── Effects ────────────────────────────────────────────────────────────────

  // Preload dashboard data whenever token changes (login / restore)
  useEffect(() => {
    if (token) {
      preloadDashboardData(fetchStats);
    }
  }, [token]);

  // Load view-specific data when navigation happens
  useEffect(() => {
    const savedToken = token || localStorage.getItem("token");
    if (!savedToken) return;

    if (activeView === "chat") {
      if (conversations.length === 0) {
        fetchConversations();
      }
    } else if (activeView === "mcq-bank") {
      const isFilterChanged =
        lastFetchedFilters.current.category !== mcqFilterCategory ||
        lastFetchedFilters.current.search !== mcqSearchText;

      if (mcqsList.length === 0 || isFilterChanged) {
        fetchMcqs(mcqFilterCategory, mcqSearchText);
        lastFetchedFilters.current = { category: mcqFilterCategory, search: mcqSearchText };
      }
    } else if (activeView === "bookmarks") {
      if (bookmarkedMcqs.length === 0 && bookmarkedConcepts.length === 0) {
        fetchBookmarks();
      }
    } else if (activeView === "stats") {
      if (detailedStats === null) {
        fetchDetailedStats();
      }
    }
  }, [
    activeView,
    token,
    mcqFilterCategory,
    mcqSearchText,
    conversations.length,
    mcqsList.length,
    bookmarkedMcqs.length,
    bookmarkedConcepts.length,
    detailedStats,
    fetchConversations,
    fetchMcqs,
    fetchBookmarks,
    fetchDetailedStats,
  ]);

  // Clear messages/inputValue on logout (token becomes null)
  useEffect(() => {
    if (!token) {
      setMessages([]);
      setInputValue("");
      setBooks([]);
    }
  }, [token]);

  // Persist and restore activeView to survive page refresh
  useEffect(() => {
    const savedView = localStorage.getItem("activeView") as any;
    if (savedView) {
      setActiveView(savedView);
    }
  }, []);

  useEffect(() => {
    localStorage.setItem("activeView", activeView);
  }, [activeView]);

  // ── Render: init screen ────────────────────────────────────────────────────
  if (!mounted || (isAuthLoading && !token)) {
    return (
      <div className="init-screen" aria-label="Initializing medNAMA">
        <div className="init-spinner" role="status" aria-label="Loading" />
        <span className="init-label">Initializing secure session…</span>
      </div>
    );
  }

  // ── Render: auth screen ────────────────────────────────────────────────────
  if (!token) {
    return (
      <AuthCard
        isRegisterMode={isRegisterMode}
        setIsRegisterMode={setIsRegisterMode}
        authError={authError}
        authUsername={authUsername}
        setAuthUsername={setAuthUsername}
        authPassword={authPassword}
        setAuthPassword={setAuthPassword}
        authRole={authRole}
        setAuthRole={setAuthRole}
        isAuthLoading={isAuthLoading}
        handleAuthSubmit={handleAuthSubmit}
        authInvite={authInvite}
        setAuthInvite={setAuthInvite}
        registrationMode={registrationMode}
      />
    );
  }

  // ── renderMainContent ──────────────────────────────────────────────────────
  const renderMainContent = () => {
    if (activeView === "dashboard") {
      return (
        <DashboardView
          username={username}
          stats={stats}
          isLoadingStats={isLoadingStats}
          openAccordionCategory={openAccordionCategory}
          setOpenAccordionCategory={setOpenAccordionCategory}
          setActiveView={setActiveView}
          setSelectedTopic={setSelectedTopic}
          handleReviewPreviousQuiz={quiz.handleReviewPreviousQuiz}
          isAdmin={isAdmin}
          token={token}
          onDoAll={(main, sub, count) =>
            quiz.startQuizWith(
              { categories: [main], sub_categories: [sub], num_questions: 50, prefer_unseen: true },
              `${sub} · all ${count}`,
              "dashboard"
            )
          }
          onRapidReview={(main, sub) => openRapidReview({ label: `${main} · ${sub}`, main, sub, returnTo: "dashboard" })}
        />
      );
    }

    if (activeView === "quiz") {
      return (
        <QuizView
          {...quiz}
          stats={stats}
          bookmarkedMcqs={bookmarkedMcqs}
          token={token}
          isAdmin={isAdmin}
          toggleBookmarkMCQ={toggleBookmarkMCQ}
          setActiveView={setActiveView}
          onFigureClick={setLightboxFig}
          books={books}
          getHeaders={getHeaders}
        />
      );
    }

    if (activeView === "mcq-bank") {
      return (
        <MCQBankView
          stats={stats}
          mcqsList={mcqsList}
          mcqTotal={mcqTotal}
          isLoadingMcqs={isLoadingMcqs}
          isLoadingMore={isLoadingMore}
          onLoadMore={loadMoreMcqs}
          mcqSearchText={mcqSearchText}
          setMcqSearchText={setMcqSearchText}
          mcqFilterCategory={mcqFilterCategory}
          setMcqFilterCategory={setMcqFilterCategory}
          toggleBookmarkMCQ={toggleBookmarkMCQ}
          fetchExplanation={quiz.fetchExplanation}
          explanationMCQId={quiz.explanationMCQId}
          setExplanationMCQId={quiz.setExplanationMCQId}
          explanationData={quiz.explanationData}
          explanationLoading={quiz.explanationLoading}
          explanationError={quiz.explanationError}
          token={token}
          onFigureClick={setLightboxFig}
        />
      );
    }

    if (activeView === "bookmarks") {
      return (
        <BookmarksView
          bookmarkedMcqs={bookmarkedMcqs}
          bookmarkedConcepts={bookmarkedConcepts}
          isLoadingBookmarks={isLoadingBookmarks}
          bookmarksActiveTab={bookmarksActiveTab}
          setBookmarksActiveTab={setBookmarksActiveTab}
          toggleBookmarkMCQ={toggleBookmarkMCQ}
          fetchExplanation={quiz.fetchExplanation}
          handleDeleteConceptBookmark={handleDeleteConceptBookmark}
          explanationMCQId={quiz.explanationMCQId}
          setExplanationMCQId={quiz.setExplanationMCQId}
          explanationData={quiz.explanationData}
          explanationLoading={quiz.explanationLoading}
          explanationError={quiz.explanationError}
          token={token}
          onFigureClick={setLightboxFig}
        />
      );
    }

    if (activeView === "stats") {
      return (
        <StatsView
          token={token}
          onReviewQuiz={quiz.handleReviewPreviousQuiz}
          onOpenPaper={(id) => {
            setTimedMockId(id);
            setActiveView("paper");
          }}
          onPractise={(filters, label) => quiz.startQuizWith(filters, label, "stats")}
        />
      );
    }

    if (activeView === "duel") {
      return <DuelView token={token} initialCode={duelCode} />;
    }

    if (activeView === "referee" && isAdmin) {
      return <RefereeView token={token} />;
    }

    if (activeView === "offline") {
      return <OfflineView getHeaders={getHeaders} username={username} />;
    }

    if (activeView === "users" && isAdmin) {
      return <UsersView getHeaders={getHeaders} username={username} />;
    }

    if (activeView === "daily") {
      return <DailyDoseView key="dose" token={token} onFigureClick={setLightboxFig} />;
    }

    if (activeView === "sprint") {
      return <DailyDoseView key="sprint" mode="sprint" token={token} onFigureClick={setLightboxFig} />;
    }

    if (activeView === "mock") {
      return <WeeklyMockView token={token} />;
    }

    if (activeView === "pastpapers" || (activeView === "paper" && !timedMockId)) {
      return (
        <PastPapersView
          token={token}
          onPractice={(filters, label) => quiz.startQuizWith(filters, label, "pastpapers")}
          onRapidReview={(scope) => openRapidReview({ ...scope, returnTo: "pastpapers" })}
          onOpenPractice={(years) => {
            quiz.setPracticeScope({ sources: ["past"], subjects: [], topics: [], years });
            setActiveView("quiz");
          }}
          onTimedPaper={(id) => {
            setTimedMockId(id);
            setActiveView("paper");
          }}
        />
      );
    }

    if (activeView === "review" && reviewScope) {
      return (
        <RapidReviewView
          key={JSON.stringify(reviewScope)}
          token={token}
          scope={reviewScope}
          isAdmin={isAdmin}
          onDrill={(filters, label) => quiz.startQuizWith(filters, label, reviewScope.returnTo)}
          onBack={() => setActiveView((reviewScope.returnTo || "dashboard") as typeof activeView)}
        />
      );
    }

    if (activeView === "revise") {
      return (
        <ReviseView
          token={token}
          isAdmin={isAdmin}
          onBack={() => setActiveView("dashboard")}
          onFigureClick={setLightboxFig}
          onTestMe={(quizSetId, label) => quiz.startAiCustomQuiz(quizSetId)}
        />
      );
    }

    if (activeView === "paper" && timedMockId) {
      return <WeeklyMockView key={`paper-${timedMockId}`} token={token} mockId={timedMockId} onExit={() => setActiveView("pastpapers")} />;
    }

    if (activeView === "lookalikes") {
      return <LookalikesView token={token} onFigureClick={setLightboxFig} />;
    }

    if (activeView === "study") {
      return (
        <StudyView
          token={token}
          notes={study.notes}
          flashcards={study.flashcards}
          savedSheets={study.savedSheets}
          isLoadingNotes={study.isLoadingNotes}
          isLoadingFlashcards={study.isLoadingFlashcards}
          isLoadingSheets={study.isLoadingSheets}
          fetchNotes={study.fetchNotes}
          fetchFlashcards={study.fetchFlashcards}
          fetchSheets={study.fetchSheets}
          createNote={study.createNote}
          updateNote={study.updateNote}
          deleteNote={study.deleteNote}
          createFlashcard={study.createFlashcard}
          updateFlashcard={study.updateFlashcard}
          deleteFlashcard={study.deleteFlashcard}
          reviewFlashcard={study.reviewFlashcard}
          deleteSheet={study.deleteSheet}
          onOpenSheet={(scope) => {
            try {
              localStorage.setItem("mednama_revise_scope", JSON.stringify(scope));
              localStorage.setItem("mednama_revise_open", "1");
            } catch {
              /* storage unavailable */
            }
            setActiveView("revise");
          }}
          onWriteSheet={() => setActiveView("revise")}
        />
      );
    }

    // Default: Chat view
    return (
      <ChatView
        messages={messages}
        inputValue={inputValue}
        setInputValue={setInputValue}
        isSearching={isSearching}
        sendQuery={sendQuery}
        handleKeyDown={handleKeyDown}
        conversations={conversations}
        activeConversationId={activeConversationId}
        isLoadingConversations={isLoadingConversations}
        handleSelectConversation={handleSelectConversation}
        handleNewChat={handleNewChat}
        handleDeleteConversation={handleDeleteConversation}
        isSidebarLocked={isSidebarLocked}
        setIsSidebarLocked={setIsSidebarLocked}
        isSidebarHovered={isSidebarHovered}
        setIsSidebarHovered={setIsSidebarHovered}
        isSidebarResizing={isSidebarResizing}
        sidebarWidth={sidebarWidth}
        startResizing={startResizing}
        setIsChatMinimized={setIsChatMinimized}
        setActiveView={setActiveView}
        token={token}
        messagesEndRef={messagesEndRef}
        inputRef={inputRef}
        onFigureClick={setLightboxFig}
        handleCreateConceptBookmark={handleCreateConceptBookmark}
        books={books}
        scope={chatScope}
        setScope={setChatScope}
        level={chatLevel}
        setLevel={setChatLevel}
        chapters={chatChapters}
        fetchChapters={fetchChatChapters}
      />
    );
  };

  // ── Render: main app ───────────────────────────────────────────────────────
  return (
    <div className="app-shell">
      {/* Sticky Mobile Header */}
      <header className="mobile-header">
        <button
          className="mobile-menu-btn"
          onClick={() => setMobileMenuOpen(true)}
          aria-label="Open navigation menu"
        >
          <Menu size={20} />
        </button>

        <div
          style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}
          onClick={() => {
            setActiveView("dashboard");
            setMobileMenuOpen(false);
          }}
        >
          <Stethoscope size={16} style={{ color: "var(--teal)" }} />
          <span className="brand-name" style={{ fontSize: "1.1rem" }}>
            med<span>NAMA</span>
          </span>
        </div>

        <div className="avatar" style={{ width: "30px", height: "30px", fontSize: "0.75rem" }} aria-hidden>
          {initials}
        </div>
      </header>

      {/* Backdrop overlay for mobile menu */}
      {mobileMenuOpen && (
        <div className="mobile-sidebar-backdrop" onClick={() => setMobileMenuOpen(false)} />
      )}

      {/* ── Sidebar ── */}
      <AppSidebar
        activeView={activeView}
        setActiveView={setActiveView}
        mobileMenuOpen={mobileMenuOpen}
        setMobileMenuOpen={setMobileMenuOpen}
        username={username}
        role={role}
        isAdmin={isAdmin}
        books={books}
        isLoadingBooks={isLoadingBooks}
        booksError={booksError}
        onRetryBooks={() => fetchBooks(true)}
        uploading={uploading}
        uploadError={uploadError}
        theme={theme}
        setTheme={setTheme}
        handleLogout={handleLogout}
        handleFileUpload={handleFileUpload}
        handleDeleteBook={handleDeleteBook}
        fileRef={fileRef}
        setSelectedTopic={setSelectedTopic}
        fetchMcqs={fetchMcqs}
        fetchBookmarks={fetchBookmarks}
        fetchDetailedStats={fetchDetailedStats}
        mcqFilterCategory={mcqFilterCategory}
        mcqSearchText={mcqSearchText}
        onChangePassword={() => setShowPasswordDialog(true)}
      />
      {showPasswordDialog ? <ChangePasswordDialog getHeaders={getHeaders} onClose={() => setShowPasswordDialog(false)} /> : null}

      {/* ── Main content ── */}
      <main className="main" aria-label="Medical knowledge assistant">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeView}
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -15 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
            style={{ width: "100%", height: "100%", overflowY: "auto", overflowX: "hidden" }}
          >
            {renderMainContent()}
          </motion.div>
        </AnimatePresence>
      </main>

      {/* ── Figure lightbox ── */}
      <LightboxModal lightboxFig={lightboxFig} setLightboxFig={setLightboxFig} token={token} />

      {/* ── Book page viewer (opened from any citation's page number) ── */}
      <PageViewer getHeaders={getHeaders} />

      {/* ── Minimized Chat Widget ── */}
      {isChatMinimized && activeView !== "chat" && (
        <MinimizedChatWidget
          messages={messages}
          isSearching={isSearching}
          quickReplyVal={quickReplyVal}
          setQuickReplyVal={setQuickReplyVal}
          sendQuery={sendQuery}
          setIsChatMinimized={setIsChatMinimized}
          setActiveView={setActiveView}
          isChatMinimized={isChatMinimized}
        />
      )}
    </div>
  );
}
