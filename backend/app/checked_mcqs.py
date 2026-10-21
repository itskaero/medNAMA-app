"""MCQs a student checked with the Answer-Key Referee that the app did not have yet.

A student pastes a question from somewhere else (a friend's recall, another book, a WhatsApp
group) into Utilities > Check an answer. If it is not already in the bank or the past papers,
it is kept, so the next student to meet it can practise it with its answer settled.

What is kept, and how carefully:

  * Already held?  The stem is embedded with the same model as every stored stem
    (quiz_generation._embed) and compared with the nearest ones. A near-identical stem, or a very
    close stem with mostly the same options, is the same question: nothing is added and the
    student is told where it already is.
  * The key.  Only a key the textbooks settled is served. 'supported' keeps the published key,
    'contradicted' takes the option the passages support. Anything else (silent, conflicting
    books, no key given) is kept as status 'pending' for an admin to settle in Admin > Content,
    and never served with a guessed key.
  * The explanation is the referee's own: its explanation plus the verified textbook quotes.
  * Where it goes.  Its nearest tagged neighbours vote on its Paper 1 subject and topic and its
    Paper 2 specialty (the knn idea of scripts/tag_taxonomy.py, no AI call), so the Custom exam
    builder finds it under the same subjects as the bank (app/practice_scope.py).
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import MCQ, MCQTag, User

logger = logging.getLogger(__name__)

CATEGORY = "Checked MCQs"
SAME_STEM = 0.96            # cosine: the same question, reworded at most trivially
CLOSE_STEM = 0.90           # with at least half the options the same, also the same question
NEIGHBOURS = 15
VOTE_SHARE = 0.5            # a label must carry half the neighbours' weight...
VOTE_MIN_SIM = 0.6          # ...and the nearest one must really be near (tag_taxonomy.knn_place)
MIN_OPTIONS = 3
SETTLED = ("supported", "contradicted")


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _fingerprint(question: str, options: dict[str, str]) -> str:
    body = _norm(question) + "|" + "|".join(sorted(_norm(v) for v in options.values()))
    return "stem:" + hashlib.sha1(body.encode()).hexdigest()


def _option_overlap(a: dict, b: dict) -> float:
    x, y = {_norm(str(v)) for v in (a or {}).values()}, {_norm(str(v)) for v in (b or {}).values()}
    return len(x & y) / max(1, min(len(x), len(y)))


def _where(db: Session, mcq_id: int) -> str:
    m = db.get(MCQ, mcq_id)
    return " · ".join(x for x in (m.main_category, m.sub_category) if x) if m else ""


def _neighbours(db: Session, vec) -> list[dict]:
    rows = db.execute(text(
        "SELECT id, options, main_category, sub_category, "
        "1 - (stem_embedding <=> CAST(:q AS vector)) AS sim "
        "FROM mcqs WHERE stem_embedding IS NOT NULL AND twist_of IS NULL "
        "ORDER BY stem_embedding <=> CAST(:q AS vector) LIMIT :k"),
        {"q": str(vec.tolist()), "k": NEIGHBOURS}).mappings().all()
    return [dict(r) for r in rows]


def _vote(db: Session, near: list[dict]) -> dict[str, str]:
    """Paper 1 subject/topic and Paper 2 specialty from the neighbours' tags."""
    from app.practice_scope import BANK2, SPECIALTIES

    ids = [n["id"] for n in near]
    tags: dict[int, dict[str, str]] = {}
    for mcq_id, axis, label in (db.query(MCQTag.mcq_id, MCQTag.axis, MCQTag.label)
                                .filter(MCQTag.mcq_id.in_(ids),
                                        MCQTag.axis.in_(("fcps_subject", "fcps_topic", "specialty"))).all()):
        tags.setdefault(mcq_id, {}).setdefault(axis, label)
    # Specialty is spelt two ways (practice_scope.SPECIALTIES); vote on the past-paper spelling,
    # which is what the 'specialty' tag holds.
    as_past = {x: spell["past"][0] for spell in SPECIALTIES.values() if spell["past"]
               for src in ("bank", "past") for x in spell[src]}
    out: dict[str, str] = {}
    if not near or near[0]["sim"] < VOTE_MIN_SIM:
        return out
    for axis in ("fcps_subject", "fcps_topic", "specialty"):
        w: Counter = Counter()
        for n in near:
            label = tags.get(n["id"], {}).get(axis)
            if axis == "specialty" and not label and n["main_category"] == BANK2:
                label = n["sub_category"]
            if axis == "specialty" and label:
                label = as_past.get(label)
            if label:
                w[label] += max(float(n["sim"]), 0.0)
        if w:
            label, top = w.most_common(1)[0]
            if top / max(sum(float(n["sim"]) for n in near), 1e-9) >= VOTE_SHARE:
                out[axis] = label
    if "fcps_topic" in out and "fcps_subject" not in out:
        out.pop("fcps_topic")          # a topic is only meaningful inside its subject
    return out


def remember(db: Session, user: User, question: str, options: dict[str, str] | None,
             key: str | None, verdict: dict[str, Any]) -> dict[str, Any]:
    """Keep a checked MCQ the app does not have. Returns what happened, for the result card."""
    from app.quiz_generation import _embed

    options = {str(k).strip().upper(): str(v).strip() for k, v in (options or {}).items() if str(v).strip()}
    question = (question or "").strip()
    if len(options) < MIN_OPTIONS or len(question) < 15:
        return {"status": "skipped", "reason": "Only a full MCQ (a question with its options) is kept."}

    ref = _fingerprint(question, options)
    same = db.query(MCQ.id).filter(MCQ.source_ref == ref).first()
    if same:
        return {"status": "exists", "mcq_id": same[0], "where": _where(db, same[0])}

    vec = _embed([question])[0]
    near = _neighbours(db, vec)
    for n in near[:5]:
        if n["sim"] >= SAME_STEM or (n["sim"] >= CLOSE_STEM and _option_overlap(options, n["options"]) >= 0.5):
            return {"status": "exists", "mcq_id": n["id"], "where": _where(db, n["id"])}

    v = verdict.get("verdict")
    supported = verdict.get("supported_option")
    published = (key or "").strip().upper()[:1] or None
    answer = None
    if v == "supported":
        answer = published or supported
    elif v == "contradicted":
        answer = supported
    settled = v in SETTLED and answer in options
    if not settled and published not in options:
        return {"status": "skipped",
                "reason": "The textbooks did not settle the answer and no key was given, so it was not kept."}

    quotes = [f'> "{e["quote"]}" [{e["book_title"]}, Page {e["page_number"]}]'
              for e in (verdict.get("evidence") or []) if e.get("quote")]
    explanation = "\n\n".join(x for x in ((verdict.get("explanation") or "").strip(), "\n".join(quotes)) if x)

    placed = _vote(db, near)
    mcq = MCQ(
        question_text=question, options=options, correct_option=answer if settled else published,
        main_category=CATEGORY, sub_category=placed.get("fcps_subject"),
        topic=placed.get("fcps_topic"), explanation_markdown=explanation or None,
        status="ready" if settled else "pending", access="open",
        grounding="book" if settled else None, source=f"checked:u{user.id}", source_ref=ref,
        stem_embedding=vec.tolist(),
        explanation_citations=[{"book_title": e["book_title"], "page_number": e["page_number"],
                                "excerpt": str(e.get("quote") or "")[:160]}
                               for e in (verdict.get("evidence") or [])] or None,
    )
    db.add(mcq)
    db.flush()
    for axis, label in placed.items():
        db.add(MCQTag(mcq_id=mcq.id, axis=axis, label=label))
    db.add(MCQTag(mcq_id=mcq.id, axis="referee", label=v or "unchecked"))
    db.commit()
    logger.info("Checked MCQ %s kept from user %s (%s, %s)", mcq.id, user.id, mcq.status, placed)
    return {
        "status": "added" if settled else "pending",
        "mcq_id": mcq.id,
        "where": " · ".join(x for x in (CATEGORY, placed.get("fcps_subject"), placed.get("specialty")) if x),
    }
