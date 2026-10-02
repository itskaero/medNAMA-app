import React, { useState } from "react";
import { ChevronDown, BookOpen, FileText, Loader2, X } from "lucide-react";
import { RAGSource } from "../types";
import { API } from "@/lib/constants";
import { proxySafeFetch } from "@/lib/proxyFetch";
import { CiteLink } from "@/components/CiteLink";

/** Full-text view of a single matched source chunk (fetched on demand). */
function FullTextView({
  source,
  token,
  onClose,
}: {
  source: RAGSource;
  token: string | null;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    content: string;
    book_title: string;
    chapter: string | null;
    page_number: number | null;
  } | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    const savedToken = localStorage.getItem("token") || token;
    (async () => {
      try {
        const res = await proxySafeFetch(`${API}/api/chat/source/${source.chunk_id}`, {
          headers: savedToken ? { Authorization: `Bearer ${savedToken}` } : {},
          credentials: "include",
        });
        if (!res.ok) {
          throw new Error(`Unable to load source (HTTP ${res.status}).`);
        }
        const data = await res.json();
        if (!cancelled) setDetail(data);
      } catch (err: any) {
        if (!cancelled) setError(err.message || "Failed to load source text.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source.chunk_id, token]);

  return (
    <div style={{ marginTop: "var(--sp-2)" }}>
      {loading ? (
        <div className="chat-history-loading" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Loader2 size={13} className="spin" style={{ animation: "spin 1s linear infinite" }} />
          Loading full source text…
        </div>
      ) : error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : detail && detail.content ? (
        <>
          <div
            style={{
              fontSize: "0.72rem", color: "var(--text-muted)",
              fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
              marginBottom: "6px", display: "flex", justifyContent: "space-between", alignItems: "center",
            }}
          >
            <span>
              {detail.book_title}
              {detail.page_number ? ` · Page ${detail.page_number}` : ""}
              {detail.chapter ? ` · ${detail.chapter}` : ""}
            </span>
            <button
              className="btn-workspace"
              onClick={onClose}
              title="Close source text"
              aria-label="Close source text"
              style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "2px 8px", fontSize: "0.68rem" }}
            >
              <X size={10} />
              Close
            </button>
          </div>
          <div
            style={{
              background: "var(--surface-2)",
              border: "1px solid var(--border-light)",
              borderRadius: "var(--r-md)",
              padding: "10px 12px",
              fontSize: "0.78rem",
              lineHeight: 1.6,
              color: "var(--text-secondary)",
              whiteSpace: "pre-wrap",
              maxHeight: "220px",
              overflowY: "auto",
            }}
          >
            {detail.content}
          </div>
        </>
      ) : null}
    </div>
  );
}

/**
 * F1 — "All matched sources" panel. Shows the reranked candidate passages that
 * the hybrid pipeline found before merging, so students can see exactly which
 * books/chapters matched and can drill into the raw text.
 */
export const SourcesPanel = React.memo(
  ({ sources, token }: { sources: RAGSource[]; token: string | null }) => {
    const [open, setOpen] = useState(false);
    const [expandedId, setExpandedId] = useState<number | null>(null);

    if (!sources || sources.length === 0) return null;

    return (
      <div style={{ marginTop: "var(--sp-3)" }}>
        <button
          className={`section-toggle-btn ${open ? "open" : ""}`}
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          title="Show every textbook passage the answer was built from"
        >
          <ChevronDown className="chevron" aria-hidden />
          <BookOpen size={12} />
          All Matched Sources
          <span className="section-count">{sources.length}</span>
        </button>

        {open ? (
          <div className="citations-drawer" role="list" aria-label="Matched textbook sources">
            {sources.map((s) => (
              <div className="citation-card" role="listitem" key={s.chunk_id}>
                <span className="citation-num">{s.rank}</span>
                <div className="citation-body">
                  <div className="citation-source">
                    {s.book_title}
                    {s.page_number ? <span className="citation-page"><CiteLink bookTitle={s.book_title} page={s.page_number} excerpt={s.snippet}>p. {s.page_number}</CiteLink></span> : null}
                    {s.relevance_score ? (
                      <span className="citation-page" title="Reranker relevance score">
                        {Math.round(s.relevance_score * 100)}%
                      </span>
                    ) : null}
                  </div>
                  {s.chapter ? (
                    <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", margin: "2px 0 4px" }}>
                      {s.chapter}
                    </div>
                  ) : null}
                  <div className="citation-excerpt">"{s.snippet}"</div>
                  <button
                    className="btn-workspace"
                    onClick={() => setExpandedId(expandedId === s.chunk_id ? null : s.chunk_id)}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: "4px",
                      padding: "2px 10px", fontSize: "0.68rem", marginTop: "6px",
                    }}
                    aria-expanded={expandedId === s.chunk_id}
                    title="Open the source paragraph in full"
                  >
                    <FileText size={10} />
                    {expandedId === s.chunk_id ? "Hide text" : "View full text"}
                  </button>
                  {expandedId === s.chunk_id ? (
                    <FullTextView source={s} token={token} onClose={() => setExpandedId(null)} />
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    );
  }
);

SourcesPanel.displayName = "SourcesPanel";