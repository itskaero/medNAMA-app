"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { Book } from "@/types";
import { API } from "@/lib/constants";

interface UseLibraryParams {
  token: string | null;
  getHeaders: () => HeadersInit;
  handleLogout: () => void;
}

export function useLibrary({ token, getHeaders, handleLogout }: UseLibraryParams) {
  const [books, setBooks] = useState<Book[]>([]);
  const [isLoadingBooks, setIsLoadingBooks] = useState(true);
  const [booksError, setBooksError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // A busy server (e.g. a long AI job) can fail one request: retry with backoff, and keep the list already on
  // screen instead of emptying it. Only after every retry fails does the sidebar show "Couldn't load".
  const fetchBooks = useCallback(
    async (showLoader = false) => {
      if (!token) return;
      if (showLoader) setIsLoadingBooks(true);
      const delays = [1000, 3000, 8000];
      try {
        for (let attempt = 0; ; attempt++) {
          let status = 0;
          try {
            const res = await fetch(`${API}/api/books`, { headers: getHeaders(), credentials: "include" });
            status = res.status;
            if (res.ok) {
              setBooks(await res.json());
              setBooksError(null);
              return;
            }
            if (res.status === 401) {
              handleLogout();
              return;
            }
          } catch {
            /* network error: retried below */
          }
          if (attempt >= delays.length || (status >= 400 && status < 500)) {
            setBooksError(status ? `The server answered ${status}.` : "Couldn't reach the server.");
            return;
          }
          await new Promise((r) => setTimeout(r, delays[attempt]));
        }
      } finally {
        setIsLoadingBooks(false);
      }
    },
    [token, getHeaders]
  );

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.endsWith(".pdf")) {
      setUploadError("Only PDF files are supported.");
      return;
    }
    setUploading(true);
    setUploadError(null);
    const formData = new FormData();
    formData.append("file", file);

    const uploadPromise = async () => {
      const res = await fetch(`${API}/api/ingest`, {
        method: "POST",
        headers: getHeaders(),
        credentials: "include",
        body: formData,
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.detail || "Failed to start ingestion.");
      }
      if (fileRef.current) fileRef.current.value = "";
      await fetchBooks(false);
    };

    const promise = uploadPromise();

    toast.promise(promise, {
      loading: "Ingesting medical textbook...",
      success: "Textbook ingested successfully!",
      error: (err) => `Ingestion failed: ${err.message}`,
    });

    try {
      await promise;
    } catch (err: any) {
      setUploadError(err.message || "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteBook = async (bookId: number, title: string) => {
    if (!confirm(`Remove "${title}"? This will delete all embeddings and figures.`)) return;
    try {
      const res = await fetch(`${API}/api/books/${bookId}`, {
        method: "DELETE",
        headers: getHeaders(),
        credentials: "include",
      });
      if (res.ok) {
        setBooks((prev) => prev.filter((b) => b.id !== bookId));
        toast.success("Book deleted successfully.");
      } else {
        toast.error("Failed to delete book.", { duration: Infinity });
      }
    } catch {
      toast.error("Network error.", { duration: Infinity });
    }
  };

  const preloadDashboardData = useCallback(
    async (fetchStats: () => Promise<void>) => {
      if (!token) return;
      setIsLoadingBooks(true);
      try {
        await Promise.all([fetchBooks(false), fetchStats()]);
      } catch (err) {
        console.error("Dashboard preloading failed:", err);
      } finally {
        setIsLoadingBooks(false);
      }
    },
    [token, fetchBooks]
  );

  // Poll books that are being ingested every 4s (a stalled ingest is not: nothing is changing).
  useEffect(() => {
    if (!token || books.length === 0) return;
    const hasActive = books.some((b) => (b.status === "processing" || b.status === "pending") && !b.stalled);
    if (!hasActive) return;
    const t = setInterval(() => fetchBooks(false), 4000);
    return () => clearInterval(t);
  }, [books, token]);

  return {
    books,
    setBooks,
    isLoadingBooks,
    setIsLoadingBooks,
    booksError,
    uploading,
    uploadError,
    fileRef,
    fetchBooks,
    handleFileUpload,
    handleDeleteBook,
    preloadDashboardData,
  };
}
