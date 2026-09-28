"""Rapid Review: key list and one-page topic summaries.

Moved verbatim from app/main.py (routes keep their paths)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models import User
from app.auth import require_student_or_admin
from app.deps import get_db

router = APIRouter()

# ─── Rapid Review: key list + cached one-page topic summary ─────────────────

class ReviewScopeRequest(BaseModel):
    exam: str | None = None                   # past-paper exam, e.g. "FCPS Part 1"
    years: list[int] | None = None
    tags: dict[str, list[str]] | None = None  # {"subject": [...], "topic": [...], "specialty": [...]}
    main: str | None = None                   # or a bank category
    sub: str | None = None
    order: str = "most_asked"                 # most_asked | topic
    only_missed: bool = False
    offset: int = 0
    limit: int = 100
    regenerate: bool = False                  # summary: admins may rebuild a cached page

@router.post("/api/study/keys")
def study_keys(req: ReviewScopeRequest, db: Session = Depends(get_db),
               current_user: User = Depends(require_student_or_admin)):
    """Every question of a scope as 'stem -> answer', most-asked first (instant, no AI)."""
    from app.rapid_review import keys

    return keys(db, current_user, req.model_dump(), req.order, req.only_missed, req.offset, req.limit)

@router.post("/api/study/topic-summary")
def study_topic_summary(req: ReviewScopeRequest, db: Session = Depends(get_db),
                        current_user: User = Depends(require_student_or_admin)):
    """One cached page of high-yield points for a topic, cited to the textbooks where they support it."""
    from app.models import TopicSummary
    from app.rapid_review import build_summary, serialize_summary, summary_allowed, summary_key
    from app.retention import restricted_allowed

    scope = req.model_dump()
    if not summary_allowed(scope):
        raise HTTPException(status_code=400, detail="Pick a subject or topic first.")
    cached = db.query(TopicSummary).filter_by(scope_key=summary_key(scope)).first()
    if cached is not None and cached.access == "restricted" and not restricted_allowed(current_user):
        raise HTTPException(status_code=403, detail="This summary is not available on this account.")
    if cached is not None and not (req.regenerate and current_user.role == "admin"):
        return serialize_summary(cached, True)
    try:
        return serialize_summary(build_summary(db, current_user, scope), False)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
