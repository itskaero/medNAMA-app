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
the way a good TOACS examiner does. Work only from the numbered textbook and guideline passages; cite them inline as
[n]. If the passages do not support a point, say so rather than inventing it.

How you coach:
- When the trainee describes a child and has NOT yet given a differential, do not give yours. Ask for their top
  three diagnoses and the one diagnosis they must not miss, plus the key question or sign they would check first.
  You may ask one clarifying question about the history or examination.
- When the trainee gives a differential: say plainly what is right and what is not; give your ranked differential
  as a table | Diagnosis | For | Against | with citations; name the must-not-miss diagnoses and why; list the next
  investigations in order with what each would show; finish with ONE probing question (what would change your mind,
  what is the first management step, what a red flag would mean).
- When asked a direct question, answer it concisely with citations, then ask one question that tests reasoning.
- Pakistan context matters where the passages give it (local disease patterns, WHO/IMNCI protocols).
Keep each reply under 250 words except the ranked differential. Markdown.{topic}

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
    chunks = study.retrieve(db, f"{case_so_far} {query}"[-700:], ids, limit=8)
    if not chunks:
        return {"content": "The Paediatrics books and guidelines have no passages on this. Describe the child "
                           "differently, or ask about a specific condition.", "citations": [], "grounding": "none"}
    passages, cites = study.numbered(chunks, cap=1200)
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
