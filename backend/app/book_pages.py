"""Book page images for the page viewer: render a PDF page, find where a cited passage sits on it.

Pages are rendered with pdfium (already installed with Docling) one at a time behind a lock (pdfium is
not thread-safe, and one page at a time keeps memory at ~100 MB on the NAS), saved as WebP under the page
cache, and served from there afterwards. Highlights come from pdfium's character boxes: the passage is
matched against the page text after both are normalised (case, whitespace, hyphenation, ligatures), so a
chunk that Docling cleaned up still lines up with the raw PDF text.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import unicodedata
from collections import OrderedDict
from pathlib import Path

from app.book_meta import PDFIUM_LOCK
from app.config import settings

logger = logging.getLogger(__name__)

RENDER_WIDTH = 1100          # px; sharp on a laptop, ~150 KB as WebP
_open_docs: "OrderedDict[str, object]" = OrderedDict()
_MAX_OPEN = 3


def pdfs_dir() -> Path:
    if settings.pdfs_dir:
        return Path(settings.pdfs_dir)
    docker = Path("/app/pdfs")
    if docker.is_dir():
        return docker
    return Path(__file__).resolve().parent.parent.parent / "pdfs"


def cache_dir() -> Path:
    d = Path(settings.page_cache_dir) if settings.page_cache_dir else Path(tempfile.gettempdir()) / "mednama-pages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pdf_path(filename: str | None) -> Path | None:
    if not filename:
        return None
    p = pdfs_dir() / Path(filename).name
    return p if p.is_file() else None


def _doc(path: Path):
    """An open pdfium document, kept in a small LRU (opening a 300 MB PDF costs ~0.1 s). Hold PDFIUM_LOCK."""
    import pypdfium2 as pdfium
    key = str(path)
    if key in _open_docs:
        _open_docs.move_to_end(key)
        return _open_docs[key]
    doc = pdfium.PdfDocument(key)
    _open_docs[key] = doc
    while len(_open_docs) > _MAX_OPEN:
        _, old = _open_docs.popitem(last=False)
        try:
            old.close()
        except Exception:
            pass
    return doc


def page_count(path: Path) -> int:
    with PDFIUM_LOCK:
        return len(_doc(path))


def render_page(book_id: int, path: Path, page_number: int) -> bytes:
    """WebP bytes of a 1-based PDF page, from the cache when rendered before."""
    out = cache_dir() / f"b{book_id}" / f"p{page_number:05d}.webp"
    if out.is_file():
        return out.read_bytes()
    with PDFIUM_LOCK:
        doc = _doc(path)
        if not 1 <= page_number <= len(doc):
            raise IndexError(page_number)
        page = doc[page_number - 1]
        try:
            width_pt = page.get_width()
            bitmap = page.render(scale=RENDER_WIDTH / max(1.0, width_pt))
            img = bitmap.to_pil()
        finally:
            page.close()
    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "WEBP", quality=72, method=4)
    data = buf.getvalue()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    return data


# ─── highlight ───────────────────────────────────────────────────────────────

def _norm_char(ch: str) -> str:
    """Normalised form of one PDF character ('' to skip it)."""
    s = unicodedata.normalize("NFKC", ch)          # ligatures: 'ﬁ' -> 'fi'
    return "".join(c.lower() for c in s if c.isalnum())


def _norm_text(text: str) -> str:
    text = re.sub(r"-\s*\n\s*", "", text)          # hyphenation across lines
    return "".join(_norm_char(c) for c in text)


def _strip_prefix(text: str) -> str:
    """Child chunks start with 'Textbook: X | Chapter: Y | Page: N'; that line is not on the page."""
    if text.startswith("Textbook:"):
        nl = text.find("\n")
        return text[nl + 1:] if nl >= 0 else ""
    return text


def find_highlight(path: Path, page_number: int, passage: str, anchor_chars: int = 40) -> list[list[float]]:
    """Boxes [x0, y0, x1, y1] (0-1, top-left origin) covering the passage on the page; [] if not found.

    The passage is located by its first and last `anchor_chars` normalised characters, so a passage that
    runs onto the next page still highlights its part on this one.
    """
    target = _norm_text(_strip_prefix(passage or ""))
    if len(target) < 12:
        return []
    with PDFIUM_LOCK:
        doc = _doc(path)
        if not 1 <= page_number <= len(doc):
            return []
        page = doc[page_number - 1]
        try:
            width, height = page.get_size()
            tp = page.get_textpage()
            try:
                n = tp.count_chars()
                raw = tp.get_text_range(0, n) if n else ""
                # Map every normalised character back to its PDF character index.
                norm_chars: list[str] = []
                index_of: list[int] = []
                for i, ch in enumerate(raw[:n]):
                    for c in _norm_char(ch):
                        norm_chars.append(c)
                        index_of.append(i)
                page_norm = "".join(norm_chars)

                start_anchor = target[:anchor_chars]
                s = page_norm.find(start_anchor)
                if s < 0:                                  # passage began on the previous page
                    s = 0 if page_norm.find(target[len(target) // 2:][:anchor_chars]) >= 0 else -1
                end_anchor = target[-anchor_chars:]
                e = page_norm.find(end_anchor, max(0, s))
                e = e + len(end_anchor) if e >= 0 else -1
                if s < 0 and e < 0:
                    return []
                if s < 0:
                    s = 0
                if e < 0:                                  # runs onto the next page
                    e = min(len(page_norm), s + len(target))
                c0, c1 = index_of[s], index_of[max(s, e - 1)]
                boxes = []
                for r in range(tp.count_rects(c0, c1 - c0 + 1)):
                    left, bottom, right, top = tp.get_rect(r)
                    boxes.append([round(left / width, 4), round(1 - top / height, 4),
                                  round(right / width, 4), round(1 - bottom / height, 4)])
                return _merge_lines(boxes)
            finally:
                tp.close()
        finally:
            page.close()


def _merge_lines(boxes: list[list[float]]) -> list[list[float]]:
    """Join the per-word boxes pdfium returns into one box per line segment."""
    boxes.sort(key=lambda b: (round(b[1], 2), b[0]))
    out: list[list[float]] = []
    for b in boxes:
        if out:
            last = out[-1]
            same_line = abs(last[1] - b[1]) < 0.006 and abs(last[3] - b[3]) < 0.006
            if same_line and b[0] - last[2] < 0.03:
                last[2] = max(last[2], b[2])
                continue
        out.append(list(b))
    return out
