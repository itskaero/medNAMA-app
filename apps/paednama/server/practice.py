"""Practise: single-best-answer MCQs filed under each syllabus topic.

A topic's questions are the ones filed under it (paeds_topic_mcqs). When the learner has answered them all (or there
are none yet), a new set is written on demand by medNAMA's question generator, scoped to the Paediatrics books and
pitched at FCPS-II, and filed under the topic for everyone. Answers go through medNAMA (/api/study/answer), so
explanations, review cards and stats work as everywhere else. The Seed Studio batch bank fills the same table.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from . import registry, settings  # noqa: F401  (backend/ on the path)
from .models import PaedsTopicMcq
from .study import topic_query
from app.exam_levels import FCPS_2  # noqa: E402
from app.models import MCQ, AnswerEvent, User  # noqa: E402

SET_SIZE = 10
# MCQ critique books are for retrieval only: questions are never written from (or copied out of) them.
NOT_A_SOURCE = {"mcq"}


def _find(stage: str, topic_id: str) -> tuple[dict, dict]:
    found = registry.find_topic(stage, topic_id)
    if not found:
        raise KeyError(topic_id)
    return found


def serialize(m: MCQ) -> dict[str, Any]:
    return {"id": m.id, "question_text": m.question_text, "options": m.options, "correct_option": m.correct_option,
            "explanation_markdown": m.explanation_markdown, "sub_category": m.sub_category,
            "figure_id": m.figure_id, "grounding": m.grounding}


def topic_questions(db: Session, user: User, stage: str, topic_id: str) -> dict[str, Any]:
    """The topic's questions this learner can see, unanswered first, and how they've done on the rest."""
    from app.retention import can_see_mcq

    _find(stage, topic_id)
    ids = [r.mcq_id for r in db.query(PaedsTopicMcq).filter_by(stage=stage, topic_id=topic_id)]
    mcqs = [m for m in (db.query(MCQ).filter(MCQ.id.in_(ids)).order_by(MCQ.id).all() if ids else []) if can_see_mcq(user, m)]
    last: dict[int, bool] = {}
    if mcqs:
        for e in (db.query(AnswerEvent).filter(AnswerEvent.user_id == user.id, AnswerEvent.mcq_id.in_([m.id for m in mcqs]))
                  .order_by(AnswerEvent.id)):
            last[e.mcq_id] = e.is_correct
    fresh = [m for m in mcqs if m.id not in last]
    return {"total": len(mcqs), "answered": len(last), "correct": sum(last.values()),
            "questions": [serialize(m) for m in fresh] + [serialize(m) for m in mcqs if m.id in last]}


def start_set(db: Session, stage: str, topic_id: str, book_ids: list[int], count: int = SET_SIZE) -> dict[str, Any]:
    """Write a new set for the topic in the background: returns medNAMA's job id to poll with job()."""
    from app.llm import llm_configured
    from app.quiz_generation import start_quiz_job

    system, topic = _find(stage, topic_id)
    if not book_ids:
        raise LookupError("No Paediatrics books are ready yet.")
    if not llm_configured("fast"):
        raise RuntimeError("The AI model isn't configured on this server.")
    return start_quiz_job({
        "prompt_text": topic_query(system, topic), "book_id": None, "page_num": None, "mcq_count": count,
        "difficulty": None, "exam_profile": "fcps", "level": FCPS_2, "book_ids": book_ids, "request_id": None,
        "chapter": None})


def job(db: Session, stage: str, topic_id: str, job_id: str) -> dict[str, Any]:
    """Poll a set; once written, its questions are filed under the topic (idempotent) and returned."""
    from app.quiz_generation import get_quiz_job

    _find(stage, topic_id)
    j = get_quiz_job(db, job_id)
    if j is None:
        raise LookupError("That set was not found; start a new one.")
    if j["status"] != "done":
        return {"status": j["status"], "detail": j.get("detail"), "elapsed_s": j.get("elapsed_s")}
    mcq_ids = [m["id"] for m in j["result"]["mcqs"]]
    have = {r.mcq_id for r in db.query(PaedsTopicMcq.mcq_id).filter(
        PaedsTopicMcq.stage == stage, PaedsTopicMcq.topic_id == topic_id, PaedsTopicMcq.mcq_id.in_(mcq_ids or [-1]))}
    for i in mcq_ids:
        if i not in have:
            db.add(PaedsTopicMcq(stage=stage, topic_id=topic_id, mcq_id=i, source="practice"))
    db.commit()
    return {"status": "done", "added": len(mcq_ids) - len(have)}
