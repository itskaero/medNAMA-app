"""'Report wrong answer'.

Moved verbatim from app/main.py (routes keep their paths)."""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload
from app.models import User, MCQ, AnswerReport
from app.auth import require_admin, require_student_or_admin, rate_limiter
from app.deps import get_db, logger

router = APIRouter()

# ─── Answer reports ("Report wrong answer") ────────────────────────────────

class AnswerReportCreate(BaseModel):
    kind: str  # 'chat' | 'mcq'
    reason: str
    mcq_id: int | None = None
    question: str | None = None
    answer_excerpt: str | None = None

class AnswerReportUpdate(BaseModel):
    status: str  # 'open' | 'resolved' | 'dismissed'

def _serialize_report(r: AnswerReport) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "mcq_id": r.mcq_id,
        "question": r.question,
        "answer_excerpt": r.answer_excerpt,
        "reason": r.reason,
        "status": r.status,
        "username": r.user.username if r.user else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
    }

@router.post(
    "/api/reports",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter(limit=20, window=60))],
)
def create_answer_report(
    req: AnswerReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Flag a chat answer or MCQ as wrong / badly cited / outdated for admin review."""
    if req.kind not in ("chat", "mcq"):
        raise HTTPException(status_code=400, detail="kind must be 'chat' or 'mcq'.")
    reason = (req.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Please say what is wrong.")
    if req.kind == "mcq":
        if not req.mcq_id or not db.query(MCQ.id).filter(MCQ.id == req.mcq_id).first():
            raise HTTPException(status_code=404, detail="MCQ not found.")
    report = AnswerReport(
        user_id=current_user.id,
        kind=req.kind,
        mcq_id=req.mcq_id if req.kind == "mcq" else None,
        question=(req.question or "")[:2000] or None,
        answer_excerpt=(req.answer_excerpt or "")[:4000] or None,
        reason=reason[:2000],
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    logger.info("Answer report %d filed by %s (%s)", report.id, current_user.username, req.kind)
    return _serialize_report(report)

@router.get("/api/reports")
def list_answer_reports(
    status_filter: str = "open",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Admin review queue. status_filter: open | resolved | dismissed | all."""
    q = db.query(AnswerReport).options(joinedload(AnswerReport.user))
    if status_filter != "all":
        q = q.filter(AnswerReport.status == status_filter)
    return [_serialize_report(r) for r in q.order_by(AnswerReport.created_at.desc()).limit(200).all()]

@router.patch("/api/reports/{report_id}")
def update_answer_report(
    report_id: int,
    req: AnswerReportUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Mark a report resolved / dismissed (or reopen it)."""
    if req.status not in ("open", "resolved", "dismissed"):
        raise HTTPException(status_code=400, detail="status must be open, resolved or dismissed.")
    report = db.query(AnswerReport).filter(AnswerReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    report.status = req.status
    report.resolved_at = None if req.status == "open" else datetime.utcnow()
    db.commit()
    db.refresh(report)
    return _serialize_report(report)
