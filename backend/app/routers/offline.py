"""Offline practice packs: questions (with keys and explanations) to answer without a connection, and the sync
that records those answers once the student is back. One answer per question per pack, so a sync that is
repeated (or interrupted and retried) never counts twice."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_student_or_admin
from app.deps import get_db
from app.models import MCQ, AnswerEvent, User

router = APIRouter()
MAX_PACK = 100
EXPLANATION_CHARS = 2500


class PackRequest(BaseModel):
    num_questions: int = 40
    scope: dict | None = None   # a Practice selection (app/practice_scope.py); none = a mix, weakest subject first


@router.post("/api/offline/pack")
def offline_pack(req: PackRequest, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    from app.past_papers import distinct_by_group
    from app.retention import _weakest_subject, access_scope, fcps_only, subject_filter

    n = max(1, min(req.num_questions, MAX_PACK))
    answered = db.query(AnswerEvent.mcq_id).filter(AnswerEvent.user_id == user.id, AnswerEvent.mcq_id.isnot(None))
    # No pictures: a pack must work with no connection, and question images are fetched on demand.
    base = access_scope(fcps_only(db.query(MCQ).filter(MCQ.status == "ready", MCQ.figure_id.is_(None),
                                                       MCQ.twist_of.is_(None), MCQ.id.notin_(answered))), user)
    if req.scope:
        from app.practice_scope import restrict
        base = restrict(db, base, req.scope)
        picked = base.order_by(func.random()).limit(3 * n).all()
    else:
        picked = []
        cond = subject_filter(db, _weakest_subject(db, user.id))
        if cond is not None:
            picked = base.filter(cond).order_by(func.random()).limit(n // 2).all()
        picked += base.filter(MCQ.id.notin_([m.id for m in picked] or [-1])).order_by(func.random()).limit(3 * n).all()
    mcqs = distinct_by_group(picked, n)
    if not mcqs:
        raise HTTPException(status_code=404, detail="No unanswered questions match this selection.")
    return {
        "pack_id": uuid.uuid4().hex[:16],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "questions": [{
            "id": m.id, "question_text": m.question_text, "options": m.options, "correct_option": m.correct_option,
            "subject": m.sub_category, "topic": m.topic,
            "explanation_markdown": (m.explanation_markdown or "")[:EXPLANATION_CHARS] or None,
        } for m in mcqs],
    }


class OfflineAnswer(BaseModel):
    mcq_id: int
    selected_option: str
    confidence: str | None = None
    answered_at: datetime | None = None


class SyncRequest(BaseModel):
    pack_id: str
    answers: list[OfflineAnswer]


@router.post("/api/offline/sync")
def offline_sync(req: SyncRequest, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    from app.retention import can_see_mcq, record_answer

    pack = "".join(c for c in req.pack_id if c.isalnum())[:32]
    if not pack:
        raise HTTPException(status_code=400, detail="pack_id is required.")
    ref = f"offline:{pack}"
    done = {i for (i,) in db.query(AnswerEvent.mcq_id).filter(AnswerEvent.user_id == user.id,
                                                              AnswerEvent.session_ref == ref)}
    saved = duplicates = skipped = 0
    now = datetime.now(timezone.utc)
    for a in req.answers[:MAX_PACK]:
        if a.mcq_id in done:
            duplicates += 1
            continue
        mcq = db.get(MCQ, a.mcq_id)
        if mcq is None or not can_see_mcq(user, mcq):
            skipped += 1
            continue
        record_answer(db, user.id, mcq, a.selected_option, a.confidence or "sure", source="offline", session_ref=ref)
        done.add(a.mcq_id)
        saved += 1
        when = a.answered_at
        if when is not None:
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            if now - timedelta(days=30) <= when <= now:   # keep the real day (streaks, per-week stats)
                db.query(AnswerEvent).filter(AnswerEvent.user_id == user.id, AnswerEvent.session_ref == ref,
                                             AnswerEvent.mcq_id == a.mcq_id).update(
                    {"created_at": when.astimezone(timezone.utc).replace(tzinfo=None)})
                db.commit()
    return {"saved": saved, "duplicates": duplicates, "skipped": skipped}
