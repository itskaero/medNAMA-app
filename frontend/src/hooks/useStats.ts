"use client";

import { useState, useCallback, useEffect } from "react";
import { API } from "@/lib/constants";

interface UseStatsParams {
  token: string | null;
  getHeaders: () => HeadersInit;
  activeView: string;
}

export function useStats({ token, getHeaders, activeView }: UseStatsParams) {
  const [stats, setStats] = useState<any>(null);
  const [isLoadingStats, setIsLoadingStats] = useState(false);

  const fetchStats = useCallback(async () => {
    if (!token) return;
    setIsLoadingStats(true);
    try {
      const res = await fetch(`${API}/api/dashboard/stats`, {
        headers: getHeaders(),
        credentials: "include",
      });
      if (res.ok) setStats(await res.json());
    } catch (err) {
      console.error("Failed to load dashboard stats", err);
    } finally {
      setIsLoadingStats(false);
    }
  }, [token, getHeaders]);

  // Refresh stats when dashboard becomes active
  useEffect(() => {
    if (token && activeView === "dashboard") {
      fetchStats();
    }
  }, [activeView, token, fetchStats]);

  return {
    stats,
    isLoadingStats,
    setIsLoadingStats,
    fetchStats,
  };
}
