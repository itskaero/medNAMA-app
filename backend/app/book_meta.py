"""What a book PDF says about itself: printed page labels, title, authors, edition, year, ISBN.

Printed page labels
-------------------
Chunks are cited by PDF page, which is 14-20 pages off the printed page in books with front matter
(Bailey: PDF page 400 is printed page 380). `page_labels()` returns one printed label per PDF page:

  1. The PDF's own page labels (/PageLabels), when the publisher set them (Bailey, Robbins, Davidson,
     First Aid, Nelson). Unprintable or default "1, 2, 3..." labels are ignored.
  2. Otherwise the number printed in the running header or footer: the first and last lines of every
     page are scanned for a bare number, and the offset (printed - PDF page) that most pages agree on
     is applied. Pages before the numbering starts get no label.

Metadata
--------
The PDF's info dictionary is right for about a third of books and junk for others ("A9R1qnyxdl.tmp.pdf",
"Vitalsource Download"). The title and copyright pages are reliable, but they also list earlier editions
("Twenty-seventh edition 2018 ... Twenty-eighth edition 2023"), so a regex can't tell which is current.
`extract_metadata()` sends the first pages' text to the AI for a JSON answer, then checks it: the ISBN must
appear in the text, the year and edition must be plausible. The admin confirms it (scripts/book_meta.py).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# pdfium is not thread-safe: every call into it, in any thread of this process, goes through this lock.
PDFIUM_LOCK = threading.RLock()

FRONT_PAGES = 15
ISBN_RE = re.compile(r"ISBN(?:-1[03])?[:\s]*((?:97[89][-\s]?)?\d[\d\-\s]{8,15}[\dX])", re.I)
_NUM_TOKEN = re.compile(r"^\d{1,4}$")
_ROMAN = re.compile(r"^[ivxlcdm]{1,7}$", re.I)

# FCPS subjects a book can serve (retention.PART1_SUBJECTS plus the Paper 2 faculties in the bank).
SUBJECTS = ["Anatomy", "Physiology", "Pathology", "Pharmacology", "Microbiology", "Biochemistry",
            "Behavioural Sciences", "Community Medicine", "Medicine", "Surgery", "ENT", "Paediatrics",
            "Gynaecology & Obstetrics", "Ophthalmology", "Radiology", "Anaesthesia", "Psychiatry",
            "Mixed (First Aid)"]


def _pdfium():
    import pypdfium2 as pdfium
    return pdfium


def page_text(pdf, index: int) -> str:
    """Plain text of one page (0-based) of an open pdfium document. Caller holds PDFIUM_LOCK."""
    page = pdf[index]
    try:
        tp = page.get_textpage()
        try:
            return tp.get_text_range() or ""
        finally:
            tp.close()
    finally:
        page.close()


# ─── printed page labels ─────────────────────────────────────────────────────

def _clean_label(label: Any) -> str | None:
    s = str(label or "").strip()
    if not s or len(s) > 12 or not re.match(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$", s):
        return None
    return s


def _embedded_labels(path: Path, n: int) -> list[str | None] | None:
    """The PDF's /PageLabels, when set and meaningful."""
    try:
        from pypdf import PdfReader
        import logging as _logging
        _logging.getLogger("pypdf").setLevel(_logging.ERROR)   # "Could not reliably determine page label" x100
        labels = list(PdfReader(path).page_labels)
    except Exception:
        return None
    if len(labels) != n:
        return None
    if all(str(l) == str(i + 1) for i, l in enumerate(labels)):
        return None                                   # the default numbering: says nothing
    cleaned = [_clean_label(l) for l in labels]
    good = sum(1 for l in cleaned if l and (l.isdigit() or _ROMAN.match(l) or re.match(r"^\d+\.e\d+$", l)))
    return cleaned if good >= 0.8 * n else None


def _header_footer_numbers(text: str) -> set[int]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    edge = lines[:3] + lines[-3:]
    nums = set()
    for ln in edge:
        for tok in re.split(r"[\s|•·]+", ln):
            if _NUM_TOKEN.match(tok):
                nums.add(int(tok))
    return nums


def _longest_increasing(idx: list[int], values: list) -> set[int]:
    """Indices (from idx, in order) of the longest chain whose values strictly increase."""
    import bisect
    tails: list[int] = []        # tails[k] = value ending the best chain of length k+1
    tail_at: list[int] = []      # position in idx of that value
    prev = [-1] * len(idx)
    for pos, i in enumerate(idx):
        k = bisect.bisect_left(tails, values[i])
        if k == len(tails):
            tails.append(values[i]); tail_at.append(pos)
        else:
            tails[k] = values[i]; tail_at[k] = pos
        prev[pos] = tail_at[k - 1] if k else -1
    out: set[int] = set()
    pos = tail_at[-1] if tail_at else -1
    while pos >= 0:
        out.add(idx[pos])
        pos = prev[pos]
    return out


def _detected_labels(pdf, n: int) -> tuple[list[str | None] | None, float]:
    """Printed numbers read from running headers/footers.

    No single offset works for every book: PDFs with dropped blank pages or inserted plates drift
    (Guyton's offset changes dozens of times). So each page's candidate number is accepted only when a
    page within 2 either side continues the same sequence (page i shows v, page i+1 shows v+1), which
    also rejects chapter and figure numbers that happen to sit in a header. Gaps between two accepted
    pages are filled when the numbering runs straight through; the ends are extended outwards.
    """
    per_page: list[set[int]] = []
    with PDFIUM_LOCK:
        for i in range(n):
            try:
                per_page.append(_header_footer_numbers(page_text(pdf, i)))
            except Exception:
                per_page.append(set())

    valid: list[int | None] = [None] * n
    for i, nums in enumerate(per_page):
        best, best_support = None, 0
        for v in nums:
            support = sum(1 for d in (-2, -1, 1, 2) if 0 <= i + d < n and v + d in per_page[i + d])
            if support > best_support:
                best, best_support = v, support
        valid[i] = best
    # Printed numbers only go up through a book. Contents pages list runs of page numbers too
    # ("652" on Guyton's PDF page 1), so keep the longest strictly increasing chain and drop the rest.
    found = sorted(_longest_increasing([i for i in range(n) if valid[i] is not None], valid))
    keep = set(found)
    valid = [valid[i] if i in keep else None for i in range(n)]
    coverage = len(found) / max(1, n)
    if coverage < 0.2:
        return None, coverage

    printed: list[int | None] = list(valid)
    for a, b in zip(found, found[1:]):
        if b - a <= 1:
            continue
        if valid[b] - valid[a] == b - a:                       # straight run: fill the pages between
            for i in range(a + 1, b):
                printed[i] = valid[a] + (i - a)
        else:                                                  # a page dropped or inserted: count from both ends
            mid = (a + b) // 2
            for i in range(a + 1, b):
                v = valid[a] + (i - a) if i <= mid else valid[b] - (b - i)
                printed[i] = v if valid[a] < v < valid[b] else None
    for i in range(found[0] - 1, -1, -1):                     # before the first numbered page
        v = valid[found[0]] - (found[0] - i)
        if v < 1:
            break
        printed[i] = v
    for i in range(found[-1] + 1, n):                         # after the last one (index pages)
        printed[i] = valid[found[-1]] + (i - found[-1])

    labeled = sum(1 for v in printed if v)
    return [str(v) if v else None for v in printed], labeled / max(1, n)


def page_labels(path: str | Path) -> tuple[list[str | None] | None, str]:
    """(labels, source): one printed label per PDF page, or (None, reason)."""
    path = Path(path)
    pdfium = _pdfium()
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
    try:
        n = len(pdf)
        emb = _embedded_labels(path, n)
        if emb:
            return emb, "pdf-labels"
        det, conf = _detected_labels(pdf, n)
        if det:
            return det, f"header-footer ({conf:.0%} of pages labelled)"
        return None, f"no printed page numbers found ({conf:.0%} of pages)"
    finally:
        with PDFIUM_LOCK:
            pdf.close()


def label_for(labels: list | None, page_number: int | None) -> str | None:
    """Printed label of a 1-based PDF page, if known."""
    if not labels or not page_number or page_number < 1 or page_number > len(labels):
        return None
    return labels[page_number - 1]


# ─── title-page metadata ─────────────────────────────────────────────────────

def front_text(path: str | Path, pages: int = FRONT_PAGES, per_page: int | None = 2500) -> str:
    """Text of the first pages, each cut to `per_page` characters (None = whole page)."""
    pdfium = _pdfium()
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            parts = []
            for i in range(min(pages, len(pdf))):
                t = " ".join(page_text(pdf, i).split())
                if t:
                    parts.append(f"[PDF page {i + 1}] {t[:per_page] if per_page else t}")
            return "\n".join(parts)
        finally:
            pdf.close()


def pdf_info(path: str | Path) -> dict[str, str]:
    try:
        from pypdf import PdfReader
        info = PdfReader(str(path)).metadata or {}
        return {k.lstrip("/").lower(): str(v) for k, v in info.items() if v and k in ("/Title", "/Author", "/Subject")}
    except Exception:
        return {}


def isbns_in(text: str) -> list[str]:
    out = []
    for m in ISBN_RE.findall(text):
        digits = re.sub(r"[^\dX]", "", m.upper())
        if len(digits) in (10, 13) and digits not in out:
            out.append(digits)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", s or "").lower())


def _prompt(filename: str, info: dict, text: str) -> list[dict]:
    return [
        {"role": "system", "content": (
            "You read the title page and copyright page of a medical textbook and return its bibliographic "
            "details as JSON. Use only what the text says. The copyright page often lists earlier editions and "
            "their years; report the CURRENT edition (the newest one, the book itself) and its year.")},
        {"role": "user", "content": (
            f"File name: {filename}\nPDF properties (often wrong): {json.dumps(info)}\n\n"
            f"Text of the first pages:\n{text[:24000]}\n\n"
            "Return JSON with exactly these keys:\n"
            '{"full_title": "the title as printed, with subtitle only if part of the name",\n'
            ' "short_title": "how students name it + edition, max 32 characters, e.g. \\"Guyton & Hall 15e\\", '
            '\\"Robbins Basic Pathology 9e\\", \\"Bailey & Love 28e\\", \\"Davidson\'s Medicine 23e\\", '
            '\\"Pelczar Microbiology 5e\\" (add the author when the title alone is generic), '
            '\\"First Aid Step 1 2024\\"",\n'
            ' "authors": ["main authors or editors, at most 4, as printed"],\n'
            ' "edition": 15, "year": 2021, "publisher": "Elsevier", "isbn": "the print ISBN of this edition",\n'
            f' "subject": "one of {SUBJECTS} that this book mainly serves for the FCPS Part 1 exam, or null"}}\n'
            "Use null for anything the text does not state.")},
    ]


def extract_metadata(path: str | Path) -> dict[str, Any]:
    """Proposed metadata for a PDF: AI reading of the first pages, checked against the text."""
    from app.llm import chat_completion, llm_configured

    path = Path(path)
    text = front_text(path)
    info = pdf_info(path)
    isbns = isbns_in(front_text(path, per_page=None))   # the copyright line is often far down its page
    result: dict[str, Any] = {"filename": path.name, "pdf_info": info, "isbns_seen": isbns}
    if not text.strip():
        result["error"] = "no text on the first pages (scanned?)"
        return result
    if not llm_configured("fast") and not llm_configured("chat"):
        result["error"] = "AI service is not configured"
        return result
    role = "fast" if llm_configured("fast") else "chat"
    raw = chat_completion(_prompt(path.name, info, text), json_mode=True, temperature=0.0, max_tokens=800,
                          label=f"book meta {path.name}", role=role)
    try:
        meta = json.loads(raw) or {}
    except json.JSONDecodeError:
        result["error"] = "AI answer was not JSON"
        return result

    warnings = []
    isbn = re.sub(r"[^\dX]", "", str(meta.get("isbn") or "").upper()) or None
    if isbn and isbn not in isbns:
        warnings.append(f"ISBN {isbn} not found in the text; dropped")
        isbn = None
    year = meta.get("year")
    if not isinstance(year, int) or not 1950 <= year <= 2035:
        year = None
    edition = meta.get("edition")
    if not isinstance(edition, int) or not 1 <= edition <= 60:
        edition = None
    full_title = (meta.get("full_title") or "").strip() or None
    if full_title and _norm(full_title)[:12] not in _norm(text):
        warnings.append("full title not found verbatim in the text; check it")
    subject = meta.get("subject") if meta.get("subject") in SUBJECTS else None
    short = (meta.get("short_title") or full_title or "").strip()[:40] or None
    authors = [a.strip() for a in (meta.get("authors") or []) if isinstance(a, str) and a.strip()][:4] or None
    result.update({"full_title": full_title, "short_title": short, "authors": authors, "edition": edition,
                   "year": year, "publisher": (meta.get("publisher") or None), "isbn": isbn,
                   "subject": subject, "warnings": warnings})
    return result
