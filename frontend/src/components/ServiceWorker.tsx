"use client";

import { useEffect } from "react";

/** Registers /sw.js so medNAMA can be installed and open with no connection. Browsers allow service workers
 *  only on https or localhost; on a plain-http LAN address this does nothing (the Offline pack still works). */
export function ServiceWorker() {
  useEffect(() => {
    if (typeof window === "undefined" || !window.isSecureContext || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* the app works without it */
    });
  }, []);
  return null;
}
