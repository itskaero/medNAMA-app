"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, X, ZoomIn, ZoomOut } from "lucide-react";
import { API } from "@/lib/constants";
import { OPEN_PAGE_EVENT, OpenPageRequest, setPageViewerEnabled } from "@/lib/pageViewer";

interface PageMeta {
  book_id: number;
  title: string;
  full_title: string | null;
  edition: number | null;
  year: number | null;
  page: number;
  total_pages: number | null;
  available: boolean;
  min_page: number;
  max_page: number;
  labels: Record<string, string | null>;
}

type Box = [number, number, number, number];

/** The printed page behind a citation, with the cited passage highlighted. Mounted once in page.tsx. */
export default function PageViewer({ getHeaders }: { getHeaders: () => HeadersInit }) {
  const [req, setReq] = useState<OpenPageRequest | null>(null);
  const [meta, setMeta] = useState<PageMeta | null>(null);
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [imgLoading, setImgLoading] = useState(true);
  const [imgError, setImgError] = useState<string | null>(null);
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [zoom, setZoom] = useState(1);

  const close = useCallback(() => {
    setReq(null);
    setMeta(null);
    setError(null);
    setBoxes([]);
    setZoom(1);
  }, []);

  // Ask once whether this user may see book pages; citations become links only if so.
  useEffect(() => {
    fetch(`${API}/api/auth/me`, { headers: getHeaders(), credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((me) => setPageViewerEnabled(!!me?.can_view_pages))
      .catch(() => {});
  }, [getHeaders]);

  useEffect(() => {
    const onOpen = (e: Event) => setReq((e as CustomEvent<OpenPageRequest>).detail);
    window.addEventListener(OPEN_PAGE_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_PAGE_EVENT, onOpen);
  }, []);

  useEffect(() => {
    if (!req) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [req, close]);

  // Resolve the citation to a book and page.
  useEffect(() => {
    if (!req) return;
    let cancelled = false;
    setMeta(null);
    setError(null);
    const q = new URLSearchParams({ page: String(req.page) });
    if (req.bookId) q.set("book_id", String(req.bookId));
    else if (req.bookTitle) q.set("title", req.bookTitle);
    fetch(`${API}/api/pages/book?${q}`, { headers: getHeaders(), credentials: "include" })
      .then(async (res) => {
        const data = await res.json().catch(() => null);
        if (!res.ok) throw new Error(data?.detail || `Couldn't open the page (error ${res.status}).`);
        return data as PageMeta;
      })
      .then((m) => {
        if (cancelled) return;
        setMeta(m);
        setPage(m.page);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [req, getHeaders]);

  // Highlight the cited passage on the cited page only.
  useEffect(() => {
    setBoxes([]);
    if (!meta || !req?.excerpt || page !== meta.page || !meta.available) return;
    let cancelled = false;
    fetch(`${API}/api/pages/highlight`, {
      method: "POST",
      headers: { ...getHeaders(), "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ book_id: meta.book_id, page, text: req.excerpt }),
    })
      .then((res) => (res.ok ? res.json() : { boxes: [] }))
      .then((d) => !cancelled && setBoxes(d.boxes || []))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [meta, page, req, getHeaders]);

  useEffect(() => {
    setImgLoading(true);
    setImgError(null);
  }, [page, meta?.book_id]);

  if (!req) return null;

  const label = meta ? meta.labels[String(page)] : null;
  const pageText = label ? `Page ${label}` : `PDF page ${page}`;
  const edition = meta?.edition ? `${meta.edition}e` : null;
  const canPrev = !!meta && page > meta.min_page;
  const canNext = !!meta && page < meta.max_page;

  return (
    <div className="modal-backdrop" onClick={close} role="dialog" aria-modal aria-label="Book page">
      <div className="modal-panel page-viewer" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">
            {meta ? meta.title : req.bookTitle || "Book page"}
            {meta ? (
              <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>
                · {pageText}
                {label && label !== String(page) ? ` (PDF ${page})` : ""}
              </span>
            ) : null}
          </span>
          <div className="page-viewer-tools">
            <button className="modal-close-btn" onClick={() => setZoom((z) => Math.max(1, z - 0.5))}
              disabled={zoom <= 1} aria-label="Zoom out" title="Zoom out"><ZoomOut size={14} /></button>
            <button className="modal-close-btn" onClick={() => setZoom((z) => Math.min(2.5, z + 0.5))}
              disabled={zoom >= 2.5} aria-label="Zoom in" title="Zoom in"><ZoomIn size={14} /></button>
            <button className="modal-close-btn" onClick={close} aria-label="Close page"><X size={14} /></button>
          </div>
        </div>

        <div className="page-viewer-body">
          {error ? (
            <div className="error-banner" role="alert" style={{ margin: "var(--sp-5)" }}>{error}</div>
          ) : !meta ? (
            <div className="page-viewer-status"><Loader2 size={16} className="spin" /> Finding the page…</div>
          ) : !meta.available ? (
            <div className="page-viewer-status">
              This book&apos;s PDF isn&apos;t on the server yet, so the page can&apos;t be shown.
            </div>
          ) : (
            <div className="page-viewer-scroll">
              <div className="page-viewer-sheet" style={{ width: `${zoom * 100}%` }}>
                {imgLoading && !imgError ? (
                  <div className="page-viewer-status page-viewer-overlay"><Loader2 size={16} className="spin" /> Loading page…</div>
                ) : null}
                {imgError ? <div className="error-banner" role="alert" style={{ margin: "var(--sp-5)" }}>{imgError}</div> : null}
                <img
                  key={`${meta.book_id}-${page}`}
                  src={`${API}/api/pages/${meta.book_id}/${page}.webp`}
                  alt={`${meta.title}, ${pageText}`}
                  onLoad={() => setImgLoading(false)}
                  onError={() => {
                    setImgLoading(false);
                    setImgError("Couldn't load this page. You may have reached the hourly page limit.");
                  }}
                  style={{ display: imgError ? "none" : "block" }}
                />
                {boxes.map(([x0, y0, x1, y1], i) => (
                  <span key={i} className="page-viewer-mark" aria-hidden
                    style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }} />
                ))}
              </div>
            </div>
          )}
        </div>

        {meta && meta.available ? (
          <div className="page-viewer-footer">
            <button className="btn-workspace" onClick={() => setPage((p) => p - 1)} disabled={!canPrev}>
              <ChevronLeft size={13} /> Previous
            </button>
            <span className="page-viewer-caption">
              {[meta.full_title, edition, meta.year].filter(Boolean).join(" · ")}
              {page === meta.page && boxes.length ? " · cited passage highlighted" : ""}
            </span>
            <button className="btn-workspace" onClick={() => setPage((p) => p + 1)} disabled={!canNext}>
              Next <ChevronRight size={13} />
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
