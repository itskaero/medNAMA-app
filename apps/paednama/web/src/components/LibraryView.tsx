"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { BookMarked, FileText, Library, Search } from "lucide-react";
import { toast } from "sonner";
import PageShell from "@/components/layout/PageShell";
import { api } from "~/lib/api";
import type { PaedsBook } from "~/lib/types";

const ORDER = ["spine", "reference", "neonatal", "critical", "clinical", "reasoning", "protocol", "radiology", "mcq"];
const SHOW = 24;

/** The Paediatrics library in two parts: the textbooks, by what each is for, and the guidelines, searchable. */
export default function LibraryView({ isAdmin }: { isAdmin: boolean }) {
  const [data, setData] = useState<{ books: PaedsBook[]; roles: Record<string, string> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);
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

  const textbooks = useMemo(() => (data?.books ?? []).filter((b) => b.role !== "guideline")
    .sort((a, b) => (ORDER.indexOf(a.role ?? "") + 1 || 99) - (ORDER.indexOf(b.role ?? "") + 1 || 99) || a.title.localeCompare(b.title)), [data]);
  const guidelines = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return (data?.books ?? []).filter((b) => b.role === "guideline" && words.every((w) => b.title.toLowerCase().includes(w)))
      .sort((a, b) => a.title.localeCompare(b.title));
  }, [data, q]);
  const nGuidelines = data?.books.filter((b) => b.role === "guideline").length ?? 0;

  const role = (b: PaedsBook) => isAdmin ? (
    <select className="pn-role-select" value={b.role ?? ""} onChange={(e) => setRole(b.id, e.target.value)} aria-label={`Role of ${b.title}`}>
      <option value="" disabled>Choose a role…</option>
      {Object.keys(data!.roles).map((r) => <option key={r} value={r}>{r}</option>)}
    </select>
  ) : null;

  return (
    <PageShell title="Library" icon={<Library size={22} />} loading={!data && !error} error={error} onRetry={load}
      subtitle={data ? `${textbooks.length} textbooks and ${nGuidelines} guidelines. Notes, Dr. Shifa and questions are written only from these, and cite them by page.` : undefined}>
      {data ? (
        <>
          <h2 className="pn-lib-h"><BookMarked size={18} /> Textbooks</h2>
          <div className="pn-lib-grid">
            {textbooks.map((b) => (
              <div key={b.id} className="pn-lib-card">
                <span className="pn-lib-role">{b.role ?? "unassigned"}</span>
                <span className="pn-lib-title">{b.title}</span>
                {b.role_label ? <span className="pn-lib-what">{b.role_label}</span> : null}
                <span className="pn-lib-meta">{b.pages ? `${b.pages.toLocaleString()} pages` : ""}{b.status !== "ready" ? ` · ${b.status}` : ""}</span>
                {role(b)}
              </div>
            ))}
          </div>

          <h2 className="pn-lib-h"><FileText size={18} /> Guidelines <small>{nGuidelines}</small></h2>
          <p className="pn-lib-note">Society, WHO and national guidance (IAP, NICE, AAP, ESPGHAN, WHO and others). They are cited alongside the textbooks for current recommendations.</p>
          <label className="pn-find">
            <Search size={16} aria-hidden />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder='Find a guideline: "NICE jaundice", "IAP dengue"' aria-label="Find a guideline" />
          </label>
          <ul className="pn-lib-list">
            {(all || q ? guidelines : guidelines.slice(0, SHOW)).map((b) => (
              <li key={b.id}>
                <span>{b.title}</span>
                <span className="pn-lib-meta">{b.pages ? `${b.pages} p.` : ""}{b.status !== "ready" ? ` · ${b.status}` : ""}</span>
                {role(b)}
              </li>
            ))}
          </ul>
          {!q && !all && guidelines.length > SHOW ? (
            <button type="button" className="btn-workspace pn-lib-more" onClick={() => setAll(true)}>Show all {guidelines.length} guidelines</button>
          ) : null}
        </>
      ) : null}
    </PageShell>
  );
}
