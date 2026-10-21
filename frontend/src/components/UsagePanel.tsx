"use client";

import React, { useCallback, useEffect, useState } from "react";
import { API } from "@/lib/constants";
import { HUBS, VIEW_LABEL, hubOf, type ViewId } from "@/lib/nav";

interface Row { view: string; opens: number; active_days: number }

/** Admin > Content: how often each screen is opened (anonymous counts, backend /api/admin/usage).
 *  For deciding what earns a place in the core four hubs, what stays under More, and what to retire. */
export default function UsagePanel({ getHeaders }: { getHeaders: () => HeadersInit }) {
  const [days, setDays] = useState(30);
  const [rows, setRows] = useState<Row[] | null>(null);
  const load = useCallback(async () => {
    const r = await fetch(`${API}/api/admin/usage?days=${days}`, { headers: getHeaders(), credentials: "include" }).catch(() => null);
    const d = r && r.ok ? await r.json() : null;
    setRows(d ? d.views : []);
  }, [days, getHeaders]);
  useEffect(() => { load(); }, [load]);

  const max = Math.max(1, ...(rows ?? []).map((r) => r.opens));
  const seen = new Set((rows ?? []).map((r) => r.view));
  // Screens nobody opened in the window: the first candidates to drop.
  const unused = HUBS.filter((h) => !h.admin).flatMap((h) => h.tabs.map((t) => t.view)).filter((v) => !seen.has(v));

  return (
    <section className="tos-card" aria-label="Screen usage" style={{ marginTop: "var(--sp-5)" }}>
      <div className="tos-row-between">
        <div>
          <span className="tile-kicker">Screen usage</span>
          <h2 className="tos-h2">Which screens learners open</h2>
          <p className="tile-text">Anonymous: one count per screen per day per browser session, with no user or content kept.</p>
        </div>
        <div className="segmented" role="radiogroup" aria-label="Period">
          {[7, 30, 90].map((d) => (
            <button key={d} type="button" role="radio" aria-checked={days === d} className={`segmented-item ${days === d ? "active" : ""}`}
              onClick={() => setDays(d)}>{d} days</button>
          ))}
        </div>
      </div>
      {rows === null ? <p className="tile-text">Loading…</p> : !rows.length ? <p className="tile-text">No visits counted yet.</p> : (
        <div role="table" aria-label="Opens per screen">
          <div className="usage-row tile-kicker" role="row">
            <span>Screen</span><span /><span className="num">Opens</span><span className="num days">Days used</span>
          </div>
          {rows.map((r) => {
            const v = r.view as ViewId;
            return (
              <div key={r.view} className="usage-row" role="row">
                <span title={r.view}>{VIEW_LABEL[v] ?? r.view} <small className="today-muted">{hubOf(v)?.label}</small></span>
                <div className="plan-bar"><span style={{ width: `${(r.opens / max) * 100}%` }} /></div>
                <span className="num">{r.opens.toLocaleString()}</span>
                <span className="num days">{r.active_days}</span>
              </div>
            );
          })}
        </div>
      )}
      {rows && unused.length ? (
        <p className="tile-text" style={{ marginTop: 10 }}>Not opened in {days} days: {unused.map((v) => VIEW_LABEL[v]).join(", ")}.</p>
      ) : null}
    </section>
  );
}
