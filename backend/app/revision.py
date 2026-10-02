"""Revision sheets: the fastest way to revise a topic from the user's own textbooks.

A *scope* is a set of books, optionally a chapter of them, optionally a topic string
inside that, and a length. Unlike a past-paper summary (app/rapid_review.py), which
starts from the questions, a book scope starts from the book:

  - a topic                retrieval restricted to the chosen books (and chapter)
  - a chapter, no topic    the heading is an anchor, not the whole: the read starts
                           there and continues onto the pages that follow in page
                           order, so a thin heading still yields a full page
  - a whole book, no topic  not a scope: a book is not a revision unit

A sheet reports how much of the scope it read (`coverage`), because a read that stopped
at the character budget must not look like a complete one.

The sheet is one page of high-yield bullets written ONLY from the passages passed to
the model. A bullet keeps its [Book, Page N] only when that book and page was one of
them, so a reference can never be invented; a mnemonic the model made up is labelled
"(AI mnemonic)" and kept, because it helps recall. Cached in topic_summaries under a
key that includes the books, the chapter, the topic and the length: a second visit is
instant, and asking for a different length is a different sheet rather than a shorter
one cut from a longer.

Also attached, because a revision page is not finished without them:
  - captioned, non-decorative figures from the same pages (retrieval.select_figures)
  - the questions the user has answered wrongly in these books, so the sheet can be
    aimed at their own gaps
  - how much of the scope was read, so a truncated chapter never reads as complete

Restricted questions are not involved (a book scope is not an exam scope), but a sheet
still inherits restricted access when the scope picks up any restricted question, in
case that changes.
"""

import hashlib
import json
import logging
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.llm import chat_completion, llm_configured
from app.models import AnswerEvent, Book, Chunk, Figure, MCQ, SavedSheet, TopicSummary, User
from app.rapid_review import INLINE_REF
from app.retention import _embed, restricted_allowed

logger = logging.getLogger(__name__)

# Bullets per sheet. "quick" is the one-minute read, "full" the evening-before page.
LENGTHS = {"quick": 10, "full": 22}
LENGTH_HINT = {"quick": "about 10 bullets", "full": "about 22 bullets"}

# How much of the scope is read. Parents (parent_id IS NULL) are whole paragraphs and
# are what retrieval expands to, so a chapter read from them is complete.
MAX_CHUNKS = 16
MAX_TOPIC_CHUNKS = 12
CHAR_BUDGET = 15000
MAX_FIGURES = 6
MAX_GAPS = 8

MIN_CHAPTER = 2          # the same junk slugs the chapter picker filters out
JUNK_CHAPTER = re.compile(r"mebooksfree|watermark|publisher|www\.|https?://", re.I)
# A leading enumerator ("10.13 ", "C. ", "148 CHAPTER 9 ") is the mark of a real
# heading; the "chapter" column is really the nearest heading, so page-number rows
# ("12", "3/12/16") and watermarks are filtered out and the rest ranked by how much
# they read like a title.
ENUMERATOR = re.compile(r"^\s*(?:\d+(?:\.\d+)*|\d+\s*[-–]\s*\d+|[A-Za-z])\s*[.):\-]?\s+")
WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
CHAPTER_RESULTS = 60    # the picker is a search box, so a short ranked list is enough

# An author byline ("Alan G Shand MD, FRCPE") is not a heading a student can revise
# from, yet every book's contributor pages land one in the chapter column. A byline is a
# heading that reads as a capitalised name followed by a run of degree/fellowship
# abbreviations. "Dirty Medicine" looks like that at first (both words are capitalised),
# so only a tail of *known credentials* counts, and at least one must be an unambiguous
# abbreviation (MD, PhD, what a reader would read as a qualification) rather than a word
# that happens to be one ("do", "Medicine"). MEDICINE is deliberately not a credential.
BYLINE_NAME = re.compile(r"^[A-Z][A-Za-z'.-]*")       # first name token / initial
_BYLINE_SPLIT = re.compile(r"[\s,;]+")
_BYLINE_CRED = frozenset("""
    MD MB MBBS MBChB BM BCh BMedSci BSc BS MS MSc MMed MHCM MMEd MMus MPhil MPH MA
    MSCE MSPT MHS DFM DPhil DSc DM DNB MCh PhD DTM DCH DLO DOHNS MFFLM MDiv CEPS
    MRCP MRCS MRCPI MRCPE MRCPG MRCGP MRCOG MRCOphth MRCPCH MRCPath MRCPsych
    MRCSED MRCSGLAS FRCP FRCS FRCOG FRCPI FRCPE FRCPG FRCPath FRCA FRCR FRCSEd
    FRCSGLAS FRCSC FRCSEng FRCSI FRACP FRCPA FCSANZ FACP FACC FACS FCCP FESC FMedSci
    FRSE FRS FHEA FAcadMEd FEBS FEBU FEBOT FEBOPRAS FIDSA FPIDS FAAAAI FAAP FSAHM FSAR
    FFPMRCA FASCRS FRACS FEAPU FAOrthA FACRS FDSRCS FDSRCPS MFDSRCS FFST FCMI AKC FLS
    FKC FREng OM PC KBE CBE MBE OBE FAMS FICS FCPS DNBE MNAMS FMAS FFSTM FFTM FHGSA
    MPHARM DO BMUS BPT MFSEM FFSEM LLB LLM FRCOphth FRSPH FRCPH RN FACMT MFPH FFPH
    FACMG FAAPM FACSM FCCC BMBCh MBBChir BChir
""".split())
_BYLINE_CRED_BASE = sorted({c.upper() for c in _BYLINE_CRED}, key=len, reverse=True)
# Credentials common enough as ordinary words that they only count when a second,
# unambiguous credential is present ("..., DO, MS" is a byline; "DO NOT ..." is not).
_BYLINE_STRONG = frozenset("""
    MD MB MBBS MBCHB BM BCH BMEDSCI BSC MS MSC MMED MHCM MMUS MPHIL MPH MA
    DPHIL DSC DM DNB MCH PHD DTM DCH DLO MRCP MRCS FRCP FRCS FRCA FRCR FRCPATH FRACP
    FMEDSCI FRSE FRS DO
""".split())
_BYLINE_WORD = frozenset({"TO", "ON", "IF", "OR", "IN", "AT", "BY", "AS", "OF", "FOR", "THE", "AND"})


def _byline_credential(token: str) -> bool:
    """Is this token a degree/fellowship abbreviation ('BSc(Hons)' counts as BSc)?"""
    base = re.sub(r"[^A-Za-z]", "", token).upper()
    if len(base) < 2 or base in _BYLINE_WORD:
        return False
    return any(base.startswith(c) or c.startswith(base) for c in _BYLINE_CRED_BASE)


def _is_byline(name: str) -> bool:
    """True for a heading that is an author byline ('Alan G Shand MD, FRCPE').

    The name part is capitalised words; the credentials that follow it are matched
    against the list above. The name run is extended one token at a time until the
    whole tail is made of credentials, so 'Brian J Angus BSc(Hons), DTM&H, MD' splits
    as a name plus three qualifications rather than as a name plus one.
    """
    n = (name or "").strip()
    m = BYLINE_NAME.match(n)
    if not m:
        return False
    pos = m.end()
    for _ in range(8):                     # a name longer than ~9 tokens is not a name
        m = re.match(r"\s+[A-Z][A-Za-z'.-]*", n[pos:])
        if not m:
            break
        pos += m.end()
        toks = [t for t in _BYLINE_SPLIT.split(n[pos:].strip()) if t]
        if not toks:
            continue
        if all(_byline_credential(t) for t in toks) and any(
                _byline_credential(t) and re.sub(r"[^A-Za-z]", "", t).upper() in _BYLINE_STRONG
                for t in toks):
            return True
    return False

SECTIONS = (
    "## Core concepts",
    "## Numbers & values",
    "## Classic presentations & associations",
    "## Look-alikes & traps",
    "## Mnemonics",
)


# ─── scope ────────────────────────────────────────────────────────────────────────────

def scope_allowed(scope: dict[str, Any]) -> bool:
    """A book needs to be about something: a chapter, or a topic to look for inside the books."""
    return bool(_book_ids(scope)) and bool((scope.get("chapter") or "").strip() or (scope.get("topic") or "").strip())


def _book_ids(scope: dict[str, Any]) -> list[int]:
    out: list[int] = []
    for b in scope.get("book_ids") or []:
        try:
            b = int(b)
        except (TypeError, ValueError):
            continue
        if b > 0 and b not in out:
            out.append(b)
    return sorted(out)


def scope_key(scope: dict[str, Any]) -> str:
    """Distinct from the past-paper key (which never has 'books'), so the two can never collide."""
    norm = {
        "books": _book_ids(scope),
        "chapter": (scope.get("chapter") or "").strip() or None,
        "topic": " ".join((scope.get("topic") or "").split()).lower() or None,
        "length": scope.get("length") if scope.get("length") in LENGTHS else "full",
    }
    return "books:" + hashlib.sha1(json.dumps(norm, sort_keys=True).encode()).hexdigest()


def scope_label(scope: dict[str, Any], titles: dict[int, str] | None = None) -> str:
    titles = titles or {}
    names = [titles.get(b, f"Book {b}") for b in _book_ids(scope)]
    books = ", ".join(names[:2]) + (f" +{len(names) - 2} more" if len(names) > 2 else "")
    bits = [books or "No book"]
    if (scope.get("chapter") or "").strip():
        bits.append((scope["chapter"] or "").strip())
    if (scope.get("topic") or "").strip():
        bits.append(" ".join((scope["topic"] or "").split()))
    return " · ".join(bits)


def available_books(db: Session) -> list[dict[str, Any]]:
    """Ready books for the picker, with how many headings each has.

    The headings themselves are not here: a big book has thousands of them (a 712 KB
    response, and a dropdown nobody can scan), so they are searched for on demand by
    search_chapters().
    """
    counts = dict(db.query(Chunk.book_id, func.count(func.distinct(Chunk.chapter)))
                  .filter(Chunk.chapter.isnot(None)).group_by(Chunk.book_id).all())
    return [{"id": b.id, "title": b.title, "total_pages": b.total_pages,
             "chapter_count": counts.get(b.id, 0)}
            for b in db.query(Book).filter(Book.status == "ready").order_by(Book.title).all()]


def _chapter_ok(name: str) -> bool:
    """A row that is a heading a student could search for, not a page number,
    a watermark, or an author byline."""
    s = (name or "").strip()
    if len(s) < MIN_CHAPTER or JUNK_CHAPTER.search(s) or not WORD.search(s) or _is_byline(s):
        return False
    return len(ENUMERATOR.sub("", s).strip()) >= 3


def _chapter_score(name: str) -> int:
    """Higher reads more like a real heading, so the best guesses come first."""
    body = ENUMERATOR.sub("", name)
    words = WORD.findall(body)
    score = 0
    if ENUMERATOR.match(name):
        score += 3
    score += 2 if len(words) >= 2 else 0
    score += 1 if len(words) >= 5 else 0
    score += 1 if 10 <= len(body) <= 70 else 0
    return score - (len(name) - len(body))     # a long bare number is not a title


def search_chapters(db: Session, book_ids: list[int], q: str = "", limit: int = CHAPTER_RESULTS
                    ) -> list[str]:
    """Headings from the chosen books that match `q`, best first. An empty `q` is a
    shortlist of the most heading-like ones, so the box is useful before typing.

    Matching is a plain substring over the heading, not a search index: a book's
    headings are only thousands of rows, and the exact name the user picks here is
    what the sheet is then read by.
    """
    book_ids = _book_ids({"book_ids": book_ids})
    if not book_ids:
        return []
    terms = [t for t in re.split(r"\s+", (q or "").strip().lower()) if len(t) > 1]
    rows = (db.query(Chunk.chapter).filter(Chunk.book_id.in_(book_ids), Chunk.chapter.isnot(None))
            .distinct().all())
    names = {n for (n,) in rows if _chapter_ok(n)}
    if terms:
        names = {n for n in names if all(t in n.lower() for t in terms)}
    scored = sorted(((_chapter_score(n) + (2 if q and q.strip().lower() == n.strip().lower() else 0), n)
                     for n in names), key=lambda t: (-t[0], t[1].lower()))
    return [n for _, n in scored[:limit]]


# ─── reading the scope ────────────────────────────────────────────────────────────────

def _read_chapter(db: Session, book_ids: list[int], chapter: str, budget: int = CHAR_BUDGET
                  ) -> tuple[list[Chunk], int, bool]:
    """Read from a chapter/section heading onwards, in page order.

    The chapter column is really the *nearest heading* and a heading usually covers
    only two or three paragraphs (in Davidson 55% cover exactly one), so a heading on
    its own is not a revision unit. The heading is therefore an anchor: its own
    paragraphs are read first, and the read then continues onto the pages that follow
    in the same book until the budget is spent, which is the slice a student actually
    means by "revise this section".

    Returns (chunks read, paragraphs the heading itself covers, extended).
    """
    like = f"%{chapter.strip()}%"
    own = (db.query(Chunk).filter(Chunk.book_id.in_(book_ids), Chunk.parent_id.is_(None),
                                  Chunk.chapter.ilike(like))
           .order_by(Chunk.book_id, Chunk.page_number, Chunk.id).all())
    if not own:
        return [], 0, False

    read: list[Any] = []
    seen: set[int] = set()
    used = 0

    def take(rows: list[Chunk]) -> bool:
        nonlocal used
        for c in rows:
            text = (c.content or "").strip()
            if not text or c.id in seen or used + len(text) > budget or len(read) >= MAX_CHUNKS:
                return False
            seen.add(c.id)
            read.append(c)
            used += len(text)
        return True

    if not take(own):
        return read, len(own), len(read) > len(own)
    # Continue past the heading in the book it was found in, up to the next pages.
    last = own[-1]
    for book_id in dict.fromkeys(b.book_id for b in own):
        if book_id != last.book_id:
            continue
        more = (db.query(Chunk).filter(Chunk.book_id == book_id, Chunk.parent_id.is_(None),
                                       Chunk.page_number > last.page_number)
                .order_by(Chunk.page_number, Chunk.id).all())
        if not more:
            continue
        if not take(more):
            break
    return read, len(own), len(read) > len(own)


def _read_topic(db: Session, book_ids: list[int], chapter: str | None, topic: str) -> list[Chunk]:
    """Passages about a topic, restricted to the chosen books (and chapter when given)."""
    from app.retrieval import retrieval_service

    per_book = max(3, MAX_TOPIC_CHUNKS // max(1, len(book_ids)) + 2)
    picked: list[Any] = []
    seen: set[int] = set()
    for book_id in book_ids:
        for c in retrieval_service.search(db, topic, limit=per_book, book_id=book_id,
                                          chapter=chapter or None).context:
            if c.id not in seen:
                seen.add(c.id)
                picked.append(c)
    return picked


# ─── the sheet ────────────────────────────────────────────────────────────────────────

def get_cached(db: Session, user: User, scope: dict[str, Any]) -> TopicSummary | None:
    row = db.query(TopicSummary).filter_by(scope_key=scope_key(scope)).first()
    if row is not None and row.access == "restricted" and not restricted_allowed(user):
        return None
    return row


def _passage_block(chunks: list[Any], titles: dict[int, str]) -> tuple[str, set[tuple[str, int]]]:
    text, valid = [], set()
    for c in chunks:
        title = c.book.title if getattr(c, "book", None) else titles.get(c.book_id, "Textbook")
        valid.add(((title or "").strip().lower(), c.page_number))
        text.append(f"[{title}, Page {c.page_number}]\n{(c.content or '').strip()}")
    return "\n\n".join(text), valid


def build(db: Session, user: User, scope: dict[str, Any]) -> TopicSummary:
    from app.retrieval import retrieval_service

    book_ids = _book_ids(scope)
    chapter = (scope.get("chapter") or "").strip()
    topic = " ".join((scope.get("topic") or "").split())
    length = scope.get("length") if scope.get("length") in LENGTHS else "full"
    books = db.query(Book).filter(Book.id.in_(book_ids), Book.status == "ready").all()
    if not books:
        raise RuntimeError("None of those books are ready to revise yet.")
    titles = {b.id: b.title for b in books}
    book_ids = [b.id for b in books]

    if topic:
        chunks = _read_topic(db, book_ids, chapter, topic)
        # Retrieval is a best match by design, not a sweep, so there is no whole to
        # have covered; say so rather than implying the book was read end to end.
        coverage = {"kind": "topic", "read": len(chunks), "own": None, "extended": False}
    else:
        chunks, own, extended = _read_chapter(db, book_ids, chapter)
        coverage = {"kind": "section", "read": len(chunks), "own": own, "extended": extended}
    if not chunks:
        raise RuntimeError(f"No text found in {scope_label(scope, titles)}. Try a different chapter or topic.")

    passages, valid = _passage_block(chunks, titles)
    label = scope_label(scope, titles)
    subject = (chapter or topic).strip()

    # The model writes only from these passages; a reference it invents is removed, not shown.
    written = ""
    if llm_configured("chat"):
        try:
            raw = chat_completion(
                [
                    {"role": "system", "content": (
                        f"You are writing a revision sheet that a medical student reads the night before an exam. "
                        f"Use ONLY the TEXTBOOK PASSAGES, and write {LENGTH_HINT[length]} in total, one line each. "
                        f"Sections in this order (skip any you cannot fill from the passages):\n"
                        + "\n".join(SECTIONS) + "\n"
                        "Rules: every bullet states one fact that is in a passage and ends with that passage's "
                        "reference exactly as shown, e.g. [Guyton Hall Physiology, Page 331]. Prefer the numbers, "
                        "thresholds, drug doses and named signs an examiner asks for. Never state anything the "
                        "passages do not say, and never write a reference to a page that is not above. A mnemonic "
                        "you invent yourself must end with '(AI mnemonic)' instead of a reference. "
                        "Reply with the markdown only."
                    )},
                    {"role": "user", "content": f"SUBJECT: {subject}\n\nTEXTBOOK PASSAGES:\n{passages[:CHAR_BUDGET]}"},
                ],
                temperature=0.1, max_tokens=2400, label="revision-sheet", role="chat",
            )
            written = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", (raw or "").strip()).strip()
        except Exception as e:
            logger.warning("Revision sheet failed for %s: %s", label, e)

    citations: list[dict[str, Any]] = []
    seen_cite: set[tuple[str, int]] = set()

    def keep_ref(m: re.Match) -> str:
        title, page = m.group(1).strip(), int(m.group(2))
        if (title.lower(), page) in valid and (title, page) not in seen_cite:
            seen_cite.add((title, page))
            citations.append({"book_title": title, "page_number": page})
        return m.group(0) if (title.lower(), page) in valid else ""

    kept: list[str] = []
    for line in written.splitlines():
        if line.lstrip().startswith(("-", "*")):
            checked = INLINE_REF.sub(keep_ref, line).rstrip()
            if INLINE_REF.search(checked) or "(AI mnemonic)" in checked:
                kept.append(checked)
        elif line.strip():
            kept.append(line.rstrip())
    # a heading left with no bullets under it is just noise
    for i in range(len(kept) - 1, -1, -1):
        if kept[i].startswith("#") and (i + 1 == len(kept) or kept[i + 1].startswith("#")):
            del kept[i]
    markdown = "\n".join(kept)
    if not markdown:
        # Never cache a failure: the next request tries again.
        raise RuntimeError("The sheet could not be written from those pages. Try again in a moment, or a "
                           "different chapter.")

    figures = _figures(db, chunks, titles, subject)
    # Aimed at the subject: the chapter when there is one, else the topic. A sheet for
    # "acute glomerulonephritis" should not offer the user's missed appendicitis question.
    gaps = _gaps(db, user, book_ids, chapter or topic)
    if gaps:
        markdown += "\n\n## Where you are weakest\n" + "\n".join(
            f"- {g['stem']} → **{g['answer']}** _{g['times']}× missed_ {g['source']}" for g in gaps)

    restricted = any(
        access == "restricted" for (access,) in
        db.query(MCQ.access).filter(MCQ.book_id.in_(book_ids)).distinct().all())

    key = scope_key(scope)
    row = db.query(TopicSummary).filter_by(scope_key=key).first() or TopicSummary(scope_key=key)
    row.label = label
    row.markdown = markdown
    row.citations = citations
    row.figures = [{"id": f["id"], "figure_label": f.get("figure_label") or "Figure",
                    "caption": f.get("caption"), "page_number": f.get("page_number"),
                    "book_title": f.get("book_title")} for f in figures]
    row.coverage = {**coverage, "chars": sum(len(c.content or "") for c in chunks)}
    row.key_count = len(gaps)
    row.access = "restricted" if restricted else "open"
    db.add(row)
    db.commit()
    logger.info("Revision sheet %s: %d bullets, %d citations, %d figures, %d gaps, read %s",
                key, len(kept), len(citations), len(figures), len(gaps), row.coverage)
    return row


def _figures(db: Session, chunks: list[Any], titles: dict[int, str], subject: str) -> list[dict[str, Any]]:
    """Captioned, non-decorative figures on the pages just read, ranked against the subject."""
    from app.retrieval import retrieval_service

    if not chunks:
        return []
    try:
        picked = retrieval_service.select_figures(db, [list(map(float, _embed([subject])[0]))], chunks)
    except Exception as e:
        logger.warning("Figure selection failed: %s", e)
        return []
    out = []
    for f in picked[:MAX_FIGURES]:
        f = dict(f)
        f["book_title"] = titles.get(f.get("book_id")) or f.get("book_title")
        out.append(f)
    return out


def _gaps(db: Session, user: User, book_ids: list[int], chapter: str) -> list[dict[str, Any]]:
    """This user's wrong answers in these books, most repeated first. No AI, straight from their events."""
    q = (db.query(MCQ.id, MCQ.question_text, MCQ.options, MCQ.correct_option, MCQ.sub_category,
                  func.count(AnswerEvent.id).label("n"), func.max(AnswerEvent.created_at).label("last"))
         .join(AnswerEvent, AnswerEvent.mcq_id == MCQ.id)
         .filter(AnswerEvent.user_id == user.id, AnswerEvent.is_correct.is_(False),
                 MCQ.book_id.in_(book_ids), MCQ.status == "ready")
         .group_by(MCQ.id, MCQ.question_text, MCQ.options, MCQ.correct_option, MCQ.sub_category)
         .order_by(func.count(AnswerEvent.id).desc(), func.max(AnswerEvent.created_at).desc()))
    terms = _chapter_terms(chapter)
    out = []
    for mid, stem, options, key, sub, n, _last in q.limit(MAX_GAPS * 2).all():
        answer = (options or {}).get(key, "")
        if not answer:
            continue
        if terms and not _mentions(stem, answer, terms):
            continue
        stem = " ".join(stem.split())
        out.append({"id": mid, "stem": stem if len(stem) <= 130 else stem[:127] + "...",
                    "answer": " ".join(str(answer).split()), "times": int(n), "source": sub or "Textbook"})
        if len(out) >= MAX_GAPS:
            break
    return out


# Words too common to identify a chapter, so "acute ..." cannot mean every question
# that happens to say "acute".
_STOP = {"and", "the", "of", "in", "for", "with", "from", "into", "their", "these", "those"}


def _chapter_terms(chapter: str) -> list[str]:
    """The content words of a chapter name, longest first."""
    words = {w for w in re.split(r"[^a-z0-9]+", (chapter or "").lower()) if len(w) > 3 and w not in _STOP}
    return sorted(words, key=len, reverse=True)


def _mentions(stem: str, answer: str, terms: list[str]) -> bool:
    """Does this question belong to the chapter? A name like "acute glomerulonephritis"
    must not match every question containing "acute", so one of its distinctive (long)
    words has to appear. Only a name with no long word at all has to match on all of them."""
    if not terms:
        return True
    hay = f"{stem} {answer}".lower()
    distinctive = [t for t in terms if len(t) >= 6]
    if distinctive:
        return any(t in hay for t in distinctive)
    return all(t in hay for t in terms)


def serialize(row: TopicSummary, cached: bool) -> dict[str, Any]:
    return {
        "label": row.label, "markdown": row.markdown,
        "citations": row.citations or [], "figures": row.figures or [],
        "coverage": row.coverage or {},
        "gap_count": row.key_count, "cached": cached,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ─── the user's saved sheets (Study Corner) ─────────────────────────────────────────

def save_sheet(db: Session, user: User, scope: dict[str, Any], label: str) -> SavedSheet:
    """Remember a freshly written sheet under this user's Study Corner.

    A row is only added when the (user, scope) pair is new, so revisiting a sheet
    keeps its original "saved on" date. The cached content in topic_summaries is
    shared, so this is a bookmark, not a copy.
    """
    existing = db.query(SavedSheet).filter_by(user_id=user.id, scope_key=scope_key(scope)).first()
    if existing is not None:
        return existing
    row = SavedSheet(
        user_id=user.id,
        scope_key=scope_key(scope),
        label=label or scope_label(scope),
        book_ids=_book_ids(scope),
        chapter=(scope.get("chapter") or "").strip() or None,
        topic=" ".join((scope.get("topic") or "").split()).strip() or None,
        length=scope.get("length") if scope.get("length") in LENGTHS else "full",
    )
    db.add(row)
    db.commit()
    return row


def saved_sheets(db: Session, user: User) -> list[dict[str, Any]]:
    """The user's saved revision sheets, newest first (Study Corner)."""
    rows = (db.query(SavedSheet).filter_by(user_id=user.id)
            .order_by(SavedSheet.created_at.desc(), SavedSheet.id.desc()).all())
    return [{
        "id": s.id,
        "label": s.label,
        "book_ids": [b for b in (s.book_ids or []) if isinstance(b, int) and b > 0],
        "chapter": s.chapter,
        "topic": s.topic,
        "length": s.length if s.length in LENGTHS else "full",
        "created_at": s.created_at.isoformat() if s.created_at else None,
    } for s in rows]


def delete_saved_sheet(db: Session, user: User, sheet_id: int) -> bool:
    """Remove one saved entry from Study Corner; the shared cached sheet stays."""
    row = db.query(SavedSheet).filter_by(user_id=user.id, id=sheet_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
