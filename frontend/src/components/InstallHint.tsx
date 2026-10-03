"use client";

import React, { useEffect, useState } from "react";
import { Download, X } from "lucide-react";

const KEY = "mednama_install_hint";

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

/** Shown once: install medNAMA as an app (Chrome/Android prompt; Add to Home Screen on iPhone). Needs https. */
export default function InstallHint() {
  const [evt, setEvt] = useState<InstallPromptEvent | null>(null);
  const [ios, setIos] = useState(false);
  const [show, setShow] = useState(false);

  useEffect(() => {
    let seen = false;
    try { seen = localStorage.getItem(KEY) === "1"; } catch { /* storage unavailable */ }
    const standalone = window.matchMedia?.("(display-mode: standalone)").matches || (navigator as unknown as { standalone?: boolean }).standalone;
    if (seen || standalone || !window.isSecureContext) return;
    const onPrompt = (e: Event) => { e.preventDefault(); setEvt(e as InstallPromptEvent); setShow(true); };
    window.addEventListener("beforeinstallprompt", onPrompt);
    // iPhone Safari has no install prompt; tell students how once.
    if (/iphone|ipad|ipod/i.test(navigator.userAgent)) { setIos(true); setShow(true); }
    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, []);

  const close = () => {
    setShow(false);
    try { localStorage.setItem(KEY, "1"); } catch { /* storage unavailable */ }
  };
  if (!show) return null;
  return (
    <div className="install-hint" role="status">
      <Download size={16} />
      <span>{ios ? "Install medNAMA: tap Share, then “Add to Home Screen”." : "Install medNAMA on this device: it opens like an app and keeps an offline pack."}</span>
      {evt ? (
        <button className="btn-primary" onClick={async () => { await evt.prompt(); await evt.userChoice.catch(() => null); close(); }}>Install</button>
      ) : null}
      <button className="qp-icon-btn" onClick={close} aria-label="Dismiss"><X size={14} /></button>
    </div>
  );
}
