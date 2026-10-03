"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fromHash, toHash, NavParams, ViewId, ALL_VIEWS } from "@/lib/nav";

export interface NavEntry { view: ViewId; params: NavParams }

/** Navigation with a history stack and the view in the URL hash, so refresh keeps the screen, links can be
 *  shared, the browser Back button works, and "Back" in the app returns to where you came from. */
export function useNav(initial: ViewId = "dashboard") {
  const [cur, setCur] = useState<NavEntry>({ view: initial, params: {} });
  const curRef = useRef(cur);
  const stack = useRef<NavEntry[]>([]);
  const [depth, setDepth] = useState(0);   // re-render when Back becomes (un)available

  const go = useCallback((next: NavEntry, push: boolean) => {
    const prev = curRef.current;
    if (prev.view === next.view && JSON.stringify(prev.params) === JSON.stringify(next.params)) return;
    if (push) {
      stack.current.push(prev);
      if (stack.current.length > 30) stack.current.shift();
    }
    curRef.current = next;
    setCur(next);
    setDepth(stack.current.length);
  }, []);

  // First load: the hash wins, then the last screen used, then the default.
  useEffect(() => {
    const h = fromHash(window.location.hash);
    if (h) { go(h, false); return; }
    try {
      const saved = localStorage.getItem("activeView") as ViewId | null;
      if (saved && ALL_VIEWS.includes(saved) && saved !== "review" && saved !== "paper") go({ view: saved, params: {} }, false);
    } catch { /* storage unavailable */ }
  }, [go]);

  // Browser Back/Forward changes the hash: follow it.
  useEffect(() => {
    const onPop = () => {
      const h = fromHash(window.location.hash);
      if (!h) return;
      const top = stack.current[stack.current.length - 1];
      if (top && top.view === h.view) stack.current.pop();
      curRef.current = h;
      setCur(h);
      setDepth(stack.current.length);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Keep the URL and the last-used screen in step with the current view.
  useEffect(() => {
    const hash = toHash(cur.view, cur.params);
    if (window.location.hash !== hash) window.history.pushState(null, "", hash);
    try { localStorage.setItem("activeView", cur.view); } catch { /* storage unavailable */ }
  }, [cur]);

  const navigate = useCallback((view: ViewId, params: NavParams = {}) => go({ view, params }, true), [go]);

  const back = useCallback((fallback: ViewId = "dashboard") => {
    const prev = stack.current.pop();
    const next = prev ?? { view: fallback, params: {} };
    curRef.current = next;
    setCur(next);
    setDepth(stack.current.length);
  }, []);

  // Drop-in for the old setActiveView(view) used across the views.
  const setActiveView = useCallback((v: ViewId | ((p: ViewId) => ViewId)) => {
    const view = typeof v === "function" ? v(curRef.current.view) : v;
    go({ view, params: {} }, true);
  }, [go]);

  const previous: NavEntry | null = depth > 0 ? stack.current[stack.current.length - 1] : null;

  return { view: cur.view, params: cur.params, navigate, back, previous, setActiveView };
}
