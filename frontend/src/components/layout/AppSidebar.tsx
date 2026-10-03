"use client";

import React from "react";
import {
  Stethoscope,
  LogOut,
  Moon,
  Sun,
  Compass,
  Sunset,
  KeyRound,
  Sun as TodayIcon,
  BookOpenCheck,
  GraduationCap,
  History,
  Shield,
} from "lucide-react";
import { HUBS, hubOf, HubId, ViewId } from "@/lib/nav";

interface AppSidebarProps {
  activeView: ViewId;
  onNavigate: (view: ViewId) => void;
  mobileMenuOpen: boolean;
  setMobileMenuOpen: (v: boolean) => void;
  username: string | null;
  isAdmin: boolean;
  bookCount: number;
  dueCount?: number;
  theme: "dark" | "light" | "balanced" | "warm";
  setTheme: (v: "dark" | "light" | "balanced" | "warm") => void;
  handleLogout: () => void;
  onChangePassword?: () => void;
}

const HUB_ICON: Record<HubId, React.ReactNode> = {
  today: <TodayIcon size={15} style={{ color: "#f59e0b" }} />,
  learn: <BookOpenCheck size={15} style={{ color: "var(--sea-green)" }} />,
  practise: <GraduationCap size={15} style={{ color: "var(--sky)" }} />,
  review: <History size={15} />,
  admin: <Shield size={15} />,
};

/** Sidebar: four hubs (Today, Learn, Practise, Review) plus Admin. The current hub's tabs are listed under it, so
 *  every screen is two clicks away at most; the same tabs sit above the page in the hub bar. */
export default function AppSidebar({
  activeView,
  onNavigate,
  mobileMenuOpen,
  setMobileMenuOpen,
  username,
  isAdmin,
  bookCount,
  dueCount,
  theme,
  setTheme,
  handleLogout,
  onChangePassword,
}: AppSidebarProps) {
  const current = hubOf(activeView).id;
  const lastTab = (hub: HubId): ViewId => {
    try {
      const v = localStorage.getItem(`mednama_hub_${hub}`) as ViewId | null;
      const h = HUBS.find((x) => x.id === hub)!;
      if (v && h.tabs.some((t) => t.view === v)) return v;
      return h.tabs[0].view;
    } catch { return HUBS.find((x) => x.id === hub)!.tabs[0].view; }
  };
  const go = (v: ViewId) => { onNavigate(v); setMobileMenuOpen(false); };
  const initials = username ? username.slice(0, 2).toUpperCase() : "DR";

  return (
    <aside className={`sidebar ${mobileMenuOpen ? "mobile-open" : ""}`} aria-label="Navigation & Library">
      {/* Brand */}
      <div
        className="sidebar-brand"
        style={{ cursor: "pointer" }}
        onClick={() => {
          go("dashboard");
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

      {/* Hubs; the current one lists its tabs */}
      <nav aria-label="Main" className="hub-nav">
        {HUBS.filter((h) => !h.admin || isAdmin).map((h) => {
          const on = h.id === current;
          return (
            <div key={h.id} role="group" aria-label={h.label}>
              <button className={`btn-workspace-nav hub-item ${on ? "active" : ""}`} title={h.blurb}
                aria-expanded={on} onClick={() => go(on ? h.tabs[0].view : lastTab(h.id))}>
                {HUB_ICON[h.id]}
                <span style={{ flex: 1 }}>{h.label}</span>
                {h.id === "today" && dueCount ? <span className="hub-badge" title="Re-tests due">{dueCount}</span> : null}
              </button>
              {on ? (
                <div className="hub-subnav">
                  {h.tabs.map((t) => (
                    <button key={t.view} className={`hub-subitem ${t.view === activeView ? "active" : ""}`} title={t.title}
                      onClick={() => go(t.view)}>
                      {t.label}{t.view === "library" && bookCount ? <span className="hub-count">{bookCount}</span> : null}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
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
