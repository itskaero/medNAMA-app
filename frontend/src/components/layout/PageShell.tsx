"use client";

import React from "react";
import { Loader2 } from "lucide-react";

/** One frame for a screen: title, optional subtitle and actions, a standard width, and the usual
 *  loading / error / empty states, so every page starts and behaves the same way. */
export default function PageShell({ title, subtitle, icon, actions, width = "default", loading, error, onRetry, empty, children }: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  width?: "narrow" | "default" | "wide";
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  empty?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className={`page page-${width}`}>
      <header className="page-head">
        <div>
          <h1 className="page-title">{icon}{title}</h1>
          {subtitle ? <p className="page-sub">{subtitle}</p> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </header>
      {loading ? (
        <div className="page-state"><Loader2 size={16} className="animate-spin" /> Loading…</div>
      ) : error ? (
        <div className="page-state page-error">
          {error} {onRetry ? <button type="button" className="btn-workspace" onClick={onRetry}>Try again</button> : null}
        </div>
      ) : empty ? (
        <div className="page-state">{empty}</div>
      ) : children}
    </div>
  );
}
