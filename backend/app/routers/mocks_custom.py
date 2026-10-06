"""The step-wise custom exam builder, and what each programme can actually offer.

Two jobs that used to be mixed together in one screen:

  * /api/mocks/custom/*     filter-based: pick sources, subjects, topics, years and rules,
                            see how many questions match at every step, then sit the paper
  * /api/mocks/programmes   programme-based: FCPS-I / FCPS-II / PGET full papers, each
                            reporting a truthful question count so an empty programme is
                            never offered as if it worked

Nothing here invents selection logic. Filtering is practice_scope.restrict() (the one
scope -> SQL translator), availability counts come from the same query shape as
practice_scope.practice_tree(), and assembly reuses the recall-group dedupe and per-subject
round-robin already proven in past_papers.create_timed_paper(). The finished paper is stored
as a WeeklyMock with part='custom' and track='u<id>:<token>', which is what
study_modes.owned_mock() recognises as a personal paper - so
/api/mocks/{id}/start|progress|submit|result work on it with no new session machinery.
"""

import logging
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_student_or_admin
from app.deps import get_db
from app.models import MCQ, MCQTag, User, WeeklyMock

router = APIRouter()
logger = logging.getLogger(__name__)

MIN_QUESTIONS = 5
MAX_QUESTIONS = 200
MIN_MINUTES = 5
MAX_MINUTES = 300
OVERFETCH = 6000          # same cap create_timed_paper uses before bucketing

# Programmes that can be offered, and the paper each one sits. Only those with a pool:
# MBBS, MD, MS and USMLE have no questions in this database (see scripts/tag_programmes.py),
# and are reported as unavailable rather than hidden, so the gap is visible instead of
# looking like a bug.
PROGRAMME_PAPERS: dict[str, dict[str, Any]] = {
    "FCPS-I": {"questions": 100, "minutes": 120, "label": "FCPS-I full paper"},
    "FCPS-II": {"questions": 100, "minutes": 120, "label": "FCPS-II full paper"},
    "PGET": {"questions": 100, "minutes": 120, "label": "PGET full paper"},
    "MBBS": {"questions": 100, "minutes": 120, "label": "MBBS paper"},
    "MD": {"questions": 100, "minutes": 120, "label": "MD paper"},
    "MS": {"questions": 100, "minutes": 120, "label": "MS paper"},
    "USMLE": {"questions": 100, "minutes": 120, "label": "USMLE paper"},
}


class CustomMockRequest(BaseModel):
    scope: dict | None = None          # app/practice_scope.py: sources, subjects, topics, years
    programme: str | None = None       # instead of a scope: that programme's whole pool
    questions: int = 100
    minutes: int = 120
    label: str | None = None
    unseen_only: bool = False          # skip questions this user has already been served


def _base_query(db: Session, user: User):
    """Every question this user may sit, before any filter. The three conditions the scope
    translator deliberately leaves to its callers."""
    from app.retention import access_scope

    return access_scope(
        db.query(MCQ).filter(MCQ.status == "ready", MCQ.twist_of.is_(None)), user)


def _programme_ids(db: Session, programme: str):
    return db.query(MCQTag.mcq_id).filter(MCQTag.axis == "programme", MCQTag.label == programme)


def _filtered(db: Session, user: User, req: CustomMockRequest):
    """The question pool for a request: a Practice scope, or a whole programme."""
    from app.practice_scope import restrict

    query = _base_query(db, user)
    if req.programme:
        query = query.filter(MCQ.id.in_(_programme_ids(db, req.programme)))
    if req.scope is not None:
        query = restrict(db, query, req.scope)
    return query


def _seen_ids(db: Session, user: User) -> set[int]:
    from app.models import AnswerEvent

    return {r[0] for r in db.query(AnswerEvent.mcq_id).filter(AnswerEvent.user_id == user.id).all()}


@router.post("/api/mocks/custom/preview")
def custom_preview(req: CustomMockRequest, db: Session = Depends(get_db),
                   current_user: User = Depends(require_student_or_admin)):
    """What a partial selection would give: totals, how it splits by subject, and warnings.

    Called at every wizard step so the student can see "N available" before committing, and
    so an impossible paper is refused with a reason instead of silently coming back short.
    """
    user = db.get(User, current_user.id)
    pool = _filtered(db, user, req)
    group = func.coalesce(MCQ.recall_group, -MCQ.id)   # one version of a recalled question
    total = pool.order_by(None).with_entities(func.count(func.distinct(group))).scalar() or 0

    seen = _seen_ids(db, user)
    unseen = (pool.order_by(None).filter(~MCQ.id.in_(seen) if seen else True)
              .with_entities(func.count(func.distinct(group))).scalar() or 0) if total else 0

    # Per subject, using the canonical taxonomy tag the builder filters on.
    subject = (db.query(MCQTag.label, func.count(func.distinct(group)))
               .select_from(MCQ)
               .join(MCQTag, (MCQTag.mcq_id == MCQ.id) & (MCQTag.axis == "fcps_subject"))
               .filter(MCQ.id.in_(pool.order_by(None).with_entities(MCQ.id)))
               .group_by(MCQTag.label).order_by(MCQTag.label).all()) if total else []

    asked = max(MIN_QUESTIONS, min(MAX_QUESTIONS, int(req.questions or 100)))
    usable = unseen if req.unseen_only else total
    notes: list[str] = []
    if usable == 0:
        notes.append("Nothing matches this selection yet. Widen the subjects or add a source.")
    elif usable < asked:
        notes.append(f"Only {usable} questions match, so the paper will be {usable} long, not {asked}.")
    if total and not subject:
        # Only Paper-1 bank and FCPS Part 1 past papers carry fcps_subject/fcps_topic tags.
        notes.append("These questions are not tagged by subject, so a subject-by-subject "
                     "breakdown is not available for them.")
    return {
        "total": total,
        "unseen": unseen,
        "questions": min(asked, usable) if usable else 0,
        "minutes": max(MIN_MINUTES, min(MAX_MINUTES, int(req.minutes or 120))),
        "by_subject": [{"subject": s, "count": n} for s, n in subject],
        "notes": notes,
        "can_start": usable >= MIN_QUESTIONS,
    }


@router.post("/api/mocks/custom")
def custom_create(req: CustomMockRequest, db: Session = Depends(get_db),
                  current_user: User = Depends(require_student_or_admin)):
    """Build the paper and return its id; sit it through the existing /api/mocks/{id}/* routes."""
    from app.past_papers import distinct_by_group

    user = db.get(User, current_user.id)
    asked = max(MIN_QUESTIONS, min(MAX_QUESTIONS, int(req.questions or 100)))
    minutes = max(MIN_MINUTES, min(MAX_MINUTES, int(req.minutes or 120)))

    pool = _filtered(db, user, req)
    if req.unseen_only:
        seen = _seen_ids(db, user)
        if seen:
            pool = pool.filter(~MCQ.id.in_(seen))

    rows = (pool.with_entities(MCQ.id, MCQ.recall_group, MCQ.sub_category)
            .order_by(func.random()).limit(OVERFETCH).all())
    if len(rows) < MIN_QUESTIONS:
        raise HTTPException(status_code=400,
                            detail="Not enough questions match this selection to build a paper.")

    # Round-robin across subjects so the paper is mixed like a real one, taking one version of
    # each recalled question. Subject comes from the canonical tag where the question has one.
    tags = dict(db.query(MCQTag.mcq_id, MCQTag.label)
                .filter(MCQTag.axis == "fcps_subject",
                        MCQTag.mcq_id.in_([r[0] for r in rows])).all())
    buckets: dict[str, list[int]] = {}
    taken: set[int] = set()
    for mcq_id, recall_group, sub_category in rows:
        key = recall_group if recall_group is not None else -mcq_id
        if key in taken:
            continue
        taken.add(key)
        buckets.setdefault(tags.get(mcq_id) or sub_category or "Other", []).append(mcq_id)

    picked: list[int] = []
    while len(picked) < asked and any(buckets.values()):
        for name in sorted(buckets):
            if buckets[name] and len(picked) < asked:
                picked.append(buckets[name].pop())
    if len(picked) < MIN_QUESTIONS:
        raise HTTPException(status_code=400, detail="Not enough distinct questions for a paper.")

    title = req.label or (f"{req.programme} paper" if req.programme else "Custom exam")
    mock = WeeklyMock(week_start=date.today(), part="custom",
                      track=f"u{user.id}:{uuid.uuid4().hex[:10]}",
                      title=f"{title} · {len(picked)} questions",
                      mcq_ids=picked, duration_min=minutes)
    db.add(mock)
    db.commit()
    db.refresh(mock)
    logger.info("Custom paper %s for user %s: %d questions, %d min, %d subjects",
                mock.id, user.id, len(picked), minutes, len(buckets))
    return {"mock_id": mock.id, "title": mock.title, "questions": len(picked),
            "minutes": minutes, "subjects": sorted(buckets)}


@router.get("/api/mocks/programmes")
def programme_availability(db: Session = Depends(get_db),
                           current_user: User = Depends(require_student_or_admin)):
    """Each programme with how many questions it really has, so the UI can disable empty ones."""
    from app import exam_levels

    user = db.get(User, current_user.id)
    base = _base_query(db, user).order_by(None).with_entities(MCQ.id).subquery()
    counts = dict(db.query(MCQTag.label, func.count(MCQTag.mcq_id))
                  .join(base, base.c.id == MCQTag.mcq_id)
                  .filter(MCQTag.axis == "programme").group_by(MCQTag.label).all())
    out = []
    for option in exam_levels.options(include_unset=False):
        code = option["value"]
        paper = PROGRAMME_PAPERS.get(code) or {}
        available = int(counts.get(code, 0))
        out.append({
            "programme": code,
            "label": option["label"],
            "description": option["description"],
            "available": available,
            "questions": paper.get("questions", 100),
            "minutes": paper.get("minutes", 120),
            "can_start": available >= MIN_QUESTIONS,
            "note": "" if available else "No questions for this programme yet.",
        })
    return {"programmes": out}
