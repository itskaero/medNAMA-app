"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Library } from "lucide-react";
import { toast } from "sonner";
import PageShell from "@/components/layout/PageShell";
import { api } from "~/lib/api";
import type { PaedsBook } from "~/lib/types";

/** The Paediatrics books in the library, what each is for, and whether it is ready to search. */
export default function LibraryView({ isAdmin }: { isAdmin: boolean }) {
  const [data, setData] = useState<{ books: PaedsBook[]; roles: Record<string, string> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<{ books: PaedsBook[]; roles: Record<string, string> }>("/api/paeds/books").then(setData).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  const setRole = async (id: number, role: string) => {
    try {
      await api(`/api/paeds/books/${id}/role`, { method: "PUT", body: JSON.stringify({ role }) });
      toast.success("Role saved");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save");
    }
  };
  const ready = data?.books.filter((b) => b.status === "ready").length ?? 0;

  return (
    <PageShell title="Library" icon={<Library size={22} />} loading={!data && !error} error={error} onRetry={load}
      subtitle={data ? `${data.books.length} Paediatrics books, ${ready} ready to search. Each has a job: read the spine first, look up the reference.` : undefined}>
      {data ? (
        <table className="pn-books">
          <thead><tr><th>Book</th><th>Role</th><th className="hide-sm">Pages</th><th>Status</th></tr></thead>
          <tbody>
            {data.books.map((b) => (
              <tr key={b.id}>
                <td>{b.title}</td>
                <td>
                  {isAdmin ? (
                    <select value={b.role ?? ""} onChange={(e) => setRole(b.id, e.target.value)} aria-label={`Role of ${b.title}`}>
                      <option value="" disabled>Choose…</option>
                      {Object.keys(data.roles).map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  ) : <b>{b.role ?? "—"}</b>}
                  {b.role_label ? <div className="pn-src">{b.role_label}</div> : null}
                </td>
                <td className="hide-sm">{b.pages?.toLocaleString() ?? "—"}</td>
                <td><span className={`pn-status ${b.status}`}>{b.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </PageShell>
  );
}
