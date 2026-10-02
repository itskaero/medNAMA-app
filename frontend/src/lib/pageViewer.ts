// Opens the book page viewer from anywhere a citation is shown ("Guyton & Hall 15e, p. 404").
// A window event keeps call sites to one line: no provider or prop threading through every view.

export interface OpenPageRequest {
  bookTitle?: string | null;
  bookId?: number | null;
  page: number;
  /** The cited passage; highlighted on the page when it can be found. */
  excerpt?: string | null;
}

export const OPEN_PAGE_EVENT = "mednama:open-page";

export function openBookPage(req: OpenPageRequest): void {
  if (!req.page || (!req.bookTitle && !req.bookId)) return;
  window.dispatchEvent(new CustomEvent<OpenPageRequest>(OPEN_PAGE_EVENT, { detail: req }));
}

// Whether this user may open book pages (/api/auth/me can_view_pages). Page numbers render as plain
// text until PageViewer has asked, and stay plain for users the server doesn't show pages to.
let viewerEnabled = false;
const listeners = new Set<() => void>();

export function setPageViewerEnabled(on: boolean): void {
  if (on === viewerEnabled) return;
  viewerEnabled = on;
  listeners.forEach((l) => l());
}

export function subscribePageViewer(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function pageViewerEnabled(): boolean {
  return viewerEnabled;
}
