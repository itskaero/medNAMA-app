"use client";

import React from "react";
import {
  Stethoscope,
  LogOut,
  BookMarked,
  Bookmark,
  GraduationCap,
  TrendingUp,
  LayoutDashboard,
  Moon,
  Sun,
  Compass,
  Sunset,
  NotebookPen,
  Flame,
  Scale,
  Users,
  KeyRound,
  CloudOff,
  Library,
  Swords,
  Trophy,
  GitCompareArrows,
  Zap,
  History,
  BookOpenCheck,
} from "lucide-react";
import { Book } from "@/types";

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
  booksError?: string | null;
  onRetryBooks?: () => void;
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
  onChangePassword?: () => void;
}

interface NavItem { view: string; label: string; icon: React.ReactNode; active: string[]; title?: string }

/** Sidebar: today, ways to practise, ways to learn, review, and (admins) tools. The textbook list lives on its
 *  own Library page: squeezed under a long menu it shrank to nothing on phones. */
function navGroups(isAdmin: boolean, bookCount: number): { title: string; items: NavItem[] }[] {
  const item = (view: string, label: string, icon: React.ReactNode, extra: Partial<NavItem> = {}): NavItem =>
    ({ view, label, icon, active: [view], ...extra });
  return [
    { title: "Today", items: [
      item("dashboard", "Dashboard", <LayoutDashboard size={14} />),
      item("daily", "Daily Dose", <Flame size={14} style={{ color: "#f59e0b" }} />),
    ] },
    { title: "Practise", items: [
      item("quiz", "Practice", <GraduationCap size={14} />, { title: "Build a session: subjects and topics from past papers and the bank, any difficulty" }),
      item("pastpapers", "Past papers", <History size={14} />, { active: ["pastpapers", "paper"] }),
      item("mock", "Weekly mock", <Trophy size={14} />),
      item("duel", "Challenge a friend", <Swords size={14} />),
      item("offline", "Offline pack", <CloudOff size={14} />, { title: "Download questions to answer with no connection; answers sync when you are back" }),
      item("sprint", "Final sprint", <Zap size={14} />, { title: "Opens in the last 7 days before your exam" }),
    ] },
    { title: "Learn", items: [
      item("chat", "Dr MedNama", <Stethoscope size={14} style={{ color: "var(--sky)" }} />, { title: "Ask, or be tutored, from your textbooks" }),
      item("revise", "Revise from books", <BookOpenCheck size={14} style={{ color: "var(--sea-green)" }} />,
        { title: "A one-page summary of a topic, written from your own books" }),
      item("lookalikes", "Look-alikes", <GitCompareArrows size={14} />),
      item("study", "Study Corner", <NotebookPen size={14} />),
      item("library", `Library${bookCount ? ` · ${bookCount}` : ""}`, <Library size={14} />, { title: "The textbooks every answer comes from" }),
    ] },
    { title: "Review", items: [
      item("stats", "Stats", <TrendingUp size={14} />),
      item("bookmarks", "Bookmarks", <Bookmark size={14} />),
      item("mcq-bank", "MCQ Bank", <BookMarked size={14} />),
    ] },
    ...(isAdmin ? [{ title: "Admin", items: [
      item("users", "Users", <Users size={14} />, { title: "Accounts, invite codes and password resets" }),
      item("referee", "Answer-Key Referee", <Scale size={14} />, { title: "Check recall answers and MCQ keys against the textbooks" }),
    ] }] : []),
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
  booksError,
  onRetryBooks,
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
  onChangePassword,
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
        {onChangePassword ? (
          <button className="btn-logout" onClick={onChangePassword} aria-label="Change password" title="Change password">
            <KeyRound size={12} style={{ display: "inline" }} />
          </button>
        ) : null}
        <button className="btn-logout" onClick={handleLogout} aria-label="Sign out">
          <LogOut size={12} style={{ display: "inline", marginRight: 4 }} />
          Sign out
        </button>
      </div>

      {/* Workspace navigation, in groups */}
      <nav aria-label="Main" style={{ padding: "var(--sp-2) var(--sp-3)", display: "flex", flexDirection: "column", gap: "2px", borderBottom: "1px solid var(--border)" }}>
        {navGroups(isAdmin, books.length).map((group) => (
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
