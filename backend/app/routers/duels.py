"""Challenge a friend (duels) and the high-yield map.

Moved verbatim from app/main.py (routes keep their paths)."""

import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from app.database import engine
from app.models import User, MCQ
from app.auth import require_admin, require_student_or_admin, rate_limiter
from app.deps import get_db, logger

router = APIRouter()

# ─── Challenge a friend (duels) ─────────────────────────────────────────────

class DuelCreateRequest(BaseModel):
    subject: str | None = None      # optional FCPS subject (e.g. Physiology)
    count: int = 10

class DuelSubmitRequest(BaseModel):
    answers: dict[str, str]         # mcq_id -> option letter
    time_ms: int | None = None

def _duel_results(db: Session, duel, viewer_id: int) -> dict:
    from app.models import DuelEntry

    entries = db.query(DuelEntry).options(joinedload(DuelEntry.user)).filter(DuelEntry.duel_id == duel.id) \
        .order_by(DuelEntry.score.desc(), DuelEntry.time_ms.asc().nullslast()).all()
    played = any(e.user_id == viewer_id for e in entries)
    mcqs = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_(duel.mcq_ids)).all()}
    questions = []
    for mid in duel.mcq_ids:
        m = mcqs.get(mid)
        if m is None:
            continue
        q = {"id": m.id, "question_text": m.question_text, "options": m.options, "figure_id": m.figure_id}
        if played:   # answers and explanations only after you have played
            q.update({"correct_option": m.correct_option, "explanation_markdown": m.explanation_markdown,
                      "picks": {e.user.username if e.user else str(e.user_id): (e.answers or {}).get(str(m.id))
                                for e in entries}})
        questions.append(q)
    return {
        "code": duel.code,
        "title": duel.title,
        "expires_at": duel.expires_at.isoformat(),
        "played": played,
        "total": len(questions),
        "players": [{"username": e.user.username if e.user else str(e.user_id), "score": e.score,
                     "time_ms": e.time_ms, "is_you": e.user_id == viewer_id} for e in entries],
        "questions": questions,
    }

@router.post("/api/duels", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limiter(limit=10, window=60))])
def create_duel(
    req: DuelCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Create a shareable 10-question challenge (public-bank questions only)."""
    import secrets
    from app.models import Duel
    from app.retention import fcps_only, open_only, subject_filter

    count = max(5, min(20, req.count or 10))
    q = open_only(fcps_only(db.query(MCQ.id).filter(MCQ.status == "ready", MCQ.figure_id.is_(None))))
    cond = subject_filter(db, req.subject) if req.subject and req.subject != "Mixed" else None
    if cond is not None:
        q = q.filter(cond)
    ids = [r[0] for r in q.order_by(func.random()).limit(count).all()]
    if len(ids) < 5:
        raise HTTPException(status_code=400, detail="Not enough questions in the bank for a duel yet.")
    duel = Duel(code=secrets.token_urlsafe(6).replace("-", "x").replace("_", "y"), creator_id=current_user.id,
                title=f"{req.subject or 'Mixed'} duel · {len(ids)} questions", mcq_ids=ids,
                expires_at=datetime.utcnow() + timedelta(days=7))
    db.add(duel)
    db.commit()
    return {"code": duel.code, "title": duel.title, "total": len(ids)}

@router.get("/api/duels/{code}")
def get_duel(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    from app.models import Duel

    duel = db.query(Duel).filter(Duel.code == code).first()
    if duel is None:
        raise HTTPException(status_code=404, detail="Duel not found.")
    if duel.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="This duel has expired.")
    return _duel_results(db, duel, current_user.id)

@router.post("/api/duels/{code}/submit")
def submit_duel(
    code: str,
    req: DuelSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Submit your answers once; returns the side-by-side result."""
    from app.models import Duel, DuelEntry
    from app.retention import record_answer

    duel = db.query(Duel).filter(Duel.code == code).first()
    if duel is None:
        raise HTTPException(status_code=404, detail="Duel not found.")
    if duel.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="This duel has expired.")
    if db.query(DuelEntry.id).filter_by(duel_id=duel.id, user_id=current_user.id).first():
        return _duel_results(db, duel, current_user.id)
    mcqs = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_(duel.mcq_ids)).all()}
    answers = {str(k): str(v).strip().upper()[:1] for k, v in (req.answers or {}).items()
               if str(k).isdigit() and int(k) in mcqs}   # a malformed id is ignored, not a 500
    score = sum(1 for mid, m in mcqs.items() if answers.get(str(mid)) == (m.correct_option or "").upper())
    db.add(DuelEntry(duel_id=duel.id, user_id=current_user.id, answers=answers, score=score,
                     time_ms=req.time_ms if req.time_ms and req.time_ms > 0 else None))
    db.commit()
    for mid, m in mcqs.items():   # duel answers feed the same retention engine
        if str(mid) in answers:
            try:
                record_answer(db, current_user.id, m, answers[str(mid)], "sure", source="duel", session_ref=f"duel:{duel.id}")
            except Exception:
                logger.exception("Retention logging failed for duel MCQ %s", mid)
                db.rollback()
    return _duel_results(db, duel, current_user.id)

@router.get("/api/recalls/frequency")
def recall_frequency(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """High-yield map: how often each chapter/system appears in the recall bank (headlines + variants)."""
    from sqlalchemy import case as _case
    from app.models import RecallItem

    rows = (
        db.query(
            RecallItem.chapter,
            func.count(RecallItem.id),
            func.sum(_case((RecallItem.kind == "headline", 1), else_=0)),
            func.sum(_case((RecallItem.verdict.in_(("contradicted", "books_conflict")), 1), else_=0)),
        )
        .group_by(RecallItem.chapter)
        .order_by(func.count(RecallItem.id).desc())
        .all()
    )
    # Page-level extraction labels the same system several ways; fold the obvious aliases together.
    aliases = {
        "gastrointestinal": "Gastroenterology", "hepatobiliary": "Gastroenterology", "hepatitis": "Gastroenterology",
        "respiratory": "Pulmonology", "calculation chapter": "Calculations", "cell physiology": "Cell Biology",
    }
    merged: dict[str, list[int]] = {}
    for chapter, n, h, d in rows:
        name = (chapter or "Unlabelled").strip()
        name = aliases.get(name.lower(), name)
        acc = merged.setdefault(name, [0, 0, 0])
        acc[0] += int(n)
        acc[1] += int(h or 0)
        acc[2] += int(d or 0)
    total = sum(v[0] for v in merged.values()) or 1
    return sorted(
        ({"chapter": name, "recalls": n, "headlines": h, "disputed": d, "share": round(n / total, 4)}
         for name, (n, h, d) in merged.items()),
        key=lambda x: -x["recalls"],
    )
