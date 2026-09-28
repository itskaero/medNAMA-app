"""Answer-Key Referee, the private recall bank, explain-it-back.

Moved verbatim from app/main.py (routes keep their paths)."""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import User, MCQ
from app.auth import require_admin, require_student_or_admin, rate_limiter
from app.deps import get_db, logger

router = APIRouter()

# ─── Answer-Key Referee + private recall bank (admin-only until licensed) ─────

class RefereeRequest(BaseModel):
    question: str
    answer: str | None = None                 # recall style: "question = answer"
    options: dict[str, str] | None = None     # MCQ style
    key: str | None = None                    # published key for the MCQ, if any

class RecallReviewRequest(BaseModel):
    review_status: str                        # confirmed | corrected | rejected | unreviewed
    reviewer_note: str | None = None

def _serialize_recall(r) -> dict:
    return {
        "id": r.id, "source": r.source, "page": r.page, "chapter": r.chapter, "kind": r.kind,
        "headline_no": r.headline_no, "question": r.question, "answer": r.answer, "verdict": r.verdict,
        "textbook_answer": r.textbook_answer, "evidence": r.evidence or [], "explanation": r.explanation,
        "review_status": r.review_status, "reviewer_note": r.reviewer_note,
        "refereed_at": r.refereed_at.isoformat() if r.refereed_at else None,
    }

@router.post("/api/referee", dependencies=[Depends(rate_limiter(limit=30, window=60))])
def referee_question(
    req: RefereeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Check a recall answer or MCQ key against the textbooks (verified quotes only)."""
    from app.referee import judge

    if not (req.question or "").strip():
        raise HTTPException(status_code=400, detail="Question is required.")
    if not req.answer and not req.options:
        raise HTTPException(status_code=400, detail="Give the published answer, or the options (and key).")
    try:
        return judge(db, req.question, answer=req.answer, options=req.options, key=req.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/api/recalls")
def list_recalls(
    verdict: str | None = None,
    chapter: str | None = None,
    q: str | None = None,
    review_status: str | None = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Browse the private recall bank with verdicts (admin)."""
    from app.models import RecallItem

    query = db.query(RecallItem)
    if verdict == "unrefereed":
        query = query.filter(RecallItem.verdict.is_(None))
    elif verdict == "disputed":
        query = query.filter(RecallItem.verdict.in_(("contradicted", "books_conflict")))
    elif verdict:
        query = query.filter(RecallItem.verdict == verdict)
    if chapter:
        query = query.filter(RecallItem.chapter.ilike(f"%{chapter}%"))
    if review_status:
        query = query.filter(RecallItem.review_status == review_status)
    if q:
        like = f"%{q}%"
        query = query.filter((RecallItem.question.ilike(like)) | (RecallItem.answer.ilike(like)))
    total = query.count()
    rows = query.order_by(RecallItem.page, RecallItem.id).offset(max(0, offset)).limit(min(max(1, limit), 200)).all()
    counts = dict(db.query(RecallItem.verdict, func.count(RecallItem.id)).group_by(RecallItem.verdict).all())
    return {
        "total": total,
        "items": [_serialize_recall(r) for r in rows],
        "counts": {("unrefereed" if k is None else k): v for k, v in counts.items()},
        "chapters": [c for (c,) in db.query(RecallItem.chapter).distinct().order_by(RecallItem.chapter) if c],
    }

@router.post("/api/recalls/{recall_id}/referee")
def referee_recall(
    recall_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """(Re)run the referee for one recall and store the verdict."""
    from app.models import RecallItem
    from app.referee import judge

    item = db.get(RecallItem, recall_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Recall not found.")
    r = judge(db, item.question, answer=item.answer)
    if not r.get("verdict"):
        raise HTTPException(status_code=502, detail=r.get("error") or "Referee failed.")
    item.verdict = r["verdict"]
    item.textbook_answer = r.get("textbook_answer")
    item.evidence = r.get("evidence")
    item.explanation = (r.get("explanation") or "") + (
        f"\n\nAI reasoning (not from the textbooks): {r['ai_reasoning']}" if r.get("ai_reasoning") else "")
    item.refereed_at = datetime.utcnow()
    db.commit()
    return {**_serialize_recall(item), "figures": r.get("figures", [])}

@router.patch("/api/recalls/{recall_id}")
def review_recall(
    recall_id: int,
    req: RecallReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """A doctor's review of the verdict (confirmed / corrected / rejected)."""
    from app.models import RecallItem

    if req.review_status not in ("unreviewed", "confirmed", "corrected", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid review_status.")
    item = db.get(RecallItem, recall_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Recall not found.")
    item.review_status = req.review_status
    item.reviewer_note = (req.reviewer_note or "").strip()[:2000] or None
    db.commit()
    return _serialize_recall(item)

# ─── Explain it back ────────────────────────────────────────────────────────

class ExplainBackRequest(BaseModel):
    explanation: str

@router.post("/api/concepts/{concept_id}/explain-back", dependencies=[Depends(rate_limiter(limit=20, window=60))])
def explain_back(
    concept_id: int,
    req: ExplainBackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Mark the student's own explanation of a concept against the textbook passage."""
    from app.models import ConceptCard
    from app.retention import grade_explanation

    card = db.get(ConceptCard, concept_id)
    if card is None or (card.visibility != "all" and current_user.role != "admin"):
        raise HTTPException(status_code=404, detail="Concept not found.")
    try:
        return grade_explanation(db, current_user.id, card, req.explanation)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Explain-back grading failed")
        raise HTTPException(status_code=502, detail=f"Could not mark the explanation: {e}")
