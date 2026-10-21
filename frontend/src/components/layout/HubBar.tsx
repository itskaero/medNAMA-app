"use client";

import React from "react";
import { ArrowLeft } from "lucide-react";
import { HUBS, hubOf, visibleTabs, ViewId, VIEW_LABEL } from "@/lib/nav";
import type { NavEntry } from "@/hooks/useNav";

/** The tab strip of the current hub (Today · Study · Practise · Review · More · Admin), with a Back link when you
 *  arrived from another hub or from a screen that isn't a tab. */
export default function HubBar({ view, isAdmin, clinical = true, previous, onNavigate, onBack, hidden }: {
  view: ViewId;
  isAdmin: boolean;
  /** the learner sits a clinical paper (shows TOACS) */
  clinical?: boolean;
  previous: NavEntry | null;
  onNavigate: (v: ViewId) => void;
  onBack: () => void;
  hidden?: boolean;
}) {
  if (hidden) return null;
  const hub = hubOf(view);
  if (hub.admin && !isAdmin) return null;
  const tabs = visibleTabs(hub, clinical, view);
  const isTab = tabs.some((t) => t.view === view);
  const showBack = previous && (hubOf(previous.view).id !== hub.id || !isTab) && previous.view !== view;
  return (
    <nav className="hubbar" aria-label={`${hub.label} sections`}>
      {showBack ? (
        <button type="button" className="hubbar-back" onClick={onBack}>
          <ArrowLeft size={14} /> {VIEW_LABEL[previous!.view] ?? "Back"}
        </button>
      ) : null}
      <span className="hubbar-title">{hub.label}</span>
      <div className="hubbar-tabs" role="tablist">
        {tabs.map((t) => (
          <button key={t.view} type="button" role="tab" aria-selected={t.view === view}
            className={`hubbar-tab ${t.view === view ? "active" : ""}`} title={t.title} onClick={() => onNavigate(t.view)}>
            {t.label}
          </button>
        ))}
      </div>
    </nav>
  );
}

export { HUBS };
