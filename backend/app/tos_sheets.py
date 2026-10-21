"""The TOS topic workspace's two reading sheets, written once and cached in topic_summaries.

  refresher  scope key 'tosref:<exam>:<paper>:<topic id>[#<subtopic slug>]'
  notes      scope key 'tos:<exam>:<paper>:<topic id>[#<subtopic slug>]'

The app writes a sheet the first time someone opens it (routers/tos.py). Seed Studio pre-writes them for a whole
paper on the PC (labs/seed_studio, "TOS sheets"), and scripts/nas_sync_tos_sheets.py copies them to the NAS, so
learners there get them instantly. One generator and one set of keys serve all three.

Both sheets come only from retrieved textbook passages. With no passages there is no sheet, never one written
from the model's memory.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import TopicSummary
from app.tos_tags import seed_topic_ids, slug

logger = logging.getLogger("app.tos_sheets")

KINDS = ("refresher", "notes")

REFRESHER_PROMPT = """You are revising with a postgraduate candidate (PGET / FCPS) who has ALREADY studied this topic.
Write a compact concept refresher for: {scope}
{subtopics}
Use ONLY the numbered passages below. Cite them inline as [n]. If the passages do not cover something, leave it out:
never fill gaps from memory.

Sections (Markdown, short):
### Core mechanisms
cause → effect chains, not prose.
### Key relationships & distinctions
a compact table where two or more things are easily confused.
### Clinical correlations
### High-yield exceptions & exam traps

Passages:
{passages}"""

NOTES_PROMPT = """Write a revision sheet a candidate can read in 1–3 minutes, for: {scope}
{subtopics}
Use ONLY the numbered passages; cite [n]. Leave out anything they do not support.
Use buzzwords, numbers and cut-offs, one comparison table, classic associations, and exam traps. Dense bullets, no prose.

Passages:
{passages}"""


class SheetUnavailable(Exception):
    """The AI is not configured or did not answer (the caller decides how to report it)."""


def scope_key(kind: str, exam_id: str, paper_id: str, topic_id: str, subtopic: str | None = None) -> str:
    prefix = "tosref" if kind == "refresher" else "tos"
    return f"{prefix}:{exam_id}:{paper_id}:{topic_id}" + (f"#{slug(subtopic)}" if subtopic else "")


def scope_label(subject: dict, topic: dict, subtopic: str | None) -> str:
    return " · ".join(x for x in (subject["subject_name"], topic["topic_name"], subtopic) if x)


def retrieve(db: Session, query: str, limit: int = 6) -> tuple[list[str], list[dict[str, Any]]]:
    """Numbered passages and their citations. The topic name is already a clean query, so the LLM query rewrite and
    the second-stage reranker are skipped: on the NAS's CPU they took ~60 s of a ~73 s sheet (PC: 24.9 s -> 1.2 s)."""
    from app.retrieval import retrieval_service

    try:
        result = retrieval_service.search(db, query, limit=limit, rewrite=False, deep_rerank=False)
    except Exception as e:
        logger.warning("TOS retrieval failed for %r: %s", query, e)
        return [], []
    passages, citations, seen = [], [], set()
    for i, c in enumerate(result.context, 1):
        title = c.book.title if c.book else "Textbook"
        passages.append(f"[{i}] {title}, {c.chapter or 'chapter n/a'}, p.{c.page_number or '?'}\n{c.content[:1400]}")
        if (title, c.page_number) not in seen:
            seen.add((title, c.page_number))
            citations.append({"n": i, "book_title": title, "chapter": c.chapter, "page_number": c.page_number,
                              "book_id": c.book_id})
    return passages, citations


def search_query(scope: str, subtopics: list[str], max_words: int = 18) -> str:
    """A focused search: the topic, then the start of its first two subtopics, at most `max_words` words.

    The whole subtopic list (often 40+ words) made the keyword search's OR fallback match nearly every passage and
    rank them all: one query took up to 3.5 minutes on the PC database and stalled sheet writing."""
    words = scope.replace("·", " ").split()
    for st in subtopics[:2]:
        words += st.replace(",", " ").split()[:6]
    seen, out = set(), []
    for w in words:
        k = w.lower().strip("():;.-")
        if k and k not in seen:
            seen.add(k)
            out.append(w.strip("():;"))
    return " ".join(out[:max_words])


def generate(db: Session, kind: str, scope: str, subtopics: list[str]) -> tuple[str, list[dict]] | None:
    """Write a sheet from the passages, or None when the library has none on this topic."""
    from app.llm import chat_completion, llm_configured

    passages, citations = retrieve(db, search_query(scope, subtopics))
    if not passages:
        return None
    if not llm_configured():
        raise SheetUnavailable("The AI model is not configured on this server.")
    prompt = REFRESHER_PROMPT if kind == "refresher" else NOTES_PROMPT
    sub_line = f"Subtopics in the TOS: {', '.join(subtopics)}" if subtopics else ""
    try:
        md = chat_completion([{"role": "user", "content": prompt.format(
            scope=scope, subtopics=sub_line, passages="\n\n".join(passages))}], temperature=0.2, label=f"tos-{kind}")
    except Exception as e:
        logger.warning("TOS %s generation failed for %s: %s", kind, scope, e)
        raise SheetUnavailable("The AI model did not answer. Try again in a moment.")
    return md, citations


def cache(db: Session, key: str, label: str, md: str, citations: list[dict]) -> None:
    try:
        db.add(TopicSummary(scope_key=key, label=label, markdown=md, citations=citations, access="open"))
        db.commit()
    except Exception as e:      # a concurrent request cached it first
        db.rollback()
        logger.info("TOS sheet %s not cached: %s", key, e)


def seed_sheets(db: Session, exam_id: str, paper: dict, topic_id: str, subtopic: str | None) -> list[dict]:
    """Seed Studio's validated revision notes for this topic: one per subtopic, or one for the topic itself when
    the TOS lists no subtopics (Seed Studio writes a note per leaf)."""
    from app.tos import find_topic

    nodes = {nid: sub for nid, (tid, sub) in seed_topic_ids(db, exam_id, paper).items() if tid == topic_id}
    if subtopic:
        nodes = {nid: sub for nid, sub in nodes.items() if sub == subtopic}
    if not nodes:
        return []
    try:
        rows = db.execute(text(
            "SELECT DISTINCT ON (n.node_id) n.node_id, s.label, s.markdown, s.citations "
            "FROM seed_notes n JOIN topic_summaries s ON s.scope_key = n.summary_key "
            "WHERE n.status = 'done' AND n.node_id = ANY(:ids) ORDER BY n.node_id, n.created_at DESC"),
            {"ids": list(nodes)}).fetchall()
    except Exception:
        db.rollback()
        return []
    order = {sub: i for i, sub in enumerate(find_topic(paper, topic_id)[1].get("subtopics", []))}
    sheets = [{"subtopic": nodes[r[0]] or "", "label": r[1], "markdown": r[2], "citations": r[3] or []} for r in rows]
    return sorted(sheets, key=lambda x: -1 if not x["subtopic"] else order.get(x["subtopic"], 999))


def get_sheet(db: Session, kind: str, exam_id: str, paper: dict, subject: dict, topic: dict,
              subtopic: str | None = None, *, write: bool = True) -> dict[str, Any]:
    """The sheet for a topic (or subtopic): Seed Studio's notes first, then the cache, then (write=True) a new
    one. Raises SheetUnavailable when the AI is needed but cannot answer."""
    label = scope_label(subject, topic, subtopic)
    if kind == "notes":
        seeded = seed_sheets(db, exam_id, paper, topic["id"], subtopic)
        if seeded:
            return {"available": True, "cached": True, "origin": "seed", "label": label, "sheets": seeded,
                    "markdown": seeded[0]["markdown"] if subtopic else "", "citations": seeded[0]["citations"]}
    key = scope_key(kind, exam_id, paper["id"], topic["id"], subtopic)
    hit = db.query(TopicSummary).filter(TopicSummary.scope_key == key).first()
    if hit:
        return {"available": True, "cached": True, "origin": "cache", "label": hit.label, "markdown": hit.markdown,
                "citations": hit.citations or [], "sheets": []}
    if not write:
        return {"available": False, "label": label, "markdown": "", "citations": [], "sheets": [], "missing": True}
    made = generate(db, kind, label, [subtopic] if subtopic else topic.get("subtopics", []))
    if made is None:
        what = "refresher" if kind == "refresher" else "sheet"
        return {"available": False, "label": label, "markdown": "", "citations": [], "sheets": [],
                "message": f"The textbook library has no passages on this topic yet, so there is no {what} "
                           "rather than one written from memory."}
    md, citations = made
    cache(db, key, label, md, citations)
    return {"available": True, "cached": False, "origin": "generated", "label": label, "markdown": md,
            "citations": citations, "sheets": []}


def prewrite(db: Session, exam_id: str, paper: dict, kinds: tuple[str, ...] = KINDS, subtopics: bool = False,
             on_progress=None) -> dict[str, int]:
    """Write every missing sheet of a paper (Seed Studio, on the PC). Topic level by default; subtopics too on request."""
    counts = {"written": 0, "cached": 0, "no_passages": 0, "failed": 0}
    targets = [(s, t, None) for s in paper["subjects"] for t in s["topics"]]
    if subtopics:
        targets += [(s, t, st) for s in paper["subjects"] for t in s["topics"] for st in t.get("subtopics", [])]
    total = len(targets) * len(kinds)
    done = 0
    for subject, topic, sub in targets:
        for kind in kinds:
            done += 1
            try:
                before = get_sheet(db, kind, exam_id, paper, subject, topic, sub, write=False)
                if before.get("available"):
                    counts["cached"] += 1
                else:
                    r = get_sheet(db, kind, exam_id, paper, subject, topic, sub)
                    counts["written" if r.get("available") else "no_passages"] += 1
            except SheetUnavailable as e:
                counts["failed"] += 1
                logger.warning("prewrite %s %s: %s", kind, topic["id"], e)
            if on_progress:
                on_progress(done, total, f"{kind}: {scope_label(subject, topic, sub)}", counts)
    return counts
