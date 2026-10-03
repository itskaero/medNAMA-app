"""Study Corner: notes and flashcards.

Moved verbatim from app/main.py (routes keep their paths)."""

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app import fsrs
from app.models import User, MCQ, Note, Flashcard
from app.auth import require_student_or_admin
from app.deps import get_db

router = APIRouter()

# ======================== STUDY: NOTES & FLASHCARDS ========================
# Personal study material is strictly per-user (shared MCQ bank stays global).

class NoteCreateRequest(BaseModel):
    title: str = "Untitled Note"
    content: str
    book_title: str | None = None
    page_number: int | None = None
    source_context: str | None = None

class FlashcardCreateRequest(BaseModel):
    front: str
    back: str
    topic: str | None = None
    book_title: str | None = None
    page_number: int | None = None

@router.get("/api/notes")
def list_notes(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Lists the current user's study notes (newest first)."""
    notes = db.query(Note).filter(Note.user_id == current_user.id).order_by(Note.updated_at.desc()).all()
    return [
        {
            "id": n.id,
            "title": n.title,
            "content": n.content,
            "book_title": n.book_title,
            "page_number": n.page_number,
            "source_context": n.source_context,
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "updated_at": n.updated_at.isoformat() if n.updated_at else None,
        }
        for n in notes
    ]

@router.post("/api/notes", status_code=status.HTTP_201_CREATED)
def create_note(
    req: NoteCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Creates a new study note for the current user."""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Note content cannot be empty.")
    note = Note(
        user_id=current_user.id,
        title=req.title.strip() or "Untitled Note",
        content=req.content.strip(),
        book_title=req.book_title,
        page_number=req.page_number,
        source_context=req.source_context,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "book_title": note.book_title,
        "page_number": note.page_number,
        "source_context": note.source_context,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }

@router.put("/api/notes/{note_id}")
def update_note(
    note_id: int,
    req: NoteCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Updates an existing note (title/content/source metadata)."""
    note = db.query(Note).filter(Note.id == note_id, Note.user_id == current_user.id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    note.title = req.title.strip() or note.title
    note.content = req.content.strip()
    note.book_title = req.book_title
    note.page_number = req.page_number
    note.source_context = req.source_context
    db.commit()
    db.refresh(note)
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "book_title": note.book_title,
        "page_number": note.page_number,
        "source_context": note.source_context,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }

@router.delete("/api/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Deletes the current user's note."""
    note = db.query(Note).filter(Note.id == note_id, Note.user_id == current_user.id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    db.delete(note)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/api/flashcards")
def list_flashcards(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Lists the current user's flashcards (not-yet-due ones first for review sessions)."""
    due_first = _due_flashcard_sorter(db, current_user)
    return [
        {
            "id": f.id,
            "front": f.front,
            "back": f.back,
            "topic": f.topic,
            "book_title": f.book_title,
            "page_number": f.page_number,
            "box": f.box,
            "review_count": f.review_count,
            "last_reviewed": f.last_reviewed.isoformat() if f.last_reviewed else None,
            "next_due": f.next_due.isoformat() if f.next_due else None,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in due_first
    ]

@router.get("/api/flashcards/review")
def get_flashcards_for_review(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Returns cards that are due now (plus a few new ones) for a review session."""
    now = datetime.utcnow()
    due = (
        db.query(Flashcard)
        .filter(
            Flashcard.user_id == current_user.id,
            (Flashcard.next_due.is_(None)) | (Flashcard.next_due <= now),
        )
        .order_by(Flashcard.next_due.asc().nulls_first())
        .limit(50)
        .all()
    )
    return [
        {
            "id": f.id,
            "front": f.front,
            "back": f.back,
            "topic": f.topic,
            "box": f.box,
            "review_count": f.review_count,
        }
        for f in due
    ]

@router.post("/api/flashcards", status_code=status.HTTP_201_CREATED)
def create_flashcard(
    req: FlashcardCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Creates a new flip-card for the current user."""
    if not req.front.strip() or not req.back.strip():
        raise HTTPException(status_code=400, detail="Both the front and back of a flashcard are required.")
    card = Flashcard(
        user_id=current_user.id,
        front=req.front.strip(),
        back=req.back.strip(),
        topic=req.topic,
        book_title=req.book_title,
        page_number=req.page_number,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return {
        "id": card.id,
        "front": card.front,
        "back": card.back,
        "topic": card.topic,
        "book_title": card.book_title,
        "page_number": card.page_number,
        "box": card.box,
    }

@router.put("/api/flashcards/{card_id}")
def update_flashcard(
    card_id: int,
    req: FlashcardCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Edits the front/back/metadata of an existing card."""
    card = db.query(Flashcard).filter(
        Flashcard.id == card_id, Flashcard.user_id == current_user.id
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found.")
    card.front = req.front.strip() or card.front
    card.back = req.back.strip() or card.back
    card.topic = req.topic
    card.book_title = req.book_title
    card.page_number = req.page_number
    db.commit()
    db.refresh(card)
    return {"id": card.id, "front": card.front, "back": card.back, "topic": card.topic}

@router.delete("/api/flashcards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_flashcard(
    card_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Deletes the current user's flashcard."""
    card = db.query(Flashcard).filter(
        Flashcard.id == card_id, Flashcard.user_id == current_user.id
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found.")
    db.delete(card)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

class FlashcardReviewRequest(BaseModel):
    """Result of a single card flip: 0=again, 1=hard, 2=good, 3=easy."""

    rating: int

@router.post("/api/flashcards/{card_id}/review")
def review_flashcard(
    card_id: int,
    req: FlashcardReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Schedules the card with FSRS (app/fsrs.py, the scheduler concept re-tests use) after a flip.

    Again brings it back in 10 minutes; Hard/Good/Easy set the next review when recall is predicted to fall
    to 90%. `box` (0..3) is kept as a coarse label for the list: again, under 3 days, under 2 weeks, longer.
    """
    if req.rating not in (0, 1, 2, 3):
        raise HTTPException(status_code=400, detail="rating must be 0 (again), 1 (hard), 2 (good), or 3 (easy).")
    card = db.query(Flashcard).filter(
        Flashcard.id == card_id, Flashcard.user_id == current_user.id
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found.")

    now = datetime.utcnow()
    g = req.rating + 1                                   # 0..3 -> FSRS Again/Hard/Good/Easy (1..4)
    if card.stability is None or card.difficulty is None:
        card.stability, card.difficulty = fsrs.initial(g)
    else:
        elapsed = (now - (card.last_reviewed or now)).total_seconds() / 86400
        card.stability, card.difficulty = fsrs.review(card.stability, card.difficulty, elapsed, g)
    if g == fsrs.AGAIN:
        card.box = 0
        card.next_due = now + timedelta(minutes=10)
    else:
        days = fsrs.interval_days(card.stability)
        card.box = 1 if days < 3 else 2 if days < 14 else 3
        card.next_due = now + timedelta(days=days)

    card.last_reviewed = now
    card.review_count = (card.review_count or 0) + 1
    db.commit()
    db.refresh(card)
    return {"id": card.id, "box": card.box, "next_due": card.next_due.isoformat(), "review_count": card.review_count}

def _due_flashcard_sorter(db: Session, current_user: User) -> list[Flashcard]:
    """Order cards for the list view: due next, then unreviewed, then newest."""
    now = datetime.utcnow()
    cards = db.query(Flashcard).filter(Flashcard.user_id == current_user.id).all()

    def sort_key(c: Flashcard) -> tuple[int, int]:
        # Due-first: 0 = due, 1 = new (unreviewed), 2 = scheduled later; newest id last-breaks.
        if c.next_due is not None and c.next_due <= now:
            return (0, c.next_due.timestamp())
        if c.review_count == 0 or c.next_due is None:
            return (1, c.id)
        return (2, c.next_due.timestamp())

    return sorted(cards, key=sort_key)
