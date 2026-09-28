"""Study modes: look-alikes, mistake types, final sprint, weekly mock.

Moved verbatim from app/main.py (routes keep their paths)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.models import User, MCQ
from app.auth import require_student_or_admin
from app.deps import MockAnswersRequest, _mcq_payload, get_db

router = APIRouter()

# ─── study modes: look-alikes, mistake types, final sprint, weekly mock ────

@router.get("/api/study/mistakes")
def study_mistakes(db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """Wrong answers by type (confusion / misconception / gap) over the last 60 days."""
    from app.study_modes import mistake_profile

    return mistake_profile(db, current_user.id)

@router.get("/api/study/lookalikes")
def study_lookalikes(db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """The student's confusable pairs with their comparison cards and questions."""
    from app.study_modes import user_pairs

    pairs = user_pairs(db, current_user.id)
    ids = {i for p in pairs for i in p["mcq_ids"]}
    by_id = {m.id: _mcq_payload(m) for m in db.query(MCQ).filter(MCQ.id.in_(ids))} if ids else {}
    for p in pairs:
        p["questions"] = [by_id[i] for i in p["mcq_ids"] if i in by_id]
    return {"pairs": pairs}

def _session_items_payload(db: Session, items: list) -> list:
    from app.models import ConceptCard, ConfusablePair
    from app.retention import serialize_card
    from app.study_modes import serialize_pair

    out = []
    for i, item in enumerate(items or []):
        entry = {**item, "index": i}
        if item.get("mcq_id"):
            m = db.get(MCQ, item["mcq_id"])
            entry["mcq"] = _mcq_payload(m) if m else None
        if item.get("concept_id") and item.get("type") in ("flash", "pearl"):
            card = db.get(ConceptCard, item["concept_id"])
            entry["concept"] = serialize_card(db, card) if card else None
        if item.get("pair_id"):
            pair = db.get(ConfusablePair, item["pair_id"])
            entry["pair"] = serialize_pair(pair) if pair else None
        out.append(entry)
    return out

@router.get("/api/study/sprint")
def study_sprint(db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """Final sprint (last 7 days before the exam): weakest concept cards, rapid re-tests, open look-alikes."""
    from app.study_modes import get_or_build_sprint, sprint_status

    user = db.get(User, current_user.id)
    status = sprint_status(user)
    if not (status["unlocked"] or status["preview"]):
        return {**status, "items": []}
    session = get_or_build_sprint(db, user)
    return {**status, "day": session.day.isoformat(), "completed": session.completed_at is not None,
            "items": _session_items_payload(db, session.items)}

@router.get("/api/mocks/weekly")
def weekly_mock(part: str = "p1", track: str = "", db: Session = Depends(get_db),
                current_user: User = Depends(require_student_or_admin)):
    """This week's paper: part=p1 (FCPS Part 1, all subjects mixed) or p2 (&track=<specialty>, '' = mixed)."""
    from app.study_modes import mock_overview

    return mock_overview(db, db.get(User, current_user.id), part, track)

@router.post("/api/mocks/weekly/start")
def weekly_mock_start(part: str = "p1", track: str = "", db: Session = Depends(get_db),
                      current_user: User = Depends(require_student_or_admin)):
    """Start (or resume) this week's sitting. Questions come without answers."""
    from app.study_modes import start_mock

    try:
        return start_mock(db, db.get(User, current_user.id), part, track)
    except ValueError:
        raise HTTPException(status_code=409, detail="You have already sat this week's mock.")

@router.put("/api/mocks/weekly/progress")
def weekly_mock_progress(req: MockAnswersRequest, part: str = "p1", track: str = "", db: Session = Depends(get_db),
                         current_user: User = Depends(require_student_or_admin)):
    """Autosave answers so a dropped connection or closed tab loses nothing."""
    from app.study_modes import save_mock_progress

    save_mock_progress(db, db.get(User, current_user.id), req.answers, part, track)
    return {"saved": True}

@router.post("/api/mocks/weekly/submit")
def weekly_mock_submit(req: MockAnswersRequest, part: str = "p1", track: str = "", db: Session = Depends(get_db),
                       current_user: User = Depends(require_student_or_admin)):
    from app.study_modes import submit_mock

    try:
        return submit_mock(db, db.get(User, current_user.id), req.answers, part, track)
    except ValueError:
        raise HTTPException(status_code=409, detail="Start this week's mock first.")

@router.get("/api/mocks/weekly/result")
def weekly_mock_result(part: str = "p1", track: str = "", db: Session = Depends(get_db),
                       current_user: User = Depends(require_student_or_admin)):
    from app.study_modes import mock_result

    try:
        return mock_result(db, db.get(User, current_user.id), part=part, track=track)
    except ValueError:
        raise HTTPException(status_code=404, detail="No submitted sitting this week.")
