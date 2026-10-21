"""The step-wise custom exam builder, and what each programme can actually offer.

Two jobs that used to be mixed together in one screen:

  * /api/mocks/custom/*     filter-based: pick sources, subjects, topics, years and rules,
                            see how many questions match at every step, then sit the paper
  * /api/mocks/programmes   programme-based: each programme's papers (FCPS-I is two, Paper 1
                            basic sciences and Paper 2 the specialty, 100 questions each), with
                            a truthful question count so an empty programme is never offered
                            as if it worked

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

# The papers each programme sits. A paper is a Practice scope (app/practice_scope.py), so it
# draws on the bank and the past papers together exactly as Custom exam does; "specialty" means
# the student picks theirs first. FCPS Part 1 is two papers of 100, sat separately. A programme
# with no paper here is served from its 'programme' tag (scripts/tag_programmes.py, imports) and
# is reported as unavailable while that tag is empty, so the gap is visible instead of looking
# like a bug.
PROGRAMME_PAPERS: dict[str, list[dict[str, Any]]] = {
    "FCPS-I": [
        {"key": "paper1", "label": "Paper 1 · Basic sciences", "scope": {"paper": "1"},
         "questions": 100, "minutes": 120, "specialty": False},
        {"key": "paper2", "label": "Paper 2 · Specialty", "scope": {"paper": "2"},
         "questions": 100, "minutes": 120, "specialty": True},
    ],
    # PGET: Paper 1 is basic sciences from PGET pool, Paper 2 is specialty from faculty pools.
    "PGET": [
        {"key": "paper1", "label": "Paper 1 · Basic sciences (MBBS)", "scope": {"paper": "pget1", "sources": ["bank"]},
         "questions": 100, "minutes": 120, "specialty": False},
        {"key": "paper2", "label": "Paper 2 · Specialty", "scope": {"paper": "2", "sources": ["bank"]},
         "questions": 100, "minutes": 120, "specialty": True},
    ],
}
TAGGED_PAPER = {"questions": 100, "minutes": 120}
PROGRAMMES_TTL_S = 600
_PROGRAMMES_CACHE: dict[int, tuple[float, dict]] = {}   # per user: access rules differ by account


class CustomMockRequest(BaseModel):
    scope: dict | None = None          # app/practice_scope.py: paper, sources, subjects, topics, specialties, years
    programme: str | None = None       # instead of a scope: that programme's whole pool, or with
    part: str | None = None            # one of its PROGRAMME_PAPERS (+ the specialty for a Paper 2)
    specialty: str | None = None
    questions: int = 100
    minutes: int = 120
    label: str | None = None
    unseen_only: bool = False          # skip questions this user has already been served
    difficulty: str | None = None      # easier | medium | harder (practice_scope.DIFFICULTY_BANDS)


def _base_query(db: Session, user: User):
    """Every question this user may sit, before any filter. The three conditions the scope
    translator deliberately leaves to its callers."""
    from app.retention import access_scope

    return access_scope(
        db.query(MCQ).filter(MCQ.status == "ready", MCQ.twist_of.is_(None)), user)


def _programme_ids(db: Session, programme: str):
    return db.query(MCQTag.mcq_id).filter(MCQTag.axis == "programme", MCQTag.label == programme)


def _part(req: CustomMockRequest) -> dict | None:
    for part in PROGRAMME_PAPERS.get(req.programme or "", []):
        if part["key"] == req.part:
            return part
    return None


def _scope(req: CustomMockRequest) -> dict | None:
    """The Practice scope a request means: its own, or a programme paper's (with the specialty)."""
    part = _part(req)
    if part is not None:
        scope = dict(part["scope"])
        if part["specialty"] and req.specialty:
            scope["specialties"] = [req.specialty]
        return {**scope, "difficulty": req.difficulty} if req.difficulty else scope
    if req.difficulty and req.scope is not None:
        return {**req.scope, "difficulty": req.difficulty}
    return req.scope


def _filtered(db: Session, user: User, req: CustomMockRequest):
    """The question pool for a request: a Practice scope, a programme paper, or a whole programme."""
    from app.practice_scope import restrict

    query = _base_query(db, user)
    scope = _scope(req)
    if req.programme and _part(req) is None:
        query = query.filter(MCQ.id.in_(_programme_ids(db, req.programme)))
    if scope is not None:
        query = restrict(db, query, scope)
    return query


def _group_names(db: Session, scope: dict | None, rows: list) -> dict[int, str]:
    """The group each question is mixed by: its subject on Paper 1, its specialty on Paper 2."""
    from app.practice_scope import SPECIALTIES, normalise

    ids = [r[0] for r in rows]
    sc = normalise(scope)
    if sc["paper"] == "pget1":
        return {r[0]: (r[2] or "General") for r in rows}
    if sc["paper"] != "2":
        return dict(db.query(MCQTag.mcq_id, MCQTag.label)
                    .filter(MCQTag.axis == "fcps_subject", MCQTag.mcq_id.in_(ids)).all())
    spelling = {x: name for name, spell in SPECIALTIES.items() for src in ("bank", "past") for x in spell[src]}
    chosen = set(sc["specialties"])
    out: dict[int, str] = {}
    for mcq_id, label in (db.query(MCQTag.mcq_id, MCQTag.label)
                          .filter(MCQTag.axis == "specialty", MCQTag.mcq_id.in_(ids)).all()):
        name = spelling.get(label)
        if name and (not chosen or name in chosen) and mcq_id not in out:
            out[mcq_id] = name
    for mcq_id, _, sub_category in rows:
        if mcq_id not in out and sub_category in spelling:
            out[mcq_id] = spelling[sub_category]
    return out


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

    # Per subject (Paper 1) or per specialty (Paper 2), as the paper will be mixed.
    from app.practice_scope import SPECIALTIES, normalise, restrict

    scope = _scope(req)
    if total and scope is not None and normalise(scope)["paper"] == "2":
        subject = []
        for name in normalise(scope)["specialties"] or list(SPECIALTIES):
            n = (restrict(db, _base_query(db, user), scope, (name, None)).order_by(None)
                 .with_entities(func.count(func.distinct(group))).scalar() or 0)
            if n:
                subject.append((name, n))
    else:
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

    # Round-robin across subjects (Paper 1) or specialties (Paper 2) so the paper is mixed like
    # a real one, taking one version of each recalled question.
    tags = _group_names(db, _scope(req), rows)
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

    part = _part(req)
    title = req.label or (" · ".join(x for x in (req.programme, part["label"] if part else "paper", req.specialty) if x)
                          if req.programme else "Custom exam")
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
    """Each programme with its papers and how many questions each really has, so the UI can
    disable empty ones. A specialty paper also lists the specialties it can be sat in."""
    import time

    from app import exam_levels
    from app.practice_scope import SPECIALTIES, restrict

    user = db.get(User, current_user.id)
    # Two dozen counts over the whole bank: seconds on the NAS, and they change only on an import.
    cached = _PROGRAMMES_CACHE.get(user.id)
    if cached and time.monotonic() - cached[0] < PROGRAMMES_TTL_S:
        return cached[1]
    group = func.coalesce(MCQ.recall_group, -MCQ.id)

    def count(scope: dict, bucket=None) -> int:
        return int(restrict(db, _base_query(db, user), scope, bucket).order_by(None)
                   .with_entities(func.count(func.distinct(group))).scalar() or 0)

    base = _base_query(db, user).order_by(None).with_entities(MCQ.id).subquery()
    tagged = dict(db.query(MCQTag.label, func.count(MCQTag.mcq_id))
                  .join(base, base.c.id == MCQTag.mcq_id)
                  .filter(MCQTag.axis == "programme").group_by(MCQTag.label).all())
    out = []
    for option in exam_levels.options(include_unset=False):
        code = option["value"]
        papers = []
        for part in PROGRAMME_PAPERS.get(code, []):
            specialties = []
            if part["specialty"]:
                for name in SPECIALTIES:
                    n = count(part["scope"], (name, None))
                    if n:
                        specialties.append({"specialty": name, "available": n, "can_start": n >= MIN_QUESTIONS})
            available = count(part["scope"])
            papers.append({
                "key": part["key"], "label": part["label"], "available": available,
                "questions": part["questions"], "minutes": part["minutes"],
                "needs_specialty": part["specialty"], "specialties": specialties,
                "can_start": available >= MIN_QUESTIONS,
            })
        if not papers and int(tagged.get(code, 0)):
            available = int(tagged[code])
            papers.append({"key": None, "label": f"{option['label']} paper", "available": available,
                           **TAGGED_PAPER, "needs_specialty": False, "specialties": [],
                           "can_start": available >= MIN_QUESTIONS})
        out.append({
            "programme": code,
            "label": option["label"],
            "description": option["description"],
            "available": sum(p["available"] for p in papers),
            "papers": papers,
            "can_start": any(p["can_start"] for p in papers),
            "note": "" if papers else "No questions for this programme yet.",
        })
    result = {"programmes": out}
    _PROGRAMMES_CACHE[user.id] = (time.monotonic(), result)
    return result
