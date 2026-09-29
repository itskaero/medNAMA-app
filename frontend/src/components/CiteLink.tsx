import React, { useSyncExternalStore } from "react";
import { openBookPage, pageViewerEnabled, subscribePageViewer } from "@/lib/pageViewer";

/** A citation's page reference that opens the book page viewer. Renders plain text without a page. */
export function CiteLink({
  bookTitle,
  page,
  excerpt,
  children,
}: {
  bookTitle?: string | null;
  page?: number | null;
  excerpt?: string | null;
  children: React.ReactNode;
}) {
  const enabled = useSyncExternalStore(subscribePageViewer, pageViewerEnabled, () => false);
  if (!enabled || !page || !bookTitle) return <>{children}</>;
  return (
    <button
      type="button"
      className="cite-link"
      title="Open this page of the book"
      onClick={(e) => {
        e.stopPropagation();
        openBookPage({ bookTitle, page, excerpt });
      }}
    >
      {children}
    </button>
  );
}
