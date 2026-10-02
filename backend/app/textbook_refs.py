"""Textbook page references for past-paper explanations.

Most past-paper questions came with the archive's own explanation but no textbook page behind it. This keeps
that explanation and adds a short "From your textbooks" section: two to four points, each a fact from a
passage the search returned, cited as [Book, Page]. A point whose passage number is not one we gave is dropped,
so every page shown is one the model actually read.

The Answer-Key Referee judges the key on the same passages first. When the textbooks contradict it, no
references are added: the question is tagged key-conflict and filed in the admin's Reports queue instead.

Each question is tagged refs=added|disputed|none, so the PC batch (scripts/prewarm_refs.py) never repeats one;
scripts/refs_transfer.py moves the sections to the NAS.
"""

import json
import logging
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.llm import chat_completion
from app.models import MCQ, MCQTag

logger = logging.getLogger(__name__)

REF_MARK = "**From your textbooks**"
PAST_CATEGORY = "Past papers · FCPS Part 1"


def _tag(db: Session, mcq_id: int, label: str) -> None:
    db.query(MCQTag).filter(MCQTag.mcq_id == mcq_id, MCQTag.axis == "refs").delete()
    db.add(MCQTag(mcq_id=mcq_id, axis="refs", label=label))


def candidates(db: Session, limit: int = 500, min_years: int = 1) -> list[int]:
    """The most-asked FCPS Part 1 past-paper questions that have not been through this yet."""
    done = db.query(MCQTag.mcq_id).filter(MCQTag.axis == "refs")
    asked = func.coalesce(func.jsonb_array_length(MCQ.asked_years), 0)
    rows = (db.query(MCQ.id).filter(MCQ.main_category == PAST_CATEGORY, MCQ.twist_of.is_(None),
                                    asked >= min_years, MCQ.id.notin_(done))
            .order_by(asked.desc(), MCQ.id).limit(limit).all())
    return [r[0] for r in rows]


def add_textbook_refs(db: Session, mcq: MCQ) -> dict[str, Any]:
    """Referee the key on the textbook passages, then append cited points. Returns {status, refs}."""
    from app.hardening import _flag_disputed_key, _passages
    from app.referee import judge

    if REF_MARK in (mcq.explanation_markdown or ""):
        return {"status": "skipped", "refs": []}
    options = {str(k).upper(): str(v) for k, v in (mcq.options or {}).items()}
    key = (mcq.correct_option or "").upper()
    answer = options.get(key, "")
    seed = {"id": mcq.id, "question_text": mcq.question_text or "", "answer": answer,
            "concept": mcq.tested_concept or mcq.topic or ""}
    blocks = _passages(db, seed)
    if not blocks:
        _tag(db, mcq.id, "none")
        db.commit()
        return {"status": "none", "refs": []}

    verdict = judge(db, mcq.question_text, options=options, key=key,
                    passages=[{"id": b["id"], "title": b["title"], "page": b["page"], "content": b["content"]}
                              for b in blocks])
    v = verdict.get("verdict")
    if v in ("contradicted", "books_conflict") or (v == "supported" and verdict.get("agrees_with_key") is False):
        _tag(db, mcq.id, "disputed")
        _flag_disputed_key(db, seed, verdict, source="Textbook references")
        return {"status": "disputed", "verdict": v, "refs": []}

    context = "\n\n".join(f"Passage {i}: [{b['title']}, Page {b['page']}]\n{b['content']}" for i, b in enumerate(blocks, 1))
    messages = [
        {"role": "system", "content": (
            "You add textbook references to an FCPS Part 1 MCQ explanation. From the passages ONLY, write 2 to 4 "
            "short points (one sentence each) that explain why the correct answer is right or why a tempting option "
            "is wrong. Each point must be stated in the passage it cites. Skip anything the passages do not say. "
            'Reply in JSON: {"points": [{"text": "...", "passage": 1}]}. Return {"points": []} if no passage helps.')},
        {"role": "user", "content": (
            f"QUESTION: {mcq.question_text}\nOPTIONS:\n" + "\n".join(f"{k}. {v}" for k, v in sorted(options.items()))
            + f"\nCORRECT: {key}. {answer}\n\nPASSAGES:\n{context}")},
    ]
    raw = chat_completion(messages, json_mode=True, temperature=0.0, max_tokens=900, label=f"refs {mcq.id}",
                          role="chat")
    try:
        points = (json.loads(raw) or {}).get("points") or []
    except json.JSONDecodeError:
        points = []
    lines, refs = [], []
    for p in points[:4]:
        try:
            n = int(p.get("passage"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= n <= len(blocks):
            continue   # a passage we did not give: drop the point rather than show an unread page
        b = blocks[n - 1]
        text = " ".join(str(p.get("text") or "").split())
        if not text:
            continue
        lines.append(f"- {text} [{b['title']}, Page {b['page']}]")
        refs.append({"book_title": b["title"], "page_number": b["page"], "excerpt": text[:160]})
    if not lines:
        _tag(db, mcq.id, "none")
        db.commit()
        return {"status": "none", "refs": []}
    mcq.explanation_markdown = ((mcq.explanation_markdown or "").rstrip() + f"\n\n{REF_MARK}\n" + "\n".join(lines)).strip()
    mcq.explanation_citations = list(mcq.explanation_citations or []) + refs
    _tag(db, mcq.id, "added")
    db.commit()
    return {"status": "added", "refs": refs}
