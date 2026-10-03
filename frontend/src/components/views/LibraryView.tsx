"use client";

import React, { useMemo, useState } from "react";
import { BookOpen, Library, Loader2, Search, Trash2, Upload } from "lucide-react";
import { Book } from "@/types";

const ORDER = ["Anatomy", "Physiology", "Biochemistry", "Pathology", "Pharmacology", "Microbiology", "Medicine",
  "Surgery", "Paediatrics", "Gynaecology & Obstetrics", "ENT", "Ophthalmology"];

function byline(b: Book): string {
  const who = (b.authors || []).slice(0, 2).join(", ") + ((b.authors || []).length > 2 ? " et al." : "");
  const n = b.edition || 0;
  const suffix = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] || "th";
  const ed = n ? `${n}${suffix} edition` : "";
  return [who, ed, b.year].filter(Boolean).join(" · ");
}

/** The textbooks Dr MedNama answers from, grouped by subject (was a long list squeezed into the sidebar). */
export default function LibraryView({
  books, isLoading, error, onRetry, isAdmin, uploading, uploadError, onUpload, onDelete, fileRef, onAsk, onRevise,
}: {
  books: Book[];
  isLoading: boolean;
  error?: string | null;
  onRetry?: () => void;
  isAdmin: boolean;
  uploading: boolean;
  uploadError: string | null;
  onUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDelete: (id: number, title: string) => void;
  fileRef: React.RefObject<HTMLInputElement | null>;
  /** Dr MedNama scoped to this book. */
  onAsk?: (bookId: number) => void;
  /** Revise with this book chosen. */
  onRevise?: (bookId: number) => void;
}) {
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const shown = books.filter((b) => !needle || `${b.title} ${b.full_title || ""} ${(b.authors || []).join(" ")} ${b.subject || ""}`
      .toLowerCase().includes(needle));
    const map = new Map<string, Book[]>();
    for (const b of shown) {
      const k = b.subject || "Other";
      map.set(k, [...(map.get(k) || []), b]);
    }
    const rank = (s: string) => (ORDER.indexOf(s) === -1 ? 99 : ORDER.indexOf(s));
    return Array.from(map.entries())
      .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
      .map(([subject, list]) => ({ subject, list: list.sort((x, y) => x.title.localeCompare(y.title)) }));
  }, [books, q]);
  const pages = books.reduce((a, b) => a + (b.total_pages || 0), 0);

  return (
    <div className="dashboard-view" style={{ maxWidth: "1100px", margin: "0 auto", display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
        <div>
          <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "10px" }}><Library size={24} /> Library</h1>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.86rem" }}>
            {books.length} textbooks{pages ? `, ${pages.toLocaleString()} pages` : ""}: every answer, explanation and reference in medNAMA comes from these.
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "6px", border: "1px solid var(--border-light)", borderRadius: "10px", padding: "6px 10px", background: "var(--surface-2)" }}>
            <Search size={14} style={{ color: "var(--text-muted)" }} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a book or author" aria-label="Find a book"
              style={{ border: "none", outline: "none", background: "transparent", fontSize: "0.84rem", color: "var(--text-primary)", width: "200px" }} />
          </label>
          {isAdmin && process.env.NEXT_PUBLIC_CLOUD_MODE !== "true" ? (
            <>
              <input type="file" accept=".pdf" style={{ display: "none" }} ref={fileRef} onChange={onUpload} aria-hidden />
              <button className="btn-primary" onClick={() => fileRef.current?.click()} disabled={uploading} style={{ padding: "8px 14px" }}>
                {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}&nbsp;{uploading ? "Ingesting…" : "Upload textbook"}
              </button>
            </>
          ) : null}
        </div>
      </div>
      {uploadError ? <div className="upload-error" role="alert">{uploadError}</div> : null}

      {error && !books.length && !isLoading ? (
        <div style={{ color: "var(--text-muted)" }}>
          Couldn&apos;t load the library. {error}{" "}
          {onRetry ? <button className="btn-workspace" onClick={onRetry}>Retry</button> : null}
        </div>
      ) : isLoading && !books.length ? (
        <div style={{ color: "var(--text-muted)" }}><Loader2 size={16} className="animate-spin" /> Loading library…</div>
      ) : !books.length ? (
        <div className="books-empty"><BookOpen size={32} /><p>{isAdmin ? "Upload your first PDF textbook." : "No textbooks yet."}</p></div>
      ) : (
        groups.map((g) => (
          <section key={g.subject} aria-label={g.subject}>
            <h2 style={{ fontSize: "0.72rem", fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-muted)", margin: "0 0 8px" }}>
              {g.subject} <span style={{ fontWeight: 500 }}>· {g.list.length}</span>
            </h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: "10px" }}>
              {g.list.map((b) => (
                <div key={b.id} style={{ position: "relative", padding: "12px 14px", borderRadius: "12px", border: "1px solid var(--border-light)", background: "var(--surface-2, var(--surface-3))", display: "flex", gap: "10px" }}>
                  <div className="book-icon" aria-hidden style={{ flexShrink: 0 }}><BookOpen size={14} /></div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontWeight: 600, fontSize: "0.88rem", lineHeight: 1.3 }} title={b.full_title || b.title}>{b.title}</div>
                    {byline(b) ? <div style={{ fontSize: "0.74rem", color: "var(--text-secondary)", marginTop: "2px" }}>{byline(b)}</div> : null}
                    <div style={{ display: "flex", gap: "8px", alignItems: "center", marginTop: "6px", fontSize: "0.72rem", color: "var(--text-muted)" }}>
                      {b.stalled ? (
                        <span className="status-pill" style={{ color: "#b45309", background: "rgba(245,158,11,0.12)" }}>stalled</span>
                      ) : b.status !== "ready" ? (
                        <span className={`status-pill ${b.status}`}>
                          <span className={`status-dot ${b.status === "processing" || b.status === "pending" ? "pulsing" : ""}`} aria-hidden />{b.status}
                        </span>
                      ) : null}
                      {b.total_pages ? <span>{b.total_pages.toLocaleString()} pages</span> : null}
                    </div>
                    {b.status === "ready" && (onAsk || onRevise) ? (
                      <div style={{ display: "flex", gap: "6px", marginTop: "8px", flexWrap: "wrap" }}>
                        {onAsk ? <button className="btn-workspace" style={{ padding: "3px 10px", fontSize: "0.72rem" }} onClick={() => onAsk(b.id)}>Ask this book</button> : null}
                        {onRevise ? <button className="btn-workspace" style={{ padding: "3px 10px", fontSize: "0.72rem" }} onClick={() => onRevise(b.id)}>Revise a chapter</button> : null}
                      </div>
                    ) : null}
                  </div>
                  {isAdmin ? (
                    <button className="book-delete-btn" style={{ opacity: 0.6 }} onClick={() => onDelete(b.id, b.title)}
                      aria-label={`Remove ${b.title}`} title="Remove book"><Trash2 size={12} /></button>
                  ) : null}
                </div>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}
