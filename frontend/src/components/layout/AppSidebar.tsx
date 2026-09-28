"use client";

import React from "react";
import {
  Stethoscope,
  LogOut,
  Upload,
  BookOpen,
  BookMarked,
  Bookmark,
  GraduationCap,
  TrendingUp,
  LayoutDashboard,
  MessageSquare,
  Loader2,
  Moon,
  Sun,
  Compass,
  Sunset,
  Sparkles,
  NotebookPen,
  Flame,
  Scale,
  Swords,
  Trophy,
  GitCompareArrows,
  Zap,
  History,
  BookOpenCheck,
} from "lucide-react";
import { Book } from "@/types";
import { BookItem } from "@/components";

interface AppSidebarProps {
  activeView: string;
  setActiveView: (view: any) => void;
  mobileMenuOpen: boolean;
  setMobileMenuOpen: (v: boolean) => void;
  username: string | null;
  role: string | null;
  isAdmin: boolean;
  books: Book[];
  isLoadingBooks: boolean;
  uploading: boolean;
  uploadError: string | null;
  theme: "dark" | "light" | "balanced" | "warm";
  setTheme: (v: "dark" | "light" | "balanced" | "warm") => void;
  handleLogout: () => void;
  handleFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  handleDeleteBook: (bookId: number, title: string) => void;
  fileRef: React.RefObject<HTMLInputElement | null>;
  setSelectedTopic: (topic: any) => void;
  fetchMcqs: (category?: string, search?: string) => void;
  fetchBookmarks: () => void;
  fetchDetailedStats: () => void;
  mcqFilterCategory: string;
  mcqSearchText: string;
}

interface NavItem { view: string; label: string; icon: React.ReactNode; active: string[]; title?: string }

/** Sidebar: what to do today, ways to practise, ways to learn, and progress / tools. */
function navGroups(isAdmin: boolean): { title: string; items: NavItem[] }[] {
  const item = (view: string, label: string, icon: React.ReactNode, extra: Partial<NavItem> = {}): NavItem =>
    ({ view, label, icon, active: [view], ...extra });
  return [
    { title: "Today", items: [
      item("dashboard", "Dashboard", <LayoutDashboard size={14} />),
      item("daily", "Daily Dose", <Flame size={14} style={{ color: "#f59e0b" }} />),
    ] },
    { title: "Practice", items: [
      item("pastpapers", "Past papers", <History size={14} />, { active: ["pastpapers", "paper"] }),
      item("quiz", "Mock Builder", <GraduationCap size={14} />),
      item("mock", "Weekly mock", <Trophy size={14} />),
      item("duel", "Challenge a friend", <Swords size={14} />),
    ] },
    { title: "Learn", items: [
      item("chat", "Discuss with Dr MedNama", <Stethoscope size={14} style={{ color: "var(--sky)" }} />),
      item("revise", "Revise from books", <BookOpenCheck size={14} style={{ color: "var(--sea-green)" }} />,
        { title: "A one-page summary of a topic, written from your own books" }),
      item("lookalikes", "Look-alikes", <GitCompareArrows size={14} />),
      item("sprint", "Final sprint", <Zap size={14} />, { title: "Opens in the last 7 days before your exam" }),
      item("study", "Study Corner", <NotebookPen size={14} />),
      item("bookmarks", "Bookmarks", <Bookmark size={14} />),
    ] },
    { title: "Progress & tools", items: [
      item("stats", "Stats", <TrendingUp size={14} />),
      item("mcq-bank", "MCQ Bank", <BookMarked size={14} />),
      ...(isAdmin ? [item("referee", "Answer-Key Referee", <Scale size={14} />,
        { title: "Admin: check recall answers and MCQ keys against the textbooks" })] : []),
    ] },
  ];
}

export default function AppSidebar({
  activeView,
  setActiveView,
  mobileMenuOpen,
  setMobileMenuOpen,
  username,
  role,
  isAdmin,
  books,
  isLoadingBooks,
  uploading,
  uploadError,
  theme,
  setTheme,
  handleLogout,
  handleFileUpload,
  handleDeleteBook,
  fileRef,
  setSelectedTopic,
  fetchMcqs,
  fetchBookmarks,
  fetchDetailedStats,
  mcqFilterCategory,
  mcqSearchText,
}: AppSidebarProps) {
  const initials = username ? username.slice(0, 2).toUpperCase() : "DR";

  return (
    <aside className={`sidebar ${mobileMenuOpen ? "mobile-open" : ""}`} aria-label="Navigation & Library">
      {/* Brand */}
      <div
        className="sidebar-brand"
        style={{ cursor: "pointer" }}
        onClick={() => {
          setActiveView("dashboard");
          setMobileMenuOpen(false);
        }}
      >
        <div className="brand-logo" aria-hidden>
          <Stethoscope size={18} />
        </div>
        <span className="brand-name">
          med<span>NAMA</span>
        </span>
      </div>

      {/* Profile */}
      <div className="sidebar-profile" style={{ justifyContent: "space-between" }}>
        <div
          className="user-handle-pill"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "6px",
            padding: "4px 10px 4px 6px",
            background: "var(--surface-2)",
            border: "1px solid var(--border-light)",
            borderRadius: "var(--r-full)",
            boxShadow: "0 2px 8px rgba(0, 0, 0, 0.15)",
          }}
        >
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: "20px",
              height: "20px",
              borderRadius: "50%",
              background: "var(--sky-dim)",
              color: "var(--sky)",
              fontSize: "0.72rem",
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
              boxShadow: "0 0 8px var(--sky-glow)",
            }}
          >
            @
          </span>
          <span
            style={{
              fontSize: "0.78rem",
              fontWeight: 600,
              fontFamily: "var(--font-mono)",
              color: "var(--text-primary)",
              letterSpacing: "0.02em",
            }}
          >
            {username}
          </span>
        </div>
        <button className="btn-logout" onClick={handleLogout} aria-label="Sign out">
          <LogOut size={12} style={{ display: "inline", marginRight: 4 }} />
          Sign out
        </button>
      </div>

      {/* Workspace navigation, in four groups */}
      <nav aria-label="Main" style={{ padding: "var(--sp-2) var(--sp-3)", display: "flex", flexDirection: "column", gap: "2px", borderBottom: "1px solid var(--border)" }}>
        {navGroups(isAdmin).map((group) => (
          <div key={group.title} role="group" aria-label={group.title} style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            <div style={{ fontSize: "0.64rem", fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-muted)", padding: "8px 10px 2px" }}>
              {group.title}
            </div>
            {group.items.map((item) => (
              <button
                key={item.view}
                className={`btn-workspace-nav ${item.active.includes(activeView) ? "active" : ""}`}
                title={item.title}
                onClick={() => {
                  if (item.view === "quiz") setSelectedTopic(null);
                  setActiveView(item.view);
                  setMobileMenuOpen(false);
                }}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </div>
        ))}
      </nav>

      {/* Library */}
      <div className="sidebar-section-label" aria-label="Library section">
        Reference Library
      </div>
      <div className="book-list" role="list" aria-label="Uploaded textbooks">
        {isLoadingBooks ? (
          <div style={{ padding: "24px 12px", textAlign: "center", color: "var(--text-muted)" }}>
            <Loader2
              size={20}
              style={{ margin: "0 auto 8px", display: "block", animation: "spin 1s linear infinite" }}
            />
            <span style={{ fontSize: "0.75rem" }}>Loading library…</span>
          </div>
        ) : books.length === 0 ? (
          <div className="books-empty">
            <BookOpen size={32} />
            <p>
              {isAdmin
                ? "Upload your first PDF textbook below."
                : "No textbooks available. Ask your administrator to add books."}
            </p>
          </div>
        ) : (
          books.map((b) => (
            <BookItem key={b.id} book={b} isAdmin={isAdmin} onDelete={handleDeleteBook} />
          ))
        )}
      </div>

      {/* Upload — admin only */}
      {isAdmin && process.env.NEXT_PUBLIC_CLOUD_MODE !== "true" && (
        <div className="sidebar-upload">
          <input
            type="file"
            accept=".pdf"
            style={{ display: "none" }}
            ref={fileRef}
            onChange={handleFileUpload}
            aria-hidden
          />
          <button
            className="upload-btn"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            aria-label="Upload PDF textbook"
          >
            {uploading ? (
              <>
                <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} />
                Ingesting…
              </>
            ) : (
              <>
                <Upload size={15} />
                Upload Textbook
              </>
            )}
          </button>
          {uploadError && (
            <div className="upload-error" role="alert">
              {uploadError}
            </div>
          )}
        </div>
      )}

      {/* Sidebar Theme Switcher Footer */}
      <div
        style={{
          padding: "var(--sp-3) var(--sp-4)",
          borderTop: "1px solid var(--border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "8px",
          background: "var(--surface-1)",
          flexShrink: 0,
        }}
      >
        <button
          className="btn-workspace"
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "8px",
            padding: "8px 12px",
            fontSize: "0.78rem",
          }}
          onClick={() => {
            const themes: Array<"dark" | "light" | "balanced" | "warm"> = [
              "dark",
              "light",
              "balanced",
              "warm",
            ];
            const nextIdx = (themes.indexOf(theme) + 1) % themes.length;
            setTheme(themes[nextIdx]);
          }}
          aria-label="Cycle theme mode"
        >
          {theme === "dark" && <Moon size={13} style={{ color: "var(--teal)" }} />}
          {theme === "light" && <Sun size={13} style={{ color: "var(--teal)" }} />}
          {theme === "balanced" && <Compass size={13} style={{ color: "var(--teal)" }} />}
          {theme === "warm" && <Sunset size={13} style={{ color: "var(--teal)" }} />}
          <span style={{ textTransform: "capitalize", fontWeight: 500 }}>{theme} Mode</span>
        </button>
      </div>
    </aside>
  );
}
