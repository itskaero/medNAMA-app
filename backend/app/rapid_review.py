"""Rapid Review: fast study of a topic.

A *scope* is either a past-paper selection (exam + optional years + subject/topic/specialty tags)
or a bank category (main_category + sub_category). For a scope:

  keys()     every question as a one-liner "stem -> answer", most-asked first (how many past-paper
             years it appeared in), with the explanation for expanding. No AI, instant.
  summary()  one cached page of high-yield points for the topic, written from the scope's most-asked
             keys plus textbook passages from retrieval. Inline [Book, Page N] references are kept only
             when they match a retrieved passage; facts taken from keys alone are marked as such.

Restricted questions (imported past papers) follow access_scope(); a summary built from restricted
keys is itself restricted.
"""

import hashlib
import json
import logging
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.llm import chat_completion, llm_configured
from app.models import MCQ, AnswerEvent, PastPaper, PastPaperQuestion, TopicSummary, User
from app.past_papers import one_per_recall_group, past_paper_filter, paper_years
from app.retention import access_scope, restricted_allowed

logger = logging.getLogger(__name__)

TOPIC_AXES = ("subject", "topic", "specialty", "system")   # what a scope is about (not which archive / sitting)

SUMMARY_KEYS = 60
KEYS_PAGE_MAX = 200
INLINE_REF = re.compile(r"\[([^\[\]\n]{2,80}?),\s*(?:Page|p\.)\s*(\d{1,4})\]", re.I)


def _scope_query(db: Session, user: User, scope: dict[str, Any]):
    q = access_scope(db.query(MCQ).filter(MCQ.status.in_(("ready", "pending"))), user)
    if scope.get("exam"):
        q = past_paper_filter(db, q, scope["exam"], scope.get("years"), scope.get("tags"))
    else:
        if scope.get("main"):
            q = q.filter(MCQ.main_category == scope["main"])
        if scope.get("sub"):
            q = q.filter(MCQ.sub_category == scope["sub"])
    return q


def _years_count_subquery(db: Session, exam: str | None):
    yq = (db.query(PastPaperQuestion.mcq_id.label("mcq_id"), func.count(func.distinct(PastPaper.year)).label("n"))
          .join(PastPaper, PastPaper.id == PastPaperQuestion.paper_id))
    if exam:
        yq = yq.filter(PastPaper.exam == exam)
    return yq.group_by(PastPaperQuestion.mcq_id).subquery()


def keys(db: Session, user: User, scope: dict[str, Any], order: str = "most_asked", only_missed: bool = False,
         offset: int = 0, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(KEYS_PAGE_MAX, int(limit or 100)))
    q = _scope_query(db, user, scope)
    if scope.get("exam"):   # a question both archives recalled is one key
        q = one_per_recall_group(db, q)
    if only_missed:
        per_q = (db.query(AnswerEvent.mcq_id, func.bool_or(AnswerEvent.is_correct).label("ok"))
                 .filter(AnswerEvent.user_id == user.id).group_by(AnswerEvent.mcq_id).subquery())
        q = q.join(per_q, per_q.c.mcq_id == MCQ.id).filter(~per_q.c.ok)
    total = q.order_by(None).count()
    yc = _years_count_subquery(db, scope.get("exam"))
    q = q.outerjoin(yc, yc.c.mcq_id == MCQ.id)
    if order == "topic":
        q = q.order_by(MCQ.topic, MCQ.id)
    else:
        # Most asked first: years including reworded repeats (asked_years), else distinct paper years.
        asked = func.coalesce(func.jsonb_array_length(MCQ.asked_years), yc.c.n, 0)
        q = q.order_by(asked.desc(), MCQ.topic, MCQ.id)
    rows = q.offset(max(0, int(offset or 0))).limit(limit).all()
    years = paper_years(db, [m.id for m in rows])
    return {
        "total": total,
        "offset": offset,
        "items": [{
            "id": m.id, "question_text": m.question_text, "options": m.options, "correct_option": m.correct_option,
            "answer": (m.options or {}).get(m.correct_option, ""), "topic": m.topic, "years": years.get(m.id, []),
            "explanation_markdown": m.explanation_markdown,
        } for m in rows],
    }


def _scope_label(scope: dict[str, Any]) -> str:
    if scope.get("exam"):
        labels = [lab for axis in TOPIC_AXES for lab in (scope.get("tags") or {}).get(axis, [])]
        return f"{scope['exam']}: {', '.join(labels) or 'all topics'}"
    return " / ".join(x for x in (scope.get("main"), scope.get("sub")) if x) or "Mixed"


def summary_key(scope: dict[str, Any]) -> str:
    """Years are left out on purpose: a topic's summary draws on its most-asked keys across all years."""
    norm = {
        "exam": scope.get("exam") or None,
        "tags": {a: sorted(v) for a, v in sorted((scope.get("tags") or {}).items()) if v},
        "main": scope.get("main") or None, "sub": scope.get("sub") or None,
    }
    return hashlib.sha1(json.dumps(norm, sort_keys=True).encode()).hexdigest()


def summary_allowed(scope: dict[str, Any]) -> bool:
    """A summary needs a topic to be about: some subject/topic/specialty label, or a bank sub-category."""
    tags = scope.get("tags") or {}
    return bool((scope.get("exam") and any(tags.get(a) for a in TOPIC_AXES)) or scope.get("sub"))


def get_summary(db: Session, user: User, scope: dict[str, Any]) -> TopicSummary | None:
    row = db.query(TopicSummary).filter_by(scope_key=summary_key(scope)).first()
    if row is not None and row.access == "restricted" and not restricted_allowed(user):
        return None
    return row


def build_summary(db: Session, user: User, scope: dict[str, Any]) -> TopicSummary:
    from app.retrieval import retrieval_service

    scope = {**scope, "years": None}   # all years: the summary covers the topic, not one sitting
    top = keys(db, user, scope, "most_asked", False, 0, SUMMARY_KEYS)["items"]
    labels = [lab for axis in TOPIC_AXES for lab in (scope.get("tags") or {}).get(axis, [])] or [scope.get("sub") or ""]
    topic = " ".join(labels).strip()

    # 1) Textbook passages: the topic with its dominant subject, plus the three most-asked questions.
    subject = None
    if top and scope.get("exam"):
        row = (db.query(MCQ.sub_category, func.count()).filter(MCQ.id.in_([k["id"] for k in top]),
                                                              MCQ.sub_category.notin_(("Mixed", "Minor Subjects")))
               .group_by(MCQ.sub_category).order_by(func.count().desc()).first())
        subject = row[0] if row else None
    queries = [f"{topic} {subject or ''}".strip()] + [
        f"{' '.join(k['question_text'].split())[:160]} {k['answer']}" for k in top[:3]]
    chunks, seen_chunks = [], set()
    for q in queries:
        for c in retrieval_service.search(db, q, limit=5).context:
            if c.id not in seen_chunks and len(chunks) < 10:
                seen_chunks.add(c.id)
                chunks.append(c)
    passages = "\n\n".join(
        f"[{c.book.title if c.book else 'Textbook'}, Page {c.page_number}]\n{c.content}" for c in chunks)

    # 2) AI writes ONLY textbook-backed bullets; each must carry a [Book, Page N] that matches a passage.
    written = ""
    if chunks and llm_configured("chat"):
        themes = "; ".join(" ".join(k["question_text"].split())[:90] for k in top[:25])
        try:
            raw = chat_completion(
                [
                    {"role": "system", "content": (
                        "Write rapid-revision notes for an FCPS candidate on the TOPIC, using ONLY the TEXTBOOK PASSAGES. "
                        "Focus on what the EXAM THEMES show is asked. Markdown sections (skip any you cannot fill):\n"
                        "## Core concepts\n## Numbers & values\n## Classic presentations & associations\n"
                        "## Look-alikes & traps\n## Mnemonics\n"
                        "Rules: every bullet states one fact found in a passage and ends with its reference exactly as "
                        "shown, e.g. [Guyton Hall Physiology, Page 331]. Never state anything the passages do not say. "
                        "A mnemonic you invent ends with '(AI mnemonic)' instead of a reference. At most 22 bullets in "
                        "total, one line each. Reply with the markdown only."
                    )},
                    {"role": "user", "content": f"TOPIC: {topic}\n\nEXAM THEMES: {themes}\n\nTEXTBOOK PASSAGES:\n{passages[:15000]}"},
                ],
                temperature=0.1, max_tokens=2200, label="rapid-review", role="chat",
            )
            written = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", (raw or "").strip()).strip()
        except Exception as e:
            logger.warning("Rapid review summary failed for %s: %s", topic, e)

    valid = {((c.book.title if c.book else "").strip().lower(), c.page_number) for c in chunks}
    citations: list[dict[str, Any]] = []

    def keep_ref(m: re.Match) -> str:
        title, page = m.group(1).strip(), int(m.group(2))
        if (title.lower(), page) in valid:
            if not any(c["book_title"] == title and c["page_number"] == page for c in citations):
                citations.append({"book_title": title, "page_number": page})
            return m.group(0)
        return ""   # an invented or mismatched reference is removed, never shown as a citation

    kept_lines = []
    for line in written.splitlines():
        if line.lstrip().startswith(("-", "*")):
            checked = INLINE_REF.sub(keep_ref, line).rstrip()
            # A bullet survives only with a verified reference (or as a labelled AI mnemonic).
            if INLINE_REF.search(checked) or "(AI mnemonic)" in checked:
                kept_lines.append(checked)
        elif line.strip():
            kept_lines.append(line.rstrip())
    # drop section headings left without bullets
    textbook_md = []
    for i, line in enumerate(kept_lines):
        if line.startswith("#") and (i + 1 >= len(kept_lines) or kept_lines[i + 1].startswith("#")):
            continue
        textbook_md.append(line)

    # 3) Most-asked past-paper questions: straight from the keys, answers verbatim (no AI, nothing paraphrased).
    asked_md = []
    for k in top[:15]:
        stem = " ".join(k["question_text"].split())
        stem = stem if len(stem) <= 150 else stem[:147] + "..."
        yrs = f" _({', '.join(map(str, k['years']))})_" if k["years"] else ""
        asked_md.append(f"- {stem} → **{k['answer']}**{yrs}")
    parts = []
    if textbook_md:
        parts.append("\n".join(textbook_md))
    if asked_md:
        parts.append("## Most-asked past-paper questions\n" + "\n".join(asked_md))
    if not parts:
        # Never cache a failure: the next request tries again.
        raise RuntimeError("The summary could not be written right now; try again in a moment.")
    markdown = "\n\n".join(parts)
    restricted = scope.get("exam") and any(m.access == "restricted" for m in db.query(MCQ.access).filter(
        MCQ.id.in_([k["id"] for k in top])).distinct())
    key = summary_key(scope)
    row = db.query(TopicSummary).filter_by(scope_key=key).first() or TopicSummary(scope_key=key)
    row.label = _scope_label(scope)
    row.markdown = markdown
    row.citations = citations
    row.key_count = len(top)
    row.access = "restricted" if restricted else "open"
    db.add(row)
    db.commit()
    return row


def serialize_summary(row: TopicSummary, cached: bool) -> dict[str, Any]:
    return {"label": row.label, "markdown": row.markdown, "citations": row.citations or [],
            "key_count": row.key_count, "cached": cached, "created_at": row.created_at.isoformat() if row.created_at else None}
