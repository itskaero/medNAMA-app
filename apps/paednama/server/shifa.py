"""Dr. Shifa (شفا, "healing"): PaedNAMA's tutor, answering only from the Paediatrics books and guidelines.

Three modes:
  explain    medNAMA's answer generator (books first, every [Book, Page] citation checked), scoped to the Paediatrics
             books, at FCPS-II depth.
  tutor      the same generator in its Socratic tutor mode: one question at a time, corrected from the books.
  reasoning  a clinical-reasoning coach: when you describe a child it asks for YOUR differential first, then ranks
             it, names the must-not-miss diagnoses and the next tests, and challenges your reasoning; cited [n].

The name is deliberately not a real paediatrician's: an AI under a real doctor's name would imply their endorsement.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from . import registry, settings  # noqa: F401  (backend/ on the path)
from . import study
from .models import PaedsChat, PaedsChatMessage

logger = logging.getLogger("paednama.shifa")

MODES = ("explain", "tutor", "reasoning")
HISTORY_TURNS = 10

REASONING_PROMPT = """You are Dr. Shifa, a paediatrics consultant coaching a trainee for CPSP {exam} in clinical reasoning,
the way a good TOACS examiner does. Work only from the numbered passages; cite them inline as [n]. If the passages do
not support a point, say so rather than inventing it.

The passages marked WORKED CASE come from case-based books (Case Files Pediatrics, decision-making texts). Coach in
their method, step by step:
  1. Summary: restate the case as a one-line problem representation (age, tempo, the key positives and negatives).
  2. Most likely diagnosis, with the findings that point to it.
  3. Must-not-miss diagnoses, and the one finding or test that rules each in or out.
  4. Next step: the single next investigation or action, and why it comes first.
  5. Key concepts: the facts an examiner will test (criteria, cut-offs, first-line treatment and doses).

How you coach:
- When the trainee describes a child and has NOT yet reasoned it through, do not give your answer. Ask them for
  step 1 and step 2 (their summary and their top three diagnoses with the must-not-miss), plus one clarifying
  question about the history or examination if a key detail is missing.
- When they reason: say plainly what is right and what is not, step by step. Give your ranked differential as a
  table | Diagnosis | For | Against | with citations; then the must-not-miss and the next step; then ONE probing
  question (what would change your mind, what the first management step is, what a red flag would mean).
- When asked for a case on a topic: write a short, realistic Pakistani ward or ER case (age, complaint, key findings,
  vitals) built from the passages, and ask for steps 1 and 2.
- When asked a direct question, answer it concisely with citations, then ask one question that tests reasoning.
- Pakistan context matters where the passages give it (local disease patterns, WHO/IMNCI and national protocols).
Keep each reply under 280 words except the ranked differential. Markdown.{topic}

Passages:
{passages}"""


def scope_ids(db: Session) -> list[int]:
    """Every ready Paediatrics book and guideline: the hard boundary for Dr. Shifa's sources."""
    from .routes import _ready_books

    return [b["id"] for b in _ready_books(db)]


def _history(db: Session, chat: PaedsChat) -> list[dict[str, str]]:
    msgs = (db.query(PaedsChatMessage).filter_by(chat_id=chat.id).order_by(PaedsChatMessage.id.desc())
            .limit(HISTORY_TURNS * 2).all())
    return [{"role": "user" if m.role == "user" else "assistant", "content": m.content} for m in reversed(msgs)]


def _level(stage: str) -> str:
    from app import exam_levels

    return exam_levels.FCPS_2     # Paediatrics IMM and FCPS-II are both clinical: management, drug choice, complications


def reasoning_passages(db: Session, query: str, ids: list[int]) -> tuple[list[Any], set[int]]:
    """Passages for a reasoning turn, in three layers: worked cases from the case-based books (Case Files, decision
    making), the textbooks for the facts, then a couple of guideline passages for current recommendations. A plain
    search over all 287 books lets the short guidelines crowd out both. Returns the passages and the numbers (1-based)
    of the worked-case ones."""
    from .routes import _ready_books

    books = [b for b in _ready_books(db) if b["id"] in set(ids)]
    reasoning = [b["id"] for b in books if b["role"] == "reasoning"]
    guidelines = [b["id"] for b in books if b["role"] == "guideline"]
    textbooks = [b["id"] for b in books if b["role"] not in ("reasoning", "guideline", "mcq")]
    out: list[Any] = []
    seen: set = set()

    def take(cs: list[Any], n: int, min_words: int = 25) -> None:
        for c in cs:
            key = c.id if c.id is not None else (c.book_id, c.page_number, c.content[:80])
            if n and key not in seen and not study.is_low_value(c.content, min_words):
                seen.add(key)
                out.append(c)
                n -= 1

    if reasoning:
        take(study.retrieve(db, query, reasoning, limit=6), 3)
    worked = set(range(1, len(out) + 1))
    take(study.retrieve(db, query, textbooks or ids, limit=8), 5)
    if guidelines:
        take(study.retrieve(db, query, guidelines, limit=5), 2, min_words=12)
    return out, worked


def _reasoning_turn(db: Session, chat: PaedsChat, text: str, history: list[dict], ids: list[int]) -> dict[str, Any]:
    topic_line = ""
    query = text
    if chat.topic_id:
        found = registry.find_topic(chat.stage, chat.topic_id)
        if found:
            topic_line = f"\nThis conversation is about the syllabus topic: {found[1]['name']}."
            query = f"{found[1]['name']} {text}"
    # Search on the whole case so far, not only the last line ("what next?" alone finds nothing).
    case_so_far = " ".join(m["content"] for m in history if m["role"] == "user")[-600:]
    chunks, worked = reasoning_passages(db, f"{case_so_far} {query}"[-700:], ids)
    if not chunks:
        return {"content": "The Paediatrics books and guidelines have no passages on this. Describe the child "
                           "differently, or ask about a specific condition.", "citations": [], "grounding": "none"}
    passages, cites = study.numbered(chunks, cap=1200)
    for c in cites:                        # mark the case-based passages so she coaches in their method
        if c["n"] in worked:
            passages = passages.replace(f"[{c['n']}] {c['book_title']}", f"[{c['n']}] WORKED CASE · {c['book_title']}", 1)
    system = REASONING_PROMPT.format(exam="IMM" if chat.stage == "imm" else "FCPS-II", topic=topic_line,
                                     passages=passages)
    try:
        md = study._llm([{"role": "system", "content": system}, *history[-HISTORY_TURNS * 2:],
                         {"role": "user", "content": text}], label="shifa-reasoning", max_tokens=1600)
    except Exception as e:
        logger.warning("Reasoning turn failed: %s", e)
        raise study.StudyUnavailable("Dr. Shifa did not answer. Try again in a moment.")
    md, cites = study.keep_valid_citations(md.strip(), cites)
    return {"content": md, "grounding": "textbook" if cites else "none",
            "citations": [{"n": c["n"], "book_title": c["book_title"], "page": c["page"], "chapter": c["chapter"]}
                          for c in cites]}


def _generator_turn(db: Session, chat: PaedsChat, text: str, history: list[dict], ids: list[int]) -> dict[str, Any]:
    from app.generation import generate_answer

    query = text
    if chat.topic_id:
        found = registry.find_topic(chat.stage, chat.topic_id)
        if found and found[1]["name"].lower() not in text.lower():
            query = f"{text} (topic: {found[1]['name']})"
    try:
        ans = generate_answer(session=db, query=query, history=history, level=_level(chat.stage),
                              mode="tutor" if chat.mode == "tutor" else None, book_ids=ids)
    except Exception as e:
        logger.warning("Shifa %s turn failed: %s", chat.mode, e)
        raise study.StudyUnavailable("Dr. Shifa did not answer. Try again in a moment.")
    return {"content": ans.get("answer_markdown") or "", "grounding": ans.get("grounding"),
            "citations": [{"book_title": c.get("book_title"), "page": c.get("page_number"), "excerpt": c.get("excerpt")}
                          for c in ans.get("citations") or []]}


def ask(db: Session, user_id: int, text: str, mode: str, stage: str, chat_id: int | None = None,
        topic_id: str | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Write a question or describe the child.")
    if mode not in MODES:
        raise ValueError("Unknown mode")
    if chat_id:
        chat = db.get(PaedsChat, chat_id)
        if chat is None or chat.user_id != user_id:
            raise LookupError("Conversation not found")
        chat.mode = mode
    else:
        words = text.split()
        chat = PaedsChat(user_id=user_id, title=" ".join(words[:8]) + ("…" if len(words) > 8 else ""), mode=mode,
                         stage=stage, topic_id=topic_id)
        db.add(chat)
        db.flush()
    history = _history(db, chat)
    ids = scope_ids(db)
    turn = (_reasoning_turn if mode == "reasoning" else _generator_turn)(db, chat, text, history, ids)
    db.add(PaedsChatMessage(chat_id=chat.id, role="user", content=text))
    reply = PaedsChatMessage(chat_id=chat.id, role="shifa", content=turn["content"], citations=turn["citations"],
                             grounding=turn.get("grounding"))
    db.add(reply)
    db.commit()
    return {"chat_id": chat.id, "title": chat.title, "message": serialize(reply)}


def serialize(m: PaedsChatMessage) -> dict[str, Any]:
    return {"id": m.id, "role": m.role, "content": m.content, "citations": m.citations or [], "grounding": m.grounding,
            "at": m.created_at.isoformat() if m.created_at else None}


def chats(db: Session, user_id: int, topic_id: str | None = None) -> list[dict[str, Any]]:
    q = db.query(PaedsChat).filter_by(user_id=user_id)
    if topic_id:
        q = q.filter_by(topic_id=topic_id)
    return [{"id": c.id, "title": c.title, "mode": c.mode, "topic_id": c.topic_id,
             "updated_at": c.updated_at.isoformat() if c.updated_at else None}
            for c in q.order_by(PaedsChat.updated_at.desc()).limit(100).all()]


def chat_detail(db: Session, user_id: int, chat_id: int) -> dict[str, Any]:
    chat = db.get(PaedsChat, chat_id)
    if chat is None or chat.user_id != user_id:
        raise LookupError("Conversation not found")
    msgs = db.query(PaedsChatMessage).filter_by(chat_id=chat.id).order_by(PaedsChatMessage.id).all()
    return {"id": chat.id, "title": chat.title, "mode": chat.mode, "topic_id": chat.topic_id,
            "messages": [serialize(m) for m in msgs]}


def delete_chat(db: Session, user_id: int, chat_id: int) -> None:
    chat = db.get(PaedsChat, chat_id)
    if chat is None or chat.user_id != user_id:
        raise LookupError("Conversation not found")
    db.delete(chat)
    db.commit()
