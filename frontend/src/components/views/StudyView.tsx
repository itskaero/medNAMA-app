"use client";

import React, { useState, useEffect } from "react";
import {
  NotebookPen,
  Layers,
  Plus,
  Trash2,
  Pencil,
  Save,
  X,
  Eye,
  Download,
  Loader2,
  RotateCw,
  BookOpenCheck,
  ArrowRight,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Note, Flashcard, SavedSheet, StudyTab } from "@/types";
import { toast } from "sonner";
import { API } from "@/lib/constants";
import { downloadAuthenticatedCSV } from "@/lib/downloadCSV";

interface StudyViewProps {
  token: string | null;
  notes: Note[];
  flashcards: Flashcard[];
  savedSheets: SavedSheet[];
  isLoadingNotes: boolean;
  isLoadingFlashcards: boolean;
  isLoadingSheets: boolean;
  fetchNotes: () => void;
  fetchFlashcards: () => void;
  fetchSheets: () => void;
  createNote: (p: any) => Promise<Note>;
  updateNote: (id: number, p: any) => Promise<void>;
  deleteNote: (id: number) => Promise<void>;
  createFlashcard: (p: any) => Promise<Flashcard>;
  updateFlashcard: (id: number, p: any) => Promise<void>;
  deleteFlashcard: (id: number) => Promise<void>;
  reviewFlashcard: (id: number, rating: number) => Promise<void>;
  deleteSheet: (id: number) => Promise<void>;
  /** Reopen a saved sheet: switches to Revise from books with that scope. */
  onOpenSheet: (scope: { book_ids: number[]; chapter: string | null; topic: string | null; length: "quick" | "full" }) => void;
  /** Jump to the Revise from books builder to write a first sheet. */
  onWriteSheet: () => void;
}

function formatShortDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString([], { month: "short", day: "numeric" }) +
    " · " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function StudyView({
  token,
  notes,
  flashcards,
  savedSheets,
  isLoadingNotes,
  isLoadingFlashcards,
  isLoadingSheets,
  fetchNotes,
  fetchFlashcards,
  fetchSheets,
  createNote,
  updateNote,
  deleteNote,
  createFlashcard,
  updateFlashcard,
  deleteFlashcard,
  reviewFlashcard,
  deleteSheet,
  onOpenSheet,
  onWriteSheet,
}: StudyViewProps) {
  const [activeTab, setActiveTab] = useState<StudyTab>("notes");
  const [showNoteForm, setShowNoteForm] = useState(false);
  const [showCardForm, setShowCardForm] = useState(false);
  const [editingNoteId, setEditingNoteId] = useState<number | null>(null);
  const [editingCardId, setEditingCardId] = useState<number | null>(null);
  const [flipped, setFlipped] = useState<Set<number>>(new Set());

  // Note editor state
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");
  // Card editor state
  const [cardFront, setCardFront] = useState("");
  const [cardBack, setCardBack] = useState("");
  const [cardTopic, setCardTopic] = useState("");

  useEffect(() => {
    if (activeTab === "notes") fetchNotes();
    else if (activeTab === "flashcards") fetchFlashcards();
    else fetchSheets();
  }, [activeTab, fetchNotes, fetchFlashcards, fetchSheets]);

  const startNewNote = () => {
    setEditingNoteId(null);
    setNoteTitle("");
    setNoteContent("");
    setShowNoteForm(true);
  };

  const startEditNote = (n: Note) => {
    setEditingNoteId(n.id);
    setNoteTitle(n.title);
    setNoteContent(n.content);
    setShowNoteForm(true);
  };

  const cancelNoteForm = () => {
    setShowNoteForm(false);
    setEditingNoteId(null);
    setNoteTitle("");
    setNoteContent("");
  };

  const saveNote = async () => {
    if (!noteContent.trim()) {
      toast.error("Note content cannot be empty.");
      return;
    }
    try {
      if (editingNoteId === null) {
        await createNote({ title: noteTitle.trim() || "Untitled Note", content: noteContent });
        toast.success("Note saved to Study Corner.");
      } else {
        await updateNote(editingNoteId, { title: noteTitle.trim() || "Untitled Note", content: noteContent });
        toast.success("Note updated.");
      }
      cancelNoteForm();
    } catch (err: any) {
      toast.error(err.message || "Failed to save note.");
    }
  };

  const saveCard = async () => {
    if (!cardFront.trim() || !cardBack.trim()) {
      toast.error("Both the front and back of a card are required.");
      return;
    }
    try {
      if (editingCardId === null) {
        await createFlashcard({
          front: cardFront.trim(),
          back: cardBack.trim(),
          topic: cardTopic.trim() || null,
        });
        toast.success("Flashcard added.");
      } else {
        await updateFlashcard(editingCardId, { front: cardFront.trim(), back: cardBack.trim() });
        toast.success("Flashcard updated.");
      }
      setShowCardForm(false);
      setEditingCardId(null);
      setCardFront("");
      setCardBack("");
      setCardTopic("");
    } catch (err: any) {
      toast.error(err.message || "Failed to save flashcard.");
    }
  };

  const toggleFlip = (id: number) => {
    setFlipped((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleReview = async (id: number, rating: number, label: string) => {
    try {
      await reviewFlashcard(id, rating);
      setFlipped((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      toast.success(`Marked "${label}".`);
    } catch (err: any) {
      toast.error(err.message || "Failed to record review.");
    }
  };

  const exportNotes = async () => {
    const ok = await downloadAuthenticatedCSV(`${API}/api/export/notes`, "mednama_notes.csv", token);
    if (ok) toast.success("Notes exported to CSV.");
    else toast.error("Failed to export notes.");
  };

  const formControlsStyle: React.CSSProperties = {
    background: "var(--surface-2)",
    border: "1px solid var(--border-light)",
    borderRadius: "var(--r-md)",
    color: "var(--text-primary)",
    fontSize: "0.82rem",
    width: "100%",
    outline: "none",
  };

  const tabButton = (tab: StudyTab, label: string, Icon: any): React.ReactNode => (
    <button
      type="button"
      onClick={() => setActiveTab(tab)}
      style={{
        display: "flex", alignItems: "center", justifyContent: "center", gap: "8px",
        padding: "10px 12px", fontSize: "0.83rem", fontWeight: activeTab === tab ? 700 : 500,
        color: activeTab === tab ? "var(--sky)" : "var(--text-secondary)",
        background: activeTab === tab ? "var(--surface-1)" : "transparent",
        border: activeTab === tab ? "1px solid var(--sky)" : "1px solid transparent",
        borderRadius: "10px", cursor: "pointer",
        boxShadow: activeTab === tab ? "0 2px 10px rgba(48, 197, 255, 0.18)" : "none",
        transition: "all 0.2s ease",
      }}
    >
      <Icon size={14} style={{ color: activeTab === tab ? "var(--sky)" : "var(--text-muted)" }} />
      <span>{label}</span>
    </button>
  );

  return (
    <div className="dashboard-view" role="region" aria-label="Study corner">
      <div className="dashboard-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 className="dashboard-title">Study Corner</h1>
          <p className="practice-subtitle" style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: "4px" }}>
            Keep personal notes, review with flip cards and reopen the revision sheets you wrote from your books — all private to your account.
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="btn-workspace" onClick={exportNotes} title="Export your notes as CSV">
            <Download size={13} style={{ marginRight: 6 }} />
            Export Notes
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: "6px",
          background: "var(--surface-2)",
          border: "1px solid var(--border-light)",
          borderRadius: "14px",
          padding: "5px",
          marginBottom: "20px",
          width: "min(560px, 100%)",
        }}
      >
        {tabButton("notes", "Notes", NotebookPen)}
        {tabButton("flashcards", "Flashcards", Layers)}
        {tabButton("sheets", "Sheets", BookOpenCheck)}
      </div>

      {/* ── Notes tab ── */}
      {activeTab === "notes" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {showNoteForm ? (
            <div className="workspace-card" style={{ background: "var(--surface-2)", border: "1px solid var(--border-light)", borderRadius: "var(--r-xl)", padding: "14px", display: "flex", flexDirection: "column", gap: "10px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="workspace-badge" style={{ fontWeight: 700 }}>
                  {editingNoteId === null ? "New Note" : "Edit Note"}
                </span>
                <button className="chat-delete-btn" onClick={cancelNoteForm} title="Close editor" aria-label="Close note editor" style={{ position: "static", opacity: 1, transform: "none" }}>
                  <X size={14} />
                </button>
              </div>
              <input
                className="input-box"
                style={formControlsStyle}
                placeholder="Note title (optional)"
                value={noteTitle}
                onChange={(e) => setNoteTitle(e.target.value)}
                aria-label="Note title"
              />
              <textarea
                className="input-box"
                style={{ ...formControlsStyle, minHeight: "110px", resize: "vertical", padding: "10px 12px" }}
                placeholder="Write your study note…"
                value={noteContent}
                onChange={(e) => setNoteContent(e.target.value)}
                aria-label="Note content"
              />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                <button className="btn-workspace" onClick={cancelNoteForm}>Cancel</button>
                <button className="btn-primary" onClick={saveNote} style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                  <Save size={13} />
                  {editingNoteId === null ? "Save Note" : "Save Changes"}
                </button>
              </div>
            </div>
          ) : (
            <button className="upload-btn" onClick={startNewNote} style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: "8px" }}>
              <Plus size={15} />
              New Note
            </button>
          )}

          {isLoadingNotes ? (
            <div className="chat-history-loading" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Loader2 size={14} className="spin" style={{ animation: "spin 1s linear infinite" }} />
              Loading notes…
            </div>
          ) : notes.length === 0 ? (
            <div style={{ padding: "40px 16px", textAlign: "center", color: "var(--text-muted)", fontSize: "0.82rem", border: "1px dashed var(--border)", borderRadius: "var(--r-xl)" }}>
              No notes yet. Capture an insight from the chat or MCQ explanations here.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {notes.map((n) => (
                <div key={n.id} className="workspace-card" style={{ background: "var(--surface-2)", border: "1px solid var(--border-light)", borderRadius: "var(--r-xl)", padding: "14px", display: "flex", flexDirection: "column", gap: "6px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: "0.92rem", color: "var(--text-primary)" }}>{n.title}</div>
                      <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: "2px" }}>
                        Updated {formatShortDate(n.updated_at)}
                        {n.book_title ? ` · ${n.book_title}` : ""}
                        {n.page_number ? ` · p.${n.page_number}` : ""}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
                      <button className="btn-workspace" onClick={() => startEditNote(n)} title="Edit note" aria-label={`Edit note ${n.title}`} style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.7rem" }}>
                        <Pencil size={10} />
                      </button>
                      <button
                        className="btn-workspace"
                        onClick={async () => {
                          if (!window.confirm(`Delete note "${n.title}"?`)) return;
                          try { await deleteNote(n.id); toast.success("Note deleted."); } catch (err: any) { toast.error(err.message || "Failed to delete note."); }
                        }}
                        title="Delete note"
                        aria-label={`Delete note ${n.title}`}
                        style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.7rem", color: "var(--danger, #ef4444)" }}
                      >
                        <Trash2 size={10} />
                      </button>
                    </div>
                  </div>
                  <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
                    {n.content}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Flashcards tab ── */}
      {activeTab === "flashcards" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {showCardForm ? (
            <div className="workspace-card" style={{ background: "var(--surface-2)", border: "1px solid var(--border-light)", borderRadius: "var(--r-xl)", padding: "14px", display: "flex", flexDirection: "column", gap: "10px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="workspace-badge" style={{ fontWeight: 700 }}>
                  {editingCardId === null ? "New Flashcard" : "Edit Flashcard"}
                </span>
                <button className="chat-delete-btn" onClick={() => { setShowCardForm(false); setEditingCardId(null); setCardFront(""); setCardBack(""); setCardTopic(""); }} title="Close editor" aria-label="Close card editor" style={{ position: "static", opacity: 1, transform: "none" }}>
                  <X size={14} />
                </button>
              </div>
              <input className="input-box" style={formControlsStyle} placeholder="Front — question / term / prompt" value={cardFront} onChange={(e) => setCardFront(e.target.value)} aria-label="Card front" />
              <textarea className="input-box" style={{ ...formControlsStyle, minHeight: "80px", resize: "vertical", padding: "10px 12px" }} placeholder="Back — answer / explanation" value={cardBack} onChange={(e) => setCardBack(e.target.value)} aria-label="Card back" />
              <input className="input-box" style={formControlsStyle} placeholder="Topic (e.g. Cardiology) — optional" value={cardTopic} onChange={(e) => setCardTopic(e.target.value)} aria-label="Card topic" />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                <button className="btn-workspace" onClick={() => { setShowCardForm(false); setEditingCardId(null); setCardFront(""); setCardBack(""); setCardTopic(""); }}>Cancel</button>
                <button className="btn-primary" onClick={saveCard} style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                  <Save size={13} />
                  {editingCardId === null ? "Add Card" : "Save Changes"}
                </button>
              </div>
            </div>
          ) : (
            <button className="upload-btn" onClick={() => { setEditingCardId(null); setCardFront(""); setCardBack(""); setCardTopic(""); setShowCardForm(true); }} style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: "8px" }}>
              <Plus size={15} />
              New Flashcard
            </button>
          )}

          {isLoadingFlashcards ? (
            <div className="chat-history-loading" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Loader2 size={14} className="spin" style={{ animation: "spin 1s linear infinite" }} />
              Loading flashcards…
            </div>
          ) : flashcards.length === 0 ? (
            <div style={{ padding: "40px 16px", textAlign: "center", color: "var(--text-muted)", fontSize: "0.82rem", border: "1px dashed var(--border)", borderRadius: "var(--r-xl)" }}>
              No flashcards yet. Add your first card above — click a card to flip, then rate it with Again / Hard / Good / Easy.
            </div>
          ) : (
            <div className="config-sub-pills-list" style={{ display: "flex", flexWrap: "wrap", gap: "10px" }}>
              {flashcards.map((c) => {
                const isFlipped = flipped.has(c.id);
                const isEditing = editingCardId === c.id;
                return (
                  <div
                    key={c.id}
                    style={{
                      width: "min(320px, 100%)",
                      background: "var(--surface-2)",
                      border: "1px solid var(--border-light)",
                      borderRadius: "var(--r-xl)",
                      overflow: "hidden",
                      display: "flex", flexDirection: "column",
                    }}
                  >
                    <AnimatePresence mode="wait" initial={false}>
                      {isEditing ? (
                        <motion.div key={`edit-${c.id}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
                          <div style={{ padding: "12px", display: "flex", flexDirection: "column", gap: "8px" }}>
                            <input className="input-box" style={formControlsStyle} value={cardFront} onChange={(e) => setCardFront(e.target.value)} placeholder="Front" aria-label="Editing card front" />
                            <textarea className="input-box" style={{ ...formControlsStyle, minHeight: "70px", resize: "vertical", padding: "8px 10px" }} value={cardBack} onChange={(e) => setCardBack(e.target.value)} placeholder="Back" aria-label="Editing card back" />
                            <div style={{ display: "flex", justifyContent: "flex-end", gap: "6px" }}>
                              <button className="btn-workspace" onClick={() => setEditingCardId(null)} style={{ fontSize: "0.7rem" }}>Cancel</button>
                              <button
                                className="btn-primary"
                                style={{ fontSize: "0.7rem", padding: "4px 10px" }}
                                onClick={async () => {
                                  if (!cardFront.trim() || !cardBack.trim()) { toast.error("Both sides of a card are required."); return; }
                                  try { await updateFlashcard(c.id, { front: cardFront.trim(), back: cardBack.trim() }); toast.success("Card updated."); setEditingCardId(null); } catch (err: any) { toast.error(err.message || "Failed to update card."); }
                                }}
                              >
                                Save
                              </button>
                            </div>
                          </div>
                        </motion.div>
                      ) : (
                        <motion.div key={`card-${c.id}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
                          <button
                            type="button"
                            onClick={() => toggleFlip(c.id)}
                            title="Click to flip"
                            style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: "6px", width: "100%", padding: "14px", textAlign: "left", background: "var(--surface-1)", border: "none", borderBottom: "1px solid var(--border-light)", cursor: "pointer", minHeight: "90px" }}
                            aria-label="Flip flashcard"
                          >
                            <div style={{ display: "flex", justifyContent: "space-between", width: "100%", alignItems: "center" }}>
                              <span className="workspace-badge" style={{ fontSize: "0.62rem", background: "var(--surface-3)" }}>
                                {isFlipped ? "Back" : "Front"}
                              </span>
                              <RotateCw size={11} style={{ color: "var(--text-muted)" }} />
                            </div>
                            <span style={{ fontSize: "0.86rem", color: "var(--text-primary)", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
                              {isFlipped ? c.back : c.front}
                            </span>
                          </button>
                          <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: "8px" }}>
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "0.66rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                              <span>
                                {c.topic ? `#${c.topic}` : "No topic"}
                                {c.box ? ` · Box ${c.box}` : ""}
                              </span>
                              <span>{c.review_count} review{c.review_count === 1 ? "" : "s"}</span>
                            </div>
                            {isFlipped && (
                              <div>
                                <div className="config-sub-pills-list" style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
                                  {[
                                    { r: 0, label: "Again" },
                                    { r: 1, label: "Hard" },
                                    { r: 2, label: "Good" },
                                    { r: 3, label: "Easy" },
                                  ].map((opt) => (
                                    <button
                                      key={opt.r}
                                      type="button"
                                      className="config-sub-pill"
                                      onClick={() => handleReview(c.id, opt.r, opt.label)}
                                      style={{ fontSize: "0.7rem", padding: "4px 10px" }}
                                    >
                                      {opt.label}
                                    </button>
                                  ))}
                                </div>
                              </div>
                            )}
                            <div style={{ display: "flex", gap: "6px" }}>
                              <button className="btn-workspace" style={{ fontSize: "0.7rem", padding: "4px 10px", display: "inline-flex", alignItems: "center", gap: "4px" }}
                                onClick={() => { setEditingCardId(c.id); setCardFront(c.front); setCardBack(c.back); setCardTopic(c.topic || ""); }}>
                                <Pencil size={10} />
                                Edit
                              </button>
                              <button className="btn-workspace" style={{ fontSize: "0.7rem", padding: "4px 10px", display: "inline-flex", alignItems: "center", gap: "4px" }}
                                onClick={async () => {
                                  if (!window.confirm("Delete this flashcard?")) return;
                                  try { await deleteFlashcard(c.id); toast.success("Flashcard deleted."); } catch (err: any) { toast.error(err.message || "Failed to delete flashcard."); }
                                }}>
                                <Trash2 size={10} />
                                Delete
                              </button>
                            </div>
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ── Sheets tab ── */}
      {activeTab === "sheets" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {isLoadingSheets ? (
            <div className="chat-history-loading" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Loader2 size={14} className="spin" style={{ animation: "spin 1s linear infinite" }} />
              Loading your revision sheets…
            </div>
          ) : savedSheets.length === 0 ? (
            <div style={{ padding: "40px 16px", textAlign: "center", color: "var(--text-muted)", fontSize: "0.82rem", border: "1px dashed var(--border)", borderRadius: "var(--r-xl)" }}>
              <div style={{ display: "flex", justifyContent: "center", marginBottom: 10 }}>
                <BookOpenCheck size={22} style={{ opacity: 0.6 }} />
              </div>
              <div>No revision sheets yet. Write a one-page sheet from your books and it is kept here automatically.</div>
              <div style={{ marginTop: 12 }}>
                <button className="btn-workspace" onClick={onWriteSheet} style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontWeight: 700 }}>
                  <ArrowRight size={13} /> Revise from books
                </button>
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {savedSheets.map((s) => (
                <div key={s.id} className="workspace-card" style={{ background: "var(--surface-2)", border: "1px solid var(--border-light)", borderRadius: "var(--r-xl)", padding: "14px", display: "flex", flexDirection: "column", gap: "8px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: "0.92rem", color: "var(--text-primary)" }}>{s.label}</div>
                      <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: "2px" }}>
                        Saved {formatShortDate(s.created_at)} · {s.book_ids.length} book{s.book_ids.length === 1 ? "" : "s"} · {s.length === "quick" ? "quick" : "full"} sheet
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
                      <button className="btn-workspace" onClick={() => onOpenSheet({ book_ids: s.book_ids, chapter: s.chapter, topic: s.topic, length: s.length })} title="Reopen this sheet" aria-label={`Open sheet ${s.label}`} style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.7rem" }}>
                        <Eye size={10} /> Open
                      </button>
                      <button
                        className="btn-workspace"
                        onClick={async () => {
                          if (!window.confirm(`Remove "${s.label}" from Study Corner?`)) return;
                          try { await deleteSheet(s.id); toast.success("Sheet removed from Study Corner."); } catch (e) { toast.error(e instanceof Error ? e.message : "Failed to remove sheet."); }
                        }}
                        title="Remove from Study Corner"
                        aria-label={`Remove sheet ${s.label}`}
                        style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "4px 10px", fontSize: "0.7rem", color: "var(--danger, #ef4444)" }}
                      >
                        <Trash2 size={10} />
                      </button>
                    </div>
                  </div>
                  {(s.chapter || s.topic) && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", fontSize: "0.68rem" }}>
                      {s.chapter && <span className="workspace-badge" style={{ background: "var(--surface-3)", padding: "2px 8px", borderRadius: "20px" }}>{s.chapter}</span>}
                      {s.topic && <span className="workspace-badge" style={{ background: "var(--surface-3)", padding: "2px 8px", borderRadius: "20px" }}>{s.topic}</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}