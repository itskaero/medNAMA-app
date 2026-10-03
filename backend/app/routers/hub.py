"""Data for the hub screens: Today (continue, Dose status, focus topics) and Review > Mistakes, plus resuming an
unfinished practice session. Reuses the answer log (answer_events) and the mastery map; nothing new is stored."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import require_student_or_admin
from app.deps import get_db
from app.models import MCQ, AttemptAnswer, DailySession, QuizAttempt, User

router = APIRouter()


@router.get("/api/today")
def today(db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    """What to do today: an unfinished session to continue, today's Daily Dose, and the weakest topics."""
    from app.stats import mastery

    cont = None
    since = datetime.utcnow() - timedelta(days=14)
    for a in (db.query(QuizAttempt).filter(QuizAttempt.user_id == user.id, QuizAttempt.completed_at.is_(None),
                                           QuizAttempt.started_at >= since, QuizAttempt.mcq_ids.isnot(None))
              .order_by(QuizAttempt.started_at.desc()).limit(5)):
        answered = db.query(AttemptAnswer).filter(AttemptAnswer.quiz_attempt_id == a.id).count()
        total = len(a.mcq_ids or [])
        if 0 < answered < total:
            cont = {"attempt_id": a.id, "label": a.label or "Practice", "answered": answered, "total": total,
                    "started_at": a.started_at.isoformat() if a.started_at else None, "feedback_mode": a.feedback_mode}
            break
    dose = db.query(DailySession).filter(DailySession.user_id == user.id, DailySession.day == date.today()).first()
    items = list(dose.items or []) if dose else []
    focus = []
    try:
        focus = mastery(db, user).get("focus", [])[:3]
    except Exception:   # the map is a nice-to-have here
        focus = []
    return {
        "continue": cont,
        "dose": {"started": dose is not None, "completed": bool(dose and dose.completed_at),
                 "done": sum(1 for i in items if i.get("done")), "total": len(items)},
        "focus": focus,
    }


MISSES_SQL = """
WITH last AS (
  SELECT DISTINCT ON (e.mcq_id) e.mcq_id, e.is_correct, e.selected_option, e.source, e.created_at, e.subject
  FROM answer_events e WHERE e.user_id = :u AND e.mcq_id IS NOT NULL
  ORDER BY e.mcq_id, e.created_at DESC
)
SELECT l.mcq_id, l.selected_option, l.source, l.created_at, l.subject,
       (SELECT label FROM mcq_tags t WHERE t.mcq_id = l.mcq_id AND t.axis = 'fcps_subject' LIMIT 1) AS fsubject,
       (SELECT label FROM mcq_tags t WHERE t.mcq_id = l.mcq_id AND t.axis = 'fcps_topic' LIMIT 1) AS ftopic
FROM last l WHERE NOT l.is_correct ORDER BY l.created_at DESC LIMIT :n
"""


def missed_ids(db: Session, user_id: int, n: int = 2000) -> list[int]:
    """Questions whose latest answer (from any feature) was wrong: still missed, not yet put right."""
    return [r[0] for r in db.execute(text(MISSES_SQL), {"u": user_id, "n": n})]


@router.get("/api/review/mistakes")
def mistakes(limit: int = 300, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    """Every question still missed (latest answer wrong), newest first, with its subject and topic, plus counts per
    subject and the mistake types (confusion / misconception / gap)."""
    from app.retention import can_see_mcq
    from app.study_modes import mistake_profile

    rows = db.execute(text(MISSES_SQL), {"u": user.id, "n": max(1, min(limit, 1000))}).all()
    mcqs = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_([r[0] for r in rows] or [-1]))}
    items, by_subject = [], {}
    for mid, chosen, source, at, subj, fsubj, ftopic in rows:
        m = mcqs.get(mid)
        if m is None or not can_see_mcq(user, m):
            continue
        subject = fsubj or subj or m.sub_category or "Other"
        by_subject[subject] = by_subject.get(subject, 0) + 1
        items.append({"id": m.id, "question_text": m.question_text, "options": m.options, "correct_option": m.correct_option,
                      "selected": chosen, "source": source, "at": at.isoformat() if at else None,
                      "subject": subject, "topic": ftopic or m.topic, "concept_id": m.concept_id})
    return {"items": items, "total": len(items),
            "subjects": sorted(({"subject": k, "count": v} for k, v in by_subject.items()), key=lambda x: -x["count"]),
            "types": mistake_profile(db, user.id)}


@router.get("/api/quizzes/{attempt_id}/resume")
def resume(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    """An unfinished session as it was served: its questions in order (keys hidden in board mode) and the answers
    already given, so the student continues from the first unanswered question."""
    from app.past_papers import paper_years, question_media, recall_info

    a = db.query(QuizAttempt).filter(QuizAttempt.id == attempt_id, QuizAttempt.user_id == user.id).first()
    if a is None or not a.mcq_ids:
        raise HTTPException(status_code=404, detail="Session not found.")
    if a.completed_at is not None:
        raise HTTPException(status_code=400, detail="This session is already finished.")
    by_id = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_(a.mcq_ids))}
    ordered = [by_id[i] for i in a.mcq_ids if i in by_id]
    media = question_media(db, [m.id for m in ordered])
    years = paper_years(db, [m.id for m in ordered])
    recalls = recall_info(db, ordered)
    board = a.feedback_mode == "board"
    answers = {str(mid): opt for mid, opt in db.query(AttemptAnswer.mcq_id, AttemptAnswer.selected_option)
               .filter(AttemptAnswer.quiz_attempt_id == a.id)}
    return {
        "quiz_attempt_id": a.id, "label": a.label, "timer_mode": a.timer_mode, "timer_value": a.timer_value,
        "feedback_mode": a.feedback_mode, "answers": answers,
        "mcqs": [{"id": m.id, "question_text": m.question_text, "options": m.options,
                  "correct_option": None if board else m.correct_option, "main_category": m.main_category,
                  "sub_category": m.sub_category, "media": media.get(m.id, []), "paper_years": years.get(m.id, []),
                  **recalls.get(m.id, {})} for m in ordered],
    }
