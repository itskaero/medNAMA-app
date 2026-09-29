"""Textbook upload, ingestion and the library.

Moved verbatim from app/main.py (routes keep their paths)."""

import os
import tempfile
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Book, Chunk, User
from app.auth import require_admin, require_student_or_admin, rate_limiter
from app.deps import bg_ingest_worker, get_db

router = APIRouter()

STALLED_AFTER_MIN = 60   # 'processing' with no progress this long = the ingest stopped

# ======================== TEXTBOOK MANAGEMENT ========================

@router.get("/api/books")
def list_books(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """List all textbooks stored in the database."""
    books = db.query(Book).order_by(Book.id.desc()).all()
    # A book still "processing" with nothing written for an hour is stalled (its ingest was interrupted): the
    # library shows that instead of a spinner and stops polling it.
    # Compared in SQL, against the database clock the timestamps were written with.
    stalled_ids = {i for (i,) in db.execute(text(
        "SELECT b.id FROM books b WHERE b.status IN ('processing', 'pending') AND greatest(b.updated_at, b.created_at, "
        "coalesce((SELECT max(c.created_at) FROM chunks c WHERE c.book_id = b.id), b.created_at)) "
        "< now() - make_interval(mins => :m)"), {"m": STALLED_AFTER_MIN})} if any(
        b.status in ("processing", "pending") for b in books) else set()

    def stalled(b: Book) -> bool:
        return b.id in stalled_ids

    return [
        {
            "id": b.id,
            "title": b.title,
            "filename": b.filename,
            "status": b.status,
            "stalled": stalled(b),
            "total_pages": b.total_pages,
            "error_message": b.error_message,
            "created_at": b.created_at,
        }
        for b in books
    ]

@router.delete("/api/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Delete a book and all associated chunks and figures from the database (Admin only)."""
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    db.delete(book)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/api/books/{book_id}/chapters")
def list_book_chapters(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns distinct chapter headings for a book (for scoped chat retrieval).

    Filters out junk/watermark slugs (e.g. 'mebooksfree.com', blank, single-char)
    so the chapter dropdown stays clean.
    """
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    rows = (
        db.query(Chunk.chapter)
        .filter(Chunk.book_id == book_id)
        .filter(Chunk.chapter.isnot(None))
        .distinct()
        .all()
    )
    chapters = []
    for (c,) in rows:
        name = (c or "").strip()
        if not name or len(name) < 2:
            continue
        lower = name.lower()
        if "mebooksfree" in lower or "watermark" in lower or "publisher" in lower:
            continue
        if name not in chapters:
            chapters.append(name)
    chapters.sort(key=str.lower)
    return {"book_id": book_id, "book_title": book.title, "chapters": chapters}

@router.post(
    "/api/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limiter(limit=5, window=60))],
)
def upload_and_ingest_book(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Uploads a PDF and spawns background ingestion (Admin only)."""
    # 1. Enforce content MIME type and extension limits (Proposal 10)
    if not file.filename.endswith(".pdf") or file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content_size = 0
    temp_dir = tempfile.gettempdir()
    temp_file_path = os.path.join(temp_dir, f"medrag_upload_{os.urandom(8).hex()}.pdf")

    try:
        with open(temp_file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                content_size += len(chunk)
                if content_size > max_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Upload exceeds maximum size limit of {settings.max_upload_size_mb}MB.",
                    )
                buffer.write(chunk)
    except HTTPException:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise
    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=500, detail=f"File save error: {e}")

    # 2. Open PDF with pypdf to check if encrypted or corrupted (Proposal 10)
    from pypdf import PdfReader
    try:
        reader = PdfReader(temp_file_path)
        if reader.is_encrypted:
            raise HTTPException(status_code=400, detail="Encrypted/password-protected PDFs are not supported.")
        _ = len(reader.pages) # simple extraction check
    except HTTPException:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise
    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=400, detail=f"Failed to read PDF. The file might be corrupted: {e}")

    existing = db.query(Book).filter(Book.filename == file.filename).first()
    if existing:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=400, detail="A book with this filename has already been uploaded.")

    title = Path(file.filename).stem.replace("-", " ").replace("_", " ").title()

    background_tasks.add_task(
        bg_ingest_worker,
        temp_pdf_path=temp_file_path,
        filename=file.filename,
        book_title=title,
    )

    return {"message": f"Book '{title}' uploaded. Ingestion started."}
