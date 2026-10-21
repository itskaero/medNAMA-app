"use client";

import React, { useEffect, useRef, useState } from "react";
import { Mic, MicOff } from "lucide-react";
import { toast } from "sonner";

/**
 * Speak instead of typing: the browser's own speech recognition (Chrome, Edge, Android, Safari 14.5+), so there is
 * no server cost and no audio leaves the browser's speech service. Hidden where the browser has none (Firefox).
 * English by default; Urdu (ur-PK) is offered because students often phrase questions that way.
 */

// The Web Speech API is not in TypeScript's DOM types.
type Recognition = {
  lang: string; interimResults: boolean; continuous: boolean;
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void; stop: () => void; abort: () => void;
};
type RecognitionCtor = new () => Recognition;

const LANG_KEY = "mednama_voice_lang";
const LANGS = [{ value: "en-GB", label: "EN" }, { value: "ur-PK", label: "اردو" }];

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function VoiceButton({ value, onChange, disabled, className }: {
  value: string;
  onChange: (text: string) => void;
  disabled?: boolean;
  className?: string;
}) {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [lang, setLang] = useState("en-GB");
  const rec = useRef<Recognition | null>(null);
  const base = useRef("");

  useEffect(() => {
    // Detected after mount: the server render has no window, and a mismatch would break hydration.
    const id = setTimeout(() => {
      setSupported(!!recognitionCtor());
      try { setLang(localStorage.getItem(LANG_KEY) || "en-GB"); } catch { /* storage unavailable */ }
    }, 0);
    return () => { clearTimeout(id); rec.current?.abort(); };
  }, []);

  if (!supported) return null;

  const stop = () => { rec.current?.stop(); };
  const start = () => {
    const Ctor = recognitionCtor();
    if (!Ctor) return;
    const r = new Ctor();
    r.lang = lang;
    r.interimResults = true;
    r.continuous = false;
    base.current = value ? `${value.trimEnd()} ` : "";
    r.onresult = (e) => {
      let text = "";
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      onChange(base.current + text);
    };
    r.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") toast.error("Allow the microphone for this site to speak your question.");
      else if (e.error !== "no-speech" && e.error !== "aborted") toast.error("Could not hear that. Try again.");
    };
    r.onend = () => setListening(false);
    rec.current = r;
    try { r.start(); setListening(true); } catch { setListening(false); }
  };
  const toggleLang = () => {
    const next = lang === "en-GB" ? "ur-PK" : "en-GB";
    setLang(next);
    try { localStorage.setItem(LANG_KEY, next); } catch { /* storage unavailable */ }
  };

  return (
    <span className={`voice ${className ?? ""}`}>
      <button type="button" className="voice-lang" onClick={toggleLang} disabled={listening}
        title="Language you will speak in" aria-label={`Speech language: ${lang === "en-GB" ? "English" : "Urdu"}. Switch`}>
        {LANGS.find((l) => l.value === lang)?.label}
      </button>
      <button type="button" className="voice-mic" data-on={listening || undefined} onClick={listening ? stop : start}
        disabled={disabled} aria-pressed={listening} aria-label={listening ? "Stop listening" : "Speak your question"}
        title={listening ? "Listening… tap to stop" : "Speak your question"}>
        {listening ? <MicOff size={15} /> : <Mic size={15} />}
      </button>
    </span>
  );
}
