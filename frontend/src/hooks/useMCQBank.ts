"use client";

import { useState, useCallback, useRef } from "react";
import { API } from "@/lib/constants";

interface UseMCQBankParams {
  token: string | null;
}

const PAGE_SIZE = 50;

export function useMCQBank({ token }: UseMCQBankParams) {
  const [mcqsList, setMcqsList] = useState<any[]>([]);
  const [mcqTotal, setMcqTotal] = useState<number | null>(null);
  const [isLoadingMcqs, setIsLoadingMcqs] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [mcqSearchText, setMcqSearchText] = useState("");
  const [mcqFilterCategory, setMcqFilterCategory] = useState("all");
  // The filters of the list on screen, so "Load more" continues the same query.
  const current = useRef({ category: "all", search: "" });

  const load = useCallback(
    (category: string, search: string, offset: number) => {
      const savedToken = localStorage.getItem("token") || token;
      if (!savedToken) return Promise.resolve(null);
      let url = `${API}/api/mcqs?category=${encodeURIComponent(category)}&offset=${offset}&limit=${PAGE_SIZE}`;
      if (search.trim()) {
        url += `&search=${encodeURIComponent(search.trim())}`;
      }
      return fetch(url, { headers: { Authorization: `Bearer ${savedToken}` }, credentials: "include" }).then(async (res) => {
        if (!res.ok) throw new Error("Failed to load MCQs");
        const total = Number(res.headers.get("X-Total-Count"));
        return { rows: (await res.json()) as any[], total: Number.isFinite(total) ? total : null };
      });
    },
    [token]
  );

  const fetchMcqs = useCallback(
    (category = "all", search = "") => {
      current.current = { category, search };
      setIsLoadingMcqs(true);
      load(category, search, 0)
        .then((page) => {
          if (!page) return;
          setMcqsList(page.rows);
          setMcqTotal(page.total);
        })
        .catch((err) => console.error(err))
        .finally(() => setIsLoadingMcqs(false));
    },
    [load]
  );

  const loadMoreMcqs = useCallback(() => {
    const { category, search } = current.current;
    setIsLoadingMore(true);
    load(category, search, mcqsList.length)
      .then((page) => {
        if (!page) return;
        setMcqsList((prev) => {
          const seen = new Set(prev.map((m) => m.id));
          return [...prev, ...page.rows.filter((m) => !seen.has(m.id))];
        });
        setMcqTotal(page.total);
      })
      .catch((err) => console.error(err))
      .finally(() => setIsLoadingMore(false));
  }, [load, mcqsList.length]);

  return {
    mcqsList,
    setMcqsList,
    mcqTotal,
    isLoadingMcqs,
    isLoadingMore,
    loadMoreMcqs,
    mcqSearchText,
    setMcqSearchText,
    mcqFilterCategory,
    setMcqFilterCategory,
    fetchMcqs,
  };
}
