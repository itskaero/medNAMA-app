"""Tables of Specifications: register the PDFs, read every page into rows, build the topic tree, check the counts.

Why page images: the UHS PGET booklet prints its tables in columns (subject | topic | sub-topics | number of MCQs)
that text extraction interleaves, so a count lands next to the wrong topic. Each page is rendered and read by the
vision model (Gemini, the same key and fallbacks as Check an answer's photo reader), which sees the table as laid
out. The page's extracted text is kept too, for the source view and as a fallback when no vision key is set.

Counts are transcribed, never computed: a count is stored only on the item the page prints it for. Where the stated
numbers do not add up (topics summing to more than their subject, a paper not totalling 100) the node is flagged
ambiguous with the arithmetic, for an operator to settle; nothing is silently corrected.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import settings
from .common import TransientError, audit, llm_json, session

from app.seed_models import SeedTosDocument, SeedTosNode, SeedTosPage  # noqa: E402

logger = logging.getLogger("seed_studio.tos")

# Known documents (data/ is gitignored; the files were downloaded on 2026-10-09).
KNOWN = {
    "pget_tos/TOSPGETNEW.pdf": dict(
        family="PGET", title="UHS PGET Table of Specifications, Papers I and II",
        version="January 2027 intake (notification of 9 July 2026)",
        source_url="https://www.uhs.edu.pk/downloads/TOSPGETNEW.pdf", has_counts=True),
}
FCPS_TITLES = {
    "anaesthesiology": "Anaesthesiology", "community-medicine": "Community Medicine", "dentistry": "Dentistry",
    "diagnostic-radiology": "Diagnostic Radiology", "ent-otorhinolaryngology": "ENT (Otorhinolaryngology)",
    "medicine-allied": "Medicine & Allied", "obstetrics-gynaecology": "Obstetrics & Gynaecology",
    "ophthalmology": "Ophthalmology", "pathology": "Pathology", "psychiatry": "Psychiatry",
    "surgery-allied": "Surgery & Allied",
}
SKIP_FILES = {"notification_pgettos.pdf"}   # the scanned notification, not a table

LEVEL_ORDER = ("paper", "specialty", "subject", "topic", "subtopic")
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

_parse_threads: dict[int, threading.Thread] = {}
# pdfium is not thread-safe: every PDF open/render/text call goes through this lock (they take milliseconds; the
# slow part, the vision call, runs in parallel outside it).
_PDF_LOCK = threading.Lock()
_parse_lock = threading.Lock()


# ─── registry ─────────────────────────────────────────────────────────────────────────────────────

def register_documents() -> int:
    """Add a document row for every TOS PDF under data/ that is not registered yet. Returns how many were added."""
    import pypdfium2 as pdfium

    added = 0
    with session() as db:
        have = {d.source_path for d in db.query(SeedTosDocument)}
        candidates = sorted(list((settings.DATA / "pget_tos").glob("*.pdf")) + list((settings.DATA / "fcps1_tos").glob("*.pdf")))
        for path in candidates:
            rel = path.relative_to(settings.DATA).as_posix()
            if f"data/{rel}" in have or path.name in SKIP_FILES:
                continue
            meta = KNOWN.get(rel)
            if meta is None and rel.startswith("fcps1_tos/"):
                spec = FCPS_TITLES.get(path.stem, path.stem.replace("-", " ").title())
                meta = dict(family="FCPS", title=f"CPSP FCPS Part I prospectus: {spec}",
                            version="CPSP prospectus (downloaded 2026-10-09)", source_url=None, has_counts=False)
            if meta is None:
                continue
            with _PDF_LOCK:
                pdf = pdfium.PdfDocument(str(path))
                pages = len(pdf)
                pdf.close()
            doc = SeedTosDocument(source_path=f"data/{rel}", page_count=pages, **meta)
            db.add(doc)
            db.flush()
            audit(db, "system", "tos_register", "document", doc.id, path=doc.source_path, pages=pages)
            added += 1
        db.commit()
    return added


def pdf_path(doc: SeedTosDocument) -> Path:
    return settings.REPO / doc.source_path


def render_page(doc: SeedTosDocument, page_no: int, scale: float = 1.6) -> bytes:
    """PNG of one page (1-based)."""
    import pypdfium2 as pdfium

    with _PDF_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path(doc)))
        try:
            image = pdf[page_no - 1].render(scale=scale).to_pil()
        finally:
            pdf.close()
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def page_text(doc: SeedTosDocument, page_no: int) -> str:
    import pypdfium2 as pdfium

    with _PDF_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path(doc)))
        try:
            return pdf[page_no - 1].get_textpage().get_text_range() or ""
        finally:
            pdf.close()


# ─── reading one page ─────────────────────────────────────────────────────────────────────────────

PROMPT = """You are transcribing ONE page of an official medical examination syllabus / Table of Specifications.
Document: {title} ({family}). This is page {page} of {pages}.
Running context from the previous page (a table may continue without repeating these headings): {context}

Return JSON only:
{{"is_syllabus": true, "rows": [{{"paper": "...", "specialty": null, "subject": "...", "topic": null, "subtopic": null,
  "count": null, "count_level": null, "count_note": null}}],
 "context_after": {{"paper": null, "specialty": null, "subject": null, "topic": null}}}}

Rules:
- One row per subject heading, per topic and per sub-topic, in reading order. A sub-topic row repeats its subject and
  topic; a topic row has subtopic null; a subject row has topic and subtopic null.
- Join words that wrap across lines. Keep the printed wording; drop bullets, numbering and symbols.
- Items nested under a sub-topic (a sub-sub-topic) are sub-topic rows named "Parent item: nested item", e.g.
  "Parasympathetic Nervous System: Overview", so an "Overview" or "Management" never stands alone.
- "paper": "Paper I" or "Paper II". When a Paper I is for MBBS or for BDS graduates, write "Paper I (MBBS)" or
  "Paper I (BDS)" every time, so the two never merge. Carry it from context.
- "specialty": the specialty a Paper II belongs to (e.g. "Paediatrics", "Medicine & Allied"), else null. {spec_hint}
- "count": ONLY a number printed on this page as the number of MCQs for that exact item (a 'Number of MCQs' column, or
  text like "MCQs-3" / "(MCQs--2)" next to a topic). Set "count_level" to "paper", "subject", "topic" or "subtopic".
  Never compute, estimate, split or copy a count onto other items. If unsure which item a number belongs to, leave it
  null and describe it in "count_note".
- "count_note": printed qualifiers about counts, e.g. "Minimum of two MCQs from each topic".
- A summary table of subjects with their MCQ counts: one subject row each, count_level "subject".
- A "Total 100" line: a row with only "paper" (and specialty for Paper II), count 100, count_level "paper".
- Reference-book lists, contributor lists, messages, cover or preamble pages: is_syllabus false, rows [].
- "context_after": the paper / specialty / subject / topic still open at the bottom of the page."""


def _retry_delay(e: Exception) -> float | None:
    """Seconds Google asks us to wait (free tier: 5 requests a minute per model), or None for a daily quota."""
    msg = str(e)
    if "PerDay" in msg or "per day" in msg.lower():
        return None
    m = re.search(r"retry in ([\d.]+)s", msg) or re.search(r"'retryDelay': '(\d+)s'", msg)
    return float(m.group(1)) if m else 20.0


def _vision_rows(image: bytes, prompt: str) -> tuple[dict, str]:
    """Read a page image with the vision model, honouring rate limits: a 429 waits the delay the API asks for and
    tries again (up to three times per model); a daily quota or an unavailable model moves to the next model."""
    from openai import APIStatusError

    from app.config import settings as app_settings
    from app.photo_mcq import FALLBACK_MODELS, _get_client, configured

    if not configured():
        raise RuntimeError("no vision key")
    url = f"data:image/png;base64,{base64.b64encode(image).decode()}"
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]}]
    models = list(dict.fromkeys([app_settings.vision_model, *FALLBACK_MODELS]))
    last: Exception | None = None
    for model in models:
        for _ in range(3):
            try:
                resp = _get_client().chat.completions.create(model=model, temperature=0, messages=messages,
                                                             response_format={"type": "json_object"})
                raw = (resp.choices[0].message.content or "").strip()
                if raw.startswith("```"):
                    raw = raw.strip("`").removeprefix("json").strip()
                return json.loads(raw), model
            except APIStatusError as e:
                last = e
                if e.status_code == 429:
                    delay = _retry_delay(e)
                    if delay is None or delay > 90:
                        break                      # daily quota: try another model
                    time.sleep(delay + 1)
                    continue
                if e.status_code in (404, 500, 503):
                    break
                raise
            except json.JSONDecodeError as e:
                last = e
                break
    if last is not None and getattr(last, "status_code", None) == 429:
        raise TransientError("The vision models' free-tier quota is used up for now; read the missing pages again "
                             "later, or set a paid GEMINI_API_KEY.")
    raise TransientError(f"vision models unavailable: {str(last)[:300]}")


def read_page(doc: SeedTosDocument, page_no: int, context: dict) -> tuple[dict, str, str]:
    """(parsed JSON, model used, page text). Vision when configured, else the text with the chat model."""
    text = page_text(doc, page_no)
    spec_hint = ""
    if doc.family == "FCPS":
        spec_hint = f"This whole document is the FCPS Part I prospectus for {doc.title.split(': ', 1)[-1]}."
    prompt = PROMPT.format(title=doc.title, family=doc.family, page=page_no, pages=doc.page_count,
                           context=json.dumps(context), spec_hint=spec_hint)
    try:
        out, model = _vision_rows(render_page(doc, page_no), prompt)
        return out, model, text
    except RuntimeError as e:
        if "no vision key" not in str(e):
            raise
    out, res = llm_json([{"role": "user", "content": prompt + "\n\nPAGE TEXT (columns may be interleaved):\n" + text[:12000]}],
                        role="chat", max_tokens=6000, temperature=0, label="tos-page")
    return out, f"text:{res.model}", text


# ─── parsing a document (background) ──────────────────────────────────────────────────────────────

def parse_running(doc_id: int) -> bool:
    t = _parse_threads.get(doc_id)
    return bool(t and t.is_alive())


def start_parse(doc_id: int, actor: str, restart: bool = False) -> bool:
    """Parse in the background; resumes at the first page not yet read unless `restart`."""
    with _parse_lock:
        if parse_running(doc_id):
            return False
        with session() as db:
            doc = db.get(SeedTosDocument, doc_id)
            if doc is None:
                raise KeyError(doc_id)
            if restart:
                db.query(SeedTosPage).filter(SeedTosPage.document_id == doc_id).delete()
            doc.parse_status, doc.parse_error = "parsing", None
            audit(db, actor, "tos_parse_start", "document", doc_id, restart=restart)
            db.commit()
        t = threading.Thread(target=_parse_worker, args=(doc_id,), name=f"tos-parse-{doc_id}", daemon=True)
        _parse_threads[doc_id] = t
        t.start()
        return True


PARSE_CONCURRENCY = 4   # pages read at once; the headings are stitched afterwards (see stitch_rows)


def _read_one(doc_id: int, page_no: int) -> None:
    """Read one page in its own session and store the result (failures too, for a later retry)."""
    with session() as db:
        doc = db.get(SeedTosDocument, doc_id)
        row = db.query(SeedTosPage).filter(SeedTosPage.document_id == doc_id, SeedTosPage.page_no == page_no).first()             or SeedTosPage(document_id=doc_id, page_no=page_no)
        try:
            out, model, text = read_page(doc, page_no, {})
            rows = [r for r in (out.get("rows") or []) if isinstance(r, dict)]
            after = out.get("context_after")
            if rows and isinstance(after, dict):
                rows[-1]["_context_after"] = after
            row.text, row.model, row.error, row.rows = text, model, None, rows
            row.status = "done" if out.get("is_syllabus", True) and rows else "skipped"
        except Exception as e:   # one bad page must not lose the others; it is retried on the next parse
            logger.warning("TOS %s page %s failed: %s", doc_id, page_no, e)
            row.status, row.error = "failed", f"{type(e).__name__}: {e}"[:500]
        db.add(row)
        db.commit()


def _parse_worker(doc_id: int) -> None:
    from concurrent.futures import ThreadPoolExecutor

    try:
        with session() as db:
            doc = db.get(SeedTosDocument, doc_id)
            have = {p.page_no for p in db.query(SeedTosPage).filter(SeedTosPage.document_id == doc_id,
                                                                    SeedTosPage.status.in_(("done", "skipped")))}
            todo = [n for n in range(1, (doc.page_count or 0) + 1) if n not in have]
        # Pages are read independently (no running context), several at once; continuation pages that do not
        # repeat their headings get them from the pages before when the tree is built (stitch_rows).
        with ThreadPoolExecutor(max_workers=PARSE_CONCURRENCY) as pool:
            list(pool.map(lambda n: _read_one(doc_id, n), todo))
        with session() as db:
            doc = db.get(SeedTosDocument, doc_id)
            failed = db.query(SeedTosPage).filter(SeedTosPage.document_id == doc_id, SeedTosPage.status == "failed").count()
            curated = (doc.parse_method or "").startswith("curated")
            if not curated:   # a tree imported from curated JSON is the source of truth; page readings stay as reference
                build_tree(db, doc)
                doc.parse_method = ", ".join(sorted({p.model for p in db.query(SeedTosPage).filter(
                    SeedTosPage.document_id == doc_id, SeedTosPage.model.isnot(None))}))[:200]
            doc.parse_status = "parsed" if (curated or not failed) else "failed"
            doc.parse_error = f"{failed} page(s) could not be read; parse again to retry them." if failed and not curated else None
            doc.parsed_at = datetime.utcnow()
            audit(db, "system", "tos_parse_done", "document", doc_id, failed_pages=failed)
            db.commit()
    except Exception as e:
        logger.exception("TOS parse %s failed", doc_id)
        with session() as db:
            doc = db.get(SeedTosDocument, doc_id)
            doc.parse_status, doc.parse_error = "failed", f"{type(e).__name__}: {e}"[:500]
            db.commit()


def stitch_rows(pages: list, family: str | None = None) -> list[tuple[int, dict]]:
    """[(page_no, row)] in reading order with missing headings filled from the rows before.

    A row that names a deeper level but leaves a higher one empty (a continuation page that does not repeat
    "Paper II > Paediatrics > Infectious diseases") takes that heading from the running context. A row that names
    a level resets everything below it, so a new subject never inherits the previous subject's topic."""
    levels = ("paper", "specialty", "subject", "topic")
    ctx: dict = {}
    closed: set[str] = set()      # papers already finished earlier in the document
    out = []
    for p in pages:
        for r in p.rows or []:
            r = {k: v for k, v in r.items()}
            pap = clean_name(r.get("paper"))
            # "Paper II Operative Dentistry": the paper is Paper II, the rest is the specialty.
            m = re.match(r"(?i)^(paper\s+ii)\b[\s:,\-]+(.+)$", pap or "")
            if m:
                r["paper"] = "Paper II"
                r["specialty"] = r.get("specialty") or m.group(2)
                pap = "Paper II"
            if family == "FCPS" and pap:
                # FCPS prospectuses have plain Paper I / Paper II (no MBBS/BDS versions).
                r["paper"] = pap = re.sub(r"\s*\(.*\)\s*$", "", pap)
            if family == "PGET":
                # In the PGET booklet only Paper II is split by specialty.
                if clean_name(r.get("specialty")) and not _key(pap or "").startswith("paper ii"):
                    r["paper"] = pap = "Paper II"
                # An unqualified "Paper I" inside an open Paper II specialty is a mislabel, unless the row is a
                # paper total (the real start of the next paper).
                elif pap and _key(pap) == "paper i" and r.get("count_level") != "paper"                         and _key(ctx.get("paper") or "").startswith("paper ii") and ctx.get("specialty"):
                    r["paper"] = pap = ctx["paper"]
            # A row naming a paper finished pages ago (a mislabelled continuation) stays in the paper now open.
            if pap and ctx.get("paper") and _key(pap) != _key(ctx["paper"]) and _key(pap) in closed:
                r["paper"] = ctx["paper"]
            # The model sometimes marks a specialty's or subject's own count as "paper" level.
            if r.get("count_level") == "paper":
                if clean_name(r.get("subject")):
                    r["count_level"] = "subject"
                elif clean_name(r.get("specialty")):
                    r["count_level"] = "specialty"
            named = [lvl for lvl in LEVEL_ORDER if clean_name(r.get(lvl))]
            if not named:
                continue
            deepest = max(LEVEL_ORDER.index(lvl) for lvl in named)
            # "Paper I" on a continuation page means the qualified Paper I already open ("Paper I (BDS)").
            pap = clean_name(r.get("paper"))
            if pap and ctx.get("paper") and pap != ctx["paper"] and _key(ctx["paper"]).startswith(_key(pap) + " "):
                r["paper"] = ctx["paper"]
            for i, lvl in enumerate(levels):
                if i >= deepest:
                    break
                if not clean_name(r.get(lvl)) and ctx.get(lvl):
                    # a Paper I row never inherits a Paper II specialty
                    if lvl == "specialty" and clean_name(r.get("paper")) and clean_name(r.get("paper")) != ctx.get("paper"):
                        continue
                    r[lvl] = ctx[lvl]
            for i, lvl in enumerate(levels):
                if clean_name(r.get(lvl)):
                    if _key(ctx.get(lvl) or "") != _key(clean_name(r.get(lvl))):
                        if lvl == "paper" and ctx.get("paper"):
                            closed.add(_key(ctx["paper"]))
                        for deeper in levels[i + 1:]:
                            ctx.pop(deeper, None)
                    ctx[lvl] = clean_name(r.get(lvl))
            # A total printed at the end of a Paper II specialty's table belongs to that specialty, not the paper.
            if r.get("count_level") == "paper" and not clean_name(r.get("specialty")) and ctx.get("specialty")                     and (clean_name(r.get("paper")) or "").lower().startswith("paper ii"):
                r["specialty"], r["count_level"] = ctx["specialty"], "specialty"
            out.append((p.page_no, r))
    return out


# ─── building and checking the tree ───────────────────────────────────────────────────────────────

def clean_name(s: Any) -> str | None:
    if s is None:
        return None
    s = re.sub(r"\(?\s*MCQs?\s*-+\s*\d+\s*\)?", "", str(s), flags=re.I)
    s = re.sub(r"\(\s*\d+\s*(?:MCQs?)?\s*\)\s*$", "", s, flags=re.I)   # "Anatomy (20)": the count is not the name
    s = re.sub(r"^\s*(?:\d+[.)]?|[ivx]+\))\s+", "", s)
    s = re.sub(r"[•●▪‣⁃·�]", " ", s)
    s = " ".join(s.split()).strip(" -–:;,.")
    return s or None


def _key(s: str) -> str:
    """Matching key for a heading: spelling variants of one item map together ("Obstetrics & Gynaecology",
    "Obstetrics and Gynecology"); the first spelling seen is the one displayed."""
    k = s.lower().replace("&", " and ")
    for uk, us in (("ae", "e"), ("oe", "e"), ("our", "or"), ("isation", "ization")):
        k = k.replace(uk, us)
    return re.sub(r"[^a-z0-9]+", " ", k).strip()


def min_per_child(note: str | None) -> int | None:
    if not note:
        return None
    m = re.search(r"minimum\s+(?:of\s+)?(\d+|one|two|three|four|five)\b", note, re.I)
    if not m:
        return None
    v = m.group(1).lower()
    return int(v) if v.isdigit() else NUMBER_WORDS[v]


def build_tree(db, doc: SeedTosDocument, rows: list[tuple[int | None, dict]] | None = None) -> int:
    """(Re)build the document's nodes from its page rows, keeping every node's identity.

    Nodes are matched by their heading path, so a re-read updates names, counts and pages in place: jobs, batches,
    questions and notes stay attached to the same node ids. A heading no longer found is deleted only if no work
    ever touched it; otherwise it is kept and flagged. Operator target overrides and count corrections survive."""
    from app.seed_models import SeedBatch, SeedItem, SeedNote

    existing = db.query(SeedTosNode).filter(SeedTosNode.document_id == doc.id).all()
    by_id_old = {n.id: n for n in existing}

    def key_path(n: SeedTosNode) -> tuple:
        parts, a = [], n
        while a is not None and a.parent_id is not None:
            parts.append(_key(a.name))
            a = by_id_old.get(a.parent_id)
        return tuple(reversed(parts))

    old_index = {key_path(n): n for n in existing if n.parent_id is not None}
    root = next((n for n in existing if n.parent_id is None), None)
    if root is None:
        root = SeedTosNode(document_id=doc.id, level="family", name=doc.family, path=doc.family, position=0)
        db.add(root)
        db.flush()
    for n in existing:   # counts and flags are re-derived from the pages; operator corrections are kept
        if not n.count_corrected:
            n.official_count = None
        n.count_note, n.min_per_child, n.ambiguous, n.ambiguity_note, n.detail = None, None, False, None, None
    index: dict[tuple, SeedTosNode] = {(): root}
    counter = [0]

    def node_for(names: list[tuple[str, str]], page_no: int) -> SeedTosNode:
        parent, keyp = root, ()
        for level, name in names:
            keyp = keyp + (_key(name),)
            n = index.get(keyp)
            if n is None:
                counter[0] += 1
                n = old_index.get(keyp)
                if n is None:
                    n = SeedTosNode(document_id=doc.id, parent_id=parent.id, level=level, name=name,
                                    path=f"{parent.path} > {name}", position=counter[0], page_no=page_no)
                    db.add(n)
                    db.flush()
                else:
                    n.parent_id, n.level, n.position = parent.id, level, counter[0]
                    n.page_no = page_no if page_no is not None else n.page_no   # JSON rows carry no page: keep the link
                    n.path = f"{parent.path} > {n.name}"
                index[keyp] = n
            parent = n
        return parent

    if rows is None:   # from the page readings; a curated JSON import passes its own fully qualified rows
        pages = db.query(SeedTosPage).filter(SeedTosPage.document_id == doc.id, SeedTosPage.status == "done") \
            .order_by(SeedTosPage.page_no).all()
        rows = stitch_rows(pages, doc.family)
    for page_no, r in rows:
        names = [(lvl, clean_name(r.get(lvl))) for lvl in LEVEL_ORDER]
        names = [(lvl, n) for lvl, n in names if n]
        if not names:
            continue
        target = node_for(names, page_no)
        count, level = r.get("count"), r.get("count_level")
        note = clean_name(r.get("count_note"))
        if level in LEVEL_ORDER:
            # The count belongs to the item at its stated level, not to a sub-topic listed on the same row.
            upto = [(lvl, n) for lvl, n in names if LEVEL_ORDER.index(lvl) <= LEVEL_ORDER.index(level)]
            if upto:
                target = node_for(upto, page_no)
        if isinstance(count, (int, float)) or (isinstance(count, str) and count.strip().isdigit()):
            c = int(count)
            if target.count_corrected:
                pass   # an operator corrected this count against the source page; keep it
            elif target.official_count is not None and target.official_count != c:
                target.ambiguous = True
                target.ambiguity_note = _append(target.ambiguity_note,
                                                f"Two different counts printed: {target.official_count} and {c} (page {page_no}).")
            elif c >= 0:
                target.official_count = c
        if r.get("detail"):
            target.detail = _append(target.detail, " ".join(str(r["detail"]).split()))
        if note:
            target.count_note = _append(target.count_note, note)
            target.min_per_child = target.min_per_child or min_per_child(note)

    seen = {n.id for n in index.values()}
    used = {i for (i,) in db.query(SeedBatch.node_id).distinct()} | {i for (i,) in db.query(SeedItem.node_id).distinct()}         | {i for (i,) in db.query(SeedNote.node_id).distinct()}
    gone = [n for n in existing if n.id not in seen]
    # delete leaves first; anything that has work attached (or a kept child) stays, flagged
    for n in sorted(gone, key=lambda n: -n.path.count(" > ")):
        has_kids = db.query(SeedTosNode.id).filter(SeedTosNode.parent_id == n.id).first() is not None
        if n.id in used or has_kids:
            n.ambiguous = True
            n.ambiguity_note = "No longer found when the document was last read; kept because work is attached to it."
        else:
            db.delete(n)
            db.flush()
    nodes = db.query(SeedTosNode).filter(SeedTosNode.document_id == doc.id).all()
    check_counts(nodes, doc.has_counts)
    db.flush()
    return len(nodes)


def _append(text: str | None, more: str) -> str:
    if not text:
        return more
    return text if more in text else f"{text} {more}"


def check_counts(nodes: list[SeedTosNode], has_counts: bool) -> None:
    """Flag (never fix) stated counts that do not add up."""
    kids: dict[int | None, list[SeedTosNode]] = {}
    for n in nodes:
        kids.setdefault(n.parent_id, []).append(n)
    by_id = {n.id: n for n in nodes}
    for n in nodes:
        children = kids.get(n.id, [])
        stated = [c.official_count for c in children if c.official_count is not None]
        if n.official_count is not None and stated:
            total = sum(stated)
            if total > n.official_count or (len(stated) == len(children) and total != n.official_count):
                n.ambiguous = True
                n.ambiguity_note = _append(n.ambiguity_note,
                                           f"Stated counts of its parts add up to {total}, not the {n.official_count} stated here.")
        # (Paper II is one paper per specialty: its parts are separate papers, not one 100-question total)
        if n.level == "paper" and n.official_count is None and stated and has_counts \
                and not any(c.level == "specialty" for c in children):
            n.ambiguity_note = _append(n.ambiguity_note, f"No paper total printed; its parts add up to {sum(stated)}.")
    if has_counts:
        def counted_below(n: SeedTosNode) -> bool:
            return any(c.official_count is not None or counted_below(c) for c in kids.get(n.id, []))

        for n in nodes:
            if n.level != "subject":
                continue
            # a subject with no count on it, above it or inside it cannot get an official target
            a, counted = n, counted_below(n)
            while a is not None and not counted:
                counted = a.official_count is not None
                a = by_id.get(a.parent_id)
            if not counted:
                n.ambiguous = True
                n.ambiguity_note = _append(n.ambiguity_note, "No MCQ count is printed for this subject or above it.")
