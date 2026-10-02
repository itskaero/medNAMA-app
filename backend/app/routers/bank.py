"""The MCQ bank and bookmarks.

Moved verbatim from app/main.py (routes keep their paths)."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.models import User, MCQ, MCQBookmark, ConceptBookmark
from app.auth import require_student_or_admin
from app.deps import ConceptBookmarkCreate, get_db

router = APIRouter()

# ======================== MCQ BANK & BOOKMARKS ========================

@router.get("/api/mcqs")
def get_all_mcqs(
    response: Response,
    category: str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """A page of the MCQ bank (category filter, search, bookmark flags), in a stable order.
    The number of matching questions is in the X-Total-Count header."""
    from app.retention import access_scope

    query = access_scope(db.query(MCQ).filter(MCQ.status != "private"), current_user)
    if category and category != "all":
        query = query.filter(MCQ.main_category == category)
    if search:
        query = query.filter(MCQ.question_text.ilike(f"%{search}%"))

    response.headers["X-Total-Count"] = str(query.order_by(None).count())
    mcqs = query.order_by(MCQ.id).offset(max(0, offset)).limit(max(1, min(200, limit))).all()
    
    # Fetch user's bookmarked MCQ IDs
    bookmarks = db.query(MCQBookmark.mcq_id).filter(MCQBookmark.user_id == current_user.id).all()
    bookmarked_ids = {b[0] for b in bookmarks}
    
    return [
        {
            "id": m.id,
            "main_category": m.main_category,
            "sub_category": m.sub_category,
            "question_text": m.question_text,
            "options": m.options,
            "correct_option": m.correct_option,
            "bookmarked": m.id in bookmarked_ids
        }
        for m in mcqs
    ]

@router.post("/api/bookmarks/mcq/{mcq_id}")
def toggle_mcq_bookmark(
    mcq_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Toggles bookmark status of an MCQ for the current student."""
    from app.retention import can_see_mcq

    mcq = db.query(MCQ).filter(MCQ.id == mcq_id).first()
    if not mcq or not can_see_mcq(current_user, mcq):
        raise HTTPException(status_code=404, detail="MCQ not found.")
        
    existing = db.query(MCQBookmark).filter(
        MCQBookmark.user_id == current_user.id,
        MCQBookmark.mcq_id == mcq_id
    ).first()
    
    if existing:
        db.delete(existing)
        db.commit()
        return {"bookmarked": False}
    else:
        bookmark = MCQBookmark(user_id=current_user.id, mcq_id=mcq_id)
        db.add(bookmark)
        db.commit()
        return {"bookmarked": True}

@router.get("/api/bookmarks/mcq")
def get_mcq_bookmarks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns the list of questions bookmarked by the user."""
    bookmarks = db.query(MCQBookmark).filter(MCQBookmark.user_id == current_user.id).all()
    return [
        {
            "id": b.mcq.id,
            "main_category": b.mcq.main_category,
            "sub_category": b.mcq.sub_category,
            "question_text": b.mcq.question_text,
            "options": b.mcq.options,
            "correct_option": b.mcq.correct_option,
            "bookmarked": True
        }
        for b in bookmarks if b.mcq
    ]

@router.post("/api/bookmarks/concept")
def create_concept_bookmark(
    req: ConceptBookmarkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Creates a persistent bookmark for textbook lines, RAG chatbot answers, or question concepts."""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty.")
        
    bookmark = ConceptBookmark(
        user_id=current_user.id,
        content=req.content,
        book_title=req.book_title,
        page_number=req.page_number,
        source_context=req.source_context
    )
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)
    return {
        "id": bookmark.id,
        "content": bookmark.content,
        "book_title": bookmark.book_title,
        "page_number": bookmark.page_number,
        "source_context": bookmark.source_context,
        "created_at": bookmark.created_at.isoformat()
    }

@router.get("/api/bookmarks/concept")
def get_concept_bookmarks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Lists all the saved textbook concepts, citation selections, or RAG answers."""
    bookmarks = db.query(ConceptBookmark).filter(
        ConceptBookmark.user_id == current_user.id
    ).order_by(ConceptBookmark.created_at.desc()).all()
    
    return [
        {
            "id": b.id,
            "content": b.content,
            "book_title": b.book_title,
            "page_number": b.page_number,
            "source_context": b.source_context,
            "created_at": b.created_at.strftime("%b %d, %Y %I:%M %p") if b.created_at else None
        }
        for b in bookmarks
    ]

@router.delete("/api/bookmarks/concept/{bookmark_id}")
def delete_concept_bookmark(
    bookmark_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Deletes a saved concept/text bookmark."""
    bookmark = db.query(ConceptBookmark).filter(
        ConceptBookmark.id == bookmark_id,
        ConceptBookmark.user_id == current_user.id
    ).first()
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found.")
    db.delete(bookmark)
    db.commit()
    return {"message": "Concept bookmark deleted successfully."}
