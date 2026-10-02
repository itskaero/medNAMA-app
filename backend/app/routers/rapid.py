"""Rapid Review: key list and one-page topic summaries.

Moved verbatim from app/main.py (routes keep their paths)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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


# ─── Revision sheets: a topic read out of the user's own books ──────────────────────

class SheetRequest(BaseModel):
    book_ids: list[int] = []
    chapter: str | None = None                  # one chapter of those books
    topic: str | None = None                    # what to look for inside it
    length: str = "full"                        # "quick" (~10 bullets) | "full" (~22)
    regenerate: bool = False                    # admins may rebuild a cached sheet


@router.get("/api/study/revision-books")
def study_revision_books(db: Session = Depends(get_db),
                         current_user: User = Depends(require_student_or_admin)):
    """The ready books to revise from. Small on purpose: a book's thousands of headings are
    searched for separately (see /revision-chapters), not listed here."""
    from app.revision import available_books

    return available_books(db)


@router.get("/api/study/revision-chapters")
def study_revision_chapters(book_ids: str = Query(..., description="comma-separated book ids"),
                            q: str = Query("", description="substring of a chapter or section name"),
                            db: Session = Depends(get_db),
                            current_user: User = Depends(require_student_or_admin)):
    """Ranked headings of the chosen books matching `q`, for the picker's search box."""
    from app.revision import search_chapters

    try:
        ids = [int(b) for b in book_ids.split(",") if b.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="book_ids must be comma-separated numbers.")
    if not ids:
        return []
    return search_chapters(db, ids, q)


@router.post("/api/study/revision-sheet")
def study_revision_sheet(req: SheetRequest, db: Session = Depends(get_db),
                         current_user: User = Depends(require_student_or_admin)):
    """A one-page revision sheet written only from the chosen books' pages, with their figures
    and the questions the user got wrong in them. Cached: a second visit is instant. Every
    sheet written is also remembered under the user's Study Corner so it can be reopened
    later."""
    from app.revision import build, get_cached, scope_allowed, scope_key, serialize, save_sheet
    from app.retention import restricted_allowed
    from app.models import TopicSummary

    scope = req.model_dump()
    if not scope_allowed(scope):
        raise HTTPException(status_code=400, detail="Pick a book and a chapter, or a book and a topic.")
    cached = get_cached(db, current_user, scope)
    if cached is None and db.query(TopicSummary).filter_by(scope_key=scope_key(scope)).first() is not None:
        raise HTTPException(status_code=403, detail="This sheet is not available on this account.")
    if cached is not None and not (req.regenerate and current_user.role == "admin"):
        save_sheet(db, current_user, scope, cached.label)
        return serialize(cached, True)
    try:
        row = build(db, current_user, scope)
        save_sheet(db, current_user, scope, row.label)
        return serialize(row, False)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/study/revision-sheets/saved")
def study_revision_sheets_saved(db: Session = Depends(get_db),
                                current_user: User = Depends(require_student_or_admin)):
    """This user's saved revision sheets, newest first, for the Study Corner."""
    from app.revision import saved_sheets

    return saved_sheets(db, current_user)


@router.delete("/api/study/revision-sheets/saved/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_revision_sheet(sheet_id: int, db: Session = Depends(get_db),
                          current_user: User = Depends(require_student_or_admin)):
    """Remove one saved sheet from Study Corner (the shared cached sheet stays)."""
    from app.revision import delete_saved_sheet

    if not delete_saved_sheet(db, current_user, sheet_id):
        raise HTTPException(status_code=404, detail="Saved sheet not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
