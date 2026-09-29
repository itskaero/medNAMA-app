"""A book's structure, read once before ingestion: chapters per page, pages to skip, scanned pages.

Chapters
    Docling only sees one page slice at a time, so a slice that starts mid-chapter doesn't know which
    chapter it is in, and table captions end up as "chapters" ("Table 3.3 Pectoral Region Muscles").
    Publisher PDFs carry their table of contents as bookmarks (10 of the first 12 books do), which
    name the chapter of every page exactly. `chapters_by_page()` returns that name per PDF page.

Pages to skip
    Contents and index pages are lists of topic names and page numbers. Indexed as passages they
    match keyword searches for everything and push real passages down. Cover, title, copyright and
    dedication pages add nothing. `skip_pages()` finds them from the bookmarks ("Index", "Contents")
    and, for books without bookmarks, from how the page reads (mostly short lines ending in page
    numbers).

Scanned pages
    OCR is off in the normal pipeline because it is slow and every PDF so far has had a text layer.
    `scanned_pages()` finds pages with almost no text but an image on them, so only those slices are
    run through OCR.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.book_meta import PDFIUM_LOCK, page_text

logger = logging.getLogger(__name__)

# Bookmark titles whose pages carry no teaching content.
_SKIP_TITLES = re.compile(
    r"^\s*(front\s*cover|cover(\s*page)?|ifc|ibc|back\s*cover|half[\s-]*title(\s*page)?|title(\s*page)?|"
    r"copyright(\s*page)?|dedication|(table\s+of\s+)?contents|brief\s+contents|detailed\s+contents|"
    r"index|subject\s+index|contributors|list\s+of\s+contributors|acknowledge?ments?|"
    r"activate\s+your\s+ebook.*|in\s+memory|full\s+page\s+photo.*)\s*$", re.I)
# Bookmark titles that are wrappers, not chapters (kept out of the chapter path).
_WRAPPER = re.compile(r"(\.pdf$|^front\s*matter$|^back\s*matter$|^volume[-\s]*\d)", re.I)


def clean_title(raw) -> str:
    """Bookmark text as a reader would write it: no NUL padding, mis-decoded dashes or underscores."""
    t = str(raw or "").replace(chr(0), "").replace(chr(0xFFFD), chr(0x2013)).replace("_", " ")
    t = "".join(ch for ch in t if ch.isprintable())
    return " ".join(t.split())


def _outline(path: Path) -> list[tuple[int, int, str]]:
    """(page index, depth, title) for every bookmark, in document order."""
    try:
        from pypdf import PdfReader
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        reader = PdfReader(str(path))
        out: list[tuple[int, int, str]] = []

        def walk(items, depth):
            for it in items:
                if isinstance(it, list):
                    walk(it, depth + 1)
                    continue
                try:
                    page = reader.get_destination_page_number(it)
                except Exception:
                    continue
                title = clean_title(getattr(it, "title", ""))
                if page is not None and page >= 0 and title:
                    out.append((page, depth, title))

        walk(reader.outline, 0)
        return out
    except Exception as e:
        logger.info(f"No usable bookmarks in {path.name}: {e}")
        return []


def chapters_by_page(path: str | Path, n_pages: int) -> list[str | None]:
    """The chapter name of every PDF page (index 0 = page 1), from the PDF's bookmarks; [] if none.

    A page's chapter is the path of the two outermost bookmarks open at that page that are not
    wrappers ("Part III - Blood > Chapter 12 Anaemia" or just "Chapter 12 Anaemia").
    """
    entries = [e for e in _outline(Path(path)) if not _WRAPPER.search(e[2])]
    if len(entries) < 3:
        return []
    min_depth = min(d for _, d, _ in entries)
    by_page: list[str | None] = [None] * n_pages
    stack: list[tuple[int, str]] = []          # (relative depth, title) currently open
    entries.sort(key=lambda e: (e[0], e[1]))
    i = 0
    for p in range(n_pages):
        while i < len(entries) and entries[i][0] <= p:
            _, depth, title = entries[i]
            depth -= min_depth
            while stack and stack[-1][0] >= depth:
                stack.pop()
            stack.append((depth, title))
            i += 1
        path_titles = [t for d, t in stack if d <= 1]
        if path_titles and not _SKIP_TITLES.match(path_titles[-1]):
            by_page[p] = " > ".join(path_titles)
    return by_page


def _list_like(text: str) -> bool:
    """Mostly short lines that end in page numbers: a contents or index page."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 15:
        return False
    numbered = sum(1 for ln in lines if re.search(r"(\d{1,4}[a-z]?|[ivxlc]+)(\s*[,–-]\s*\d{1,4}[a-z]?)*\.?$", ln)
                   and len(ln) < 90)
    return numbered / len(lines) >= 0.55


def skip_pages(path: str | Path, n_pages: int) -> set[int]:
    """0-based PDF pages to leave out of the passages (front matter, contents, index)."""
    path = Path(path)
    entries = _outline(path)
    skip: set[int] = set()
    if entries:
        starts = sorted({(p, t) for p, _, t in entries})
        for k, (p, title) in enumerate(starts):
            if _SKIP_TITLES.match(title):
                nxt = next((q for q, _ in starts[k + 1:] if q > p), None)
                end = nxt if nxt is not None else (n_pages if re.search(r"index", title, re.I) else p + 1)
                skip.update(range(p, min(end, n_pages)))
    # Pages that read like a contents or index list, near the front or back only (inside a chapter a
    # numbered list is content).
    import pypdfium2 as pdfium
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            edge = list(range(min(40, n_pages))) + list(range(max(0, n_pages - 80), n_pages))
            for p in edge:
                if p not in skip and _list_like(page_text(pdf, p)):
                    skip.add(p)
        finally:
            pdf.close()
    return skip


# OCR a book only when this share of its pages is image-only. Below it, image-only pages are full-page
# figures (Snell has ~100), and OCR would turn their labels into scattered-word passages.
OCR_BOOK_SHARE = 0.25


def scanned_pages(path: str | Path, n_pages: int, min_chars: int = 40) -> set[int]:
    """0-based pages with (almost) no text layer but an image on them."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c
    out: set[int] = set()
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            for p in range(n_pages):
                if len(page_text(pdf, p).strip()) >= min_chars:
                    continue
                page = pdf[p]
                try:
                    has_image = any(obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE for obj in page.get_objects())
                finally:
                    page.close()
                if has_image:
                    out.add(p)
        finally:
            pdf.close()
    return out
