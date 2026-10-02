"""Admin: textbook page references for past-paper explanations (app/textbook_refs.py), driven by
scripts/prewarm_refs.py over HTTP so the work runs inside the backend that already holds the models."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.deps import get_db
from app.models import MCQ, User

router = APIRouter()


@router.get("/api/admin/refs/todo")
def refs_todo(limit: int = 500, min_years: int = 1, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from app.textbook_refs import candidates

    return {"mcq_ids": candidates(db, limit=max(1, min(limit, 5000)), min_years=max(0, min_years))}


@router.post("/api/admin/refs/{mcq_id}")
def refs_add(mcq_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from app.llm import llm_configured
    from app.textbook_refs import add_textbook_refs

    if not llm_configured("chat"):
        raise HTTPException(status_code=503, detail="The AI service is not configured.")
    mcq = db.get(MCQ, mcq_id)
    if mcq is None:
        raise HTTPException(status_code=404, detail="Question not found.")
    return add_textbook_refs(db, mcq)
