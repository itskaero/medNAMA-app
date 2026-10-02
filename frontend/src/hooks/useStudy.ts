"use client";

import { useState, useCallback } from "react";
import { Note, Flashcard, SavedSheet } from "@/types";
import { API } from "@/lib/constants";
import { proxySafeFetch } from "@/lib/proxyFetch";

interface UseStudyParams {
  token: string | null;
  getHeaders: () => HeadersInit;
}

/** Throws the FastAPI `detail` (or `message`) when a response is not ok. */
async function readError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (data && typeof data.detail === "string") return data.detail;
    if (data && typeof data.message === "string") return data.message;
  } catch {
    /* not JSON */
  }
  return `Request failed (HTTP ${res.status}).`;
}

export function useStudy({ token, getHeaders }: UseStudyParams) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [flashcards, setFlashcards] = useState<Flashcard[]>([]);
  const [savedSheets, setSavedSheets] = useState<SavedSheet[]>([]);
  const [isLoadingNotes, setIsLoadingNotes] = useState(false);
  const [isLoadingFlashcards, setIsLoadingFlashcards] = useState(false);
  const [isLoadingSheets, setIsLoadingSheets] = useState(false);

  // ── Notes ────────────────────────────────────────────────────────────────
  const fetchNotes = useCallback(async () => {
    if (!token) return;
    setIsLoadingNotes(true);
    try {
      const res = await proxySafeFetch(`${API}/api/notes`, {
        headers: getHeaders(),
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readError(res));
      setNotes(await res.json());
    } catch (err) {
      console.error("Failed to load notes:", err);
    } finally {
      setIsLoadingNotes(false);
    }
  }, [token, getHeaders]);

  const createNote = useCallback(
    async (payload: { title: string; content: string; book_title?: string | null; page_number?: number | null; source_context?: string | null }): Promise<Note> => {
      const res = await proxySafeFetch(`${API}/api/notes`, {
        method: "POST",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readError(res));
      const note = (await res.json()) as Note;
      setNotes((prev) => [note, ...prev]);
      return note;
    },
    [getHeaders]
  );

  const updateNote = useCallback(
    async (id: number, payload: { title: string; content: string }) => {
      const res = await proxySafeFetch(`${API}/api/notes/${id}`, {
        method: "PUT",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readError(res));
      const updated = (await res.json()) as Note;
      setNotes((prev) => prev.map((n) => (n.id === id ? updated : n)));
    },
    [getHeaders]
  );

  const deleteNote = useCallback(
    async (id: number) => {
      const res = await proxySafeFetch(`${API}/api/notes/${id}`, {
        method: "DELETE",
        headers: getHeaders(),
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readError(res));
      setNotes((prev) => prev.filter((n) => n.id !== id));
    },
    [getHeaders]
  );

  // ── Flashcards ───────────────────────────────────────────────────────────
  const fetchFlashcards = useCallback(async () => {
    if (!token) return;
    setIsLoadingFlashcards(true);
    try {
      const res = await proxySafeFetch(`${API}/api/flashcards`, {
        headers: getHeaders(),
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readError(res));
      setFlashcards(await res.json());
    } catch (err) {
      console.error("Failed to load flashcards:", err);
    } finally {
      setIsLoadingFlashcards(false);
    }
  }, [token, getHeaders]);

  const createFlashcard = useCallback(
    async (payload: { front: string; back: string; topic?: string | null; book_title?: string | null; page_number?: number | null }): Promise<Flashcard> => {
      const res = await proxySafeFetch(`${API}/api/flashcards`, {
        method: "POST",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readError(res));
      const card = (await res.json()) as Flashcard;
      setFlashcards((prev) => [card, ...prev]);
      return card;
    },
    [getHeaders]
  );

  const updateFlashcard = useCallback(
    async (id: number, payload: { front: string; back: string }) => {
      const res = await proxySafeFetch(`${API}/api/flashcards/${id}`, {
        method: "PUT",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readError(res));
      const card = (await res.json()) as Flashcard;
      setFlashcards((prev) => prev.map((f) => (f.id === id ? { ...f, ...card } : f)));
    },
    [getHeaders]
  );

  const deleteFlashcard = useCallback(
    async (id: number) => {
      const res = await proxySafeFetch(`${API}/api/flashcards/${id}`, {
        method: "DELETE",
        headers: getHeaders(),
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readError(res));
      setFlashcards((prev) => prev.filter((f) => f.id !== id));
    },
    [getHeaders]
  );

  /** Record the result of a flip (0=again, 1=hard, 2=good, 3=easy). */
  const reviewFlashcard = useCallback(
    async (id: number, rating: number) => {
      const res = await proxySafeFetch(`${API}/api/flashcards/${id}/review`, {
        method: "POST",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ rating }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const updated = (await res.json()) as { id: number; box: number; next_due: string; review_count: number };
      setFlashcards((prev) =>
        prev.map((f) =>
          f.id === id
            ? { ...f, box: updated.box, next_due: updated.next_due, review_count: updated.review_count }
            : f
        )
      );
    },
    [getHeaders]
  );

  // ── Saved revision sheets (written in Revise from books) ──────────────────
  const fetchSheets = useCallback(async () => {
    if (!token) return;
    setIsLoadingSheets(true);
    try {
      const res = await proxySafeFetch(`${API}/api/study/revision-sheets/saved`, {
        headers: getHeaders(),
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readError(res));
      setSavedSheets(await res.json());
    } catch (err) {
      console.error("Failed to load saved sheets:", err);
    } finally {
      setIsLoadingSheets(false);
    }
  }, [token, getHeaders]);

  const deleteSheet = useCallback(
    async (id: number) => {
      const res = await proxySafeFetch(`${API}/api/study/revision-sheets/saved/${id}`, {
        method: "DELETE",
        headers: getHeaders(),
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readError(res));
      setSavedSheets((prev) => prev.filter((s) => s.id !== id));
    },
    [getHeaders]
  );

  return {
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
  };
}