"""The daily loop: Daily Dose, answers with confidence, concept cards, readiness.

Moved verbatim from app/main.py (routes keep their paths)."""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models import User, MCQ
from app.auth import require_student_or_admin
from app.deps import _mcq_payload, get_db

router = APIRouter()

# ─── Daily loop: Daily Dose, answers with confidence, concept cards, readiness ──

class StudyAnswerRequest(BaseModel):
    mcq_id: int
    selected_option: str
    confidence: str = "sure"          # sure | unsure | guess
    dose_index: int | None = None     # position in today's Daily Dose, if answered there
    session_kind: str = "dose"        # dose | sprint: which session dose_index refers to

class ExamDateRequest(BaseModel):
    exam_date: str | None = None      # YYYY-MM-DD, or null to clear

@router.get("/api/study/daily")
def get_daily_dose(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Today's Daily Dose: due concept re-tests, new questions, a spot-the-diagnosis image, a pearl."""
    from app.models import ConceptCard, ConfusablePair
    from app.retention import get_or_build_daily_session, serialize_card, streak
    from app.study_modes import serialize_pair

    # current_user belongs to auth's own DB session; changes (streak freezes) must be
    # made on this request's session or they are never committed.
    user = db.get(User, current_user.id)
    session = get_or_build_daily_session(db, user)
    items = []
    for i, item in enumerate(session.items or []):
        entry = {**item, "index": i}
        if item.get("mcq_id"):
            m = db.get(MCQ, item["mcq_id"])
            entry["mcq"] = _mcq_payload(m) if m else None
        if item.get("type") == "pearl" and item.get("concept_id"):
            card = db.get(ConceptCard, item["concept_id"])
            entry["concept"] = serialize_card(db, card) if card else None
        if item.get("type") == "pair" and item.get("pair_id"):
            pair = db.get(ConfusablePair, item["pair_id"])
            entry["pair"] = serialize_pair(pair) if pair else None
        items.append(entry)
    return {
        "day": session.day.isoformat(),
        "items": items,
        "completed": session.completed_at is not None,
        "streak": streak(db, current_user.id),
        "streak_freezes": user.streak_freezes,
    }

@router.post("/api/study/answer")
def study_answer(
    req: StudyAnswerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Answer one question with a confidence tap; schedules concept re-tests."""
    from app.models import ConceptCard, DailySession
    from app.retention import mark_item_done, record_answer, serialize_card
    from datetime import date as _date

    from app.retention import can_see_mcq

    mcq = db.get(MCQ, req.mcq_id)
    if mcq is None or not can_see_mcq(current_user, mcq):
        raise HTTPException(status_code=404, detail="Question not found.")
    from app.models import StudySession

    sprint = req.session_kind == "sprint"
    source = ("sprint" if sprint else "dose") if req.dose_index is not None else "practice"
    session = None
    if req.dose_index is not None:
        if sprint:
            session = db.query(StudySession).filter_by(user_id=current_user.id, kind="sprint", day=_date.today()).first()
        else:
            session = db.query(DailySession).filter_by(user_id=current_user.id, day=_date.today()).first()
    # Stats groups answers into sittings: today's dose / sprint, or one practice session per day.
    session_ref = f"{source}:{session.id}" if session is not None else f"practice:{_date.today().isoformat()}"
    result = record_answer(db, current_user.id, mcq, req.selected_option, req.confidence, source=source,
                           session_ref=session_ref)
    if req.dose_index is not None:
        if session is not None:
            mark_item_done(db, session, req.dose_index, result["is_correct"])
    card = db.get(ConceptCard, mcq.concept_id) if mcq.concept_id else None
    return {
        **result,
        "explanation_markdown": mcq.explanation_markdown,
        "concept": serialize_card(db, card) if card else None,
    }

@router.post("/api/study/dose/{index}/done")
def mark_dose_item_done(
    index: int,
    kind: str = "dose",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Mark a non-question session item (the pearl, a sprint flash card) as done."""
    from app.models import DailySession, StudySession
    from app.retention import mark_item_done, streak
    from datetime import date as _date

    if kind == "sprint":
        session = db.query(StudySession).filter_by(user_id=current_user.id, kind="sprint", day=_date.today()).first()
    else:
        session = db.query(DailySession).filter_by(user_id=current_user.id, day=_date.today()).first()
    if session is None:
        raise HTTPException(status_code=404, detail="No Daily Dose for today yet.")
    mark_item_done(db, session, index)
    return {"completed": session.completed_at is not None, "streak": streak(db, current_user.id)}

@router.get("/api/concepts/by-mcq/{mcq_id}")
def concept_for_mcq(
    mcq_id: int,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Concept card for a question. 202 while it is still being written in the background."""
    from app.retention import concept_status_for_mcq, ensure_concept_async, serialize_card

    state, card = concept_status_for_mcq(db, mcq_id)
    if state == "missing":
        raise HTTPException(status_code=404, detail="Question not found.")
    if state == "none":
        ensure_concept_async(mcq_id)
        state = "pending"
    if state == "pending":
        response.status_code = status.HTTP_202_ACCEPTED
        return {"status": "pending"}
    return {"status": "ready", "concept": serialize_card(db, card)}

@router.get("/api/concepts/{concept_id}")
def get_concept(
    concept_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    from app.models import ConceptCard
    from app.retention import serialize_card

    card = db.get(ConceptCard, concept_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Concept not found.")
    return serialize_card(db, card)

@router.get("/api/study/readiness")
def get_readiness(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Readiness estimate vs the 75% line, concept mastery, exam countdown, streak."""
    from app.retention import readiness

    return readiness(db, db.get(User, current_user.id))

@router.put("/api/study/exam-date")
def set_exam_date(
    req: ExamDateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    from datetime import date as _date

    user = db.get(User, current_user.id)   # this request's session, so the change is committed
    if req.exam_date:
        try:
            user.exam_date = _date.fromisoformat(req.exam_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="exam_date must be YYYY-MM-DD.")
    else:
        user.exam_date = None
    db.commit()
    return {"exam_date": user.exam_date.isoformat() if user.exam_date else None}


@router.get("/api/study/anki-export")
def anki_export(db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """Your concept cards as an Anki import file (File > Import): front = the concept, back = the summary, the
    textbook quote and page, and the mnemonic; tagged by subject. Plain text, so any Anki version reads it."""
    import html

    from app.models import ConceptCard, ConceptReview

    rows = (db.query(ConceptCard, ConceptReview).join(ConceptReview, ConceptReview.concept_id == ConceptCard.id)
            .filter(ConceptReview.user_id == current_user.id).order_by(ConceptCard.subject, ConceptCard.title).all())

    def cell(text: str) -> str:
        return html.escape(" ".join((text or "").split("\t"))).replace("\r", "").replace("\n", "<br>")

    lines = ["#separator:tab", "#html:true", "#tags column:3", "#deck:medNAMA"]
    for card, _ in rows:
        back = [cell(card.summary)]
        if card.quote:
            back.append(f"<i>“{cell(card.quote)}”</i>")
        if card.book_title:
            back.append(f"<small>{cell(card.book_title)}{f', p. {card.page_number}' if card.page_number else ''}</small>")
        if card.mnemonic:
            back.append(f"<b>Mnemonic:</b> {cell(card.mnemonic)}")
        tag = "medNAMA " + "_".join((card.subject or "General").split())
        lines.append(f"{cell(card.title)}\t{'<br><br>'.join(back)}\t{tag}")
    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="medNAMA-cards.txt"'})
