"""Page viewer: the printed page behind a citation.

    GET  /api/pages/book?title=|book_id=&page=   which book and page a citation means, its printed label,
                                                 and whether the PDF is on this server
    GET  /api/pages/{book_id}/{page}.webp        the page image (rendered once, then cached)
    POST /api/pages/highlight                    boxes covering the cited passage on that page

Citations name books by title, and older saved citations use titles from before the metadata clean-up
("Kaztung Pharmacology"), so a title resolves through the current title, earlier titles (books.aliases)
and the file name. The viewer shows a few pages around the cited one; the server caps how many page
images one user can open per hour, so the books can't be paged through wholesale.
"""

import re
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_student_or_admin
from app.book_meta import label_for
from app.book_pages import find_highlight, page_count, pdf_path, render_page
from app.config import settings
from app.deps import get_db
from app.models import Book, User

router = APIRouter()

VIEW_WINDOW = 3                                  # pages either side of the cited one
_views: dict[int, deque] = defaultdict(deque)    # user id -> timestamps of page images served
_views_lock = threading.Lock()


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resolve_book(db: Session, book_id: int | None, title: str | None) -> Book | None:
    if book_id:
        return db.get(Book, book_id)
    want = _norm(title)
    if not want:
        return None
    for b in db.query(Book).all():
        names = [b.title, b.full_title, (b.filename or "").rsplit(".", 1)[0], *(b.aliases or [])]
        if any(_norm(n) == want for n in names if n):
            return b
    return None


def viewer_allowed(user: User | None) -> bool:
    mode = (settings.page_viewer_access or "admin").lower()
    return user is not None and (mode == "all" or user.role == "admin")


def _require_viewer(user: User) -> None:
    if not viewer_allowed(user):
        raise HTTPException(status_code=403, detail="Book pages are only shown to the library's owner.")


def _count_view(user: User) -> None:
    if user.role == "admin":
        return
    now = time.time()
    with _views_lock:
        q = _views[user.id]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= settings.page_views_per_hour:
            raise HTTPException(status_code=429, detail="Page limit for this hour reached. Try again later.")
        q.append(now)


@router.get("/api/pages/book")
def page_meta(page: int, book_id: int | None = None, title: str | None = None,
              db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    _require_viewer(current_user)
    book = resolve_book(db, book_id, title)
    if not book:
        raise HTTPException(status_code=404, detail="That book isn't in the library.")
    path = pdf_path(book.filename)
    total = book.total_pages or (page_count(path) if path else None)
    if total and not 1 <= page <= total:
        raise HTTPException(status_code=404, detail="That page isn't in the book.")
    lo = max(1, page - VIEW_WINDOW)
    hi = min(total or page, page + VIEW_WINDOW)
    return {
        "book_id": book.id,
        "title": book.title,
        "full_title": book.full_title,
        "edition": book.edition,
        "year": book.year,
        "page": page,
        "total_pages": total,
        "available": bool(path),
        "min_page": lo,
        "max_page": hi,
        "labels": {str(p): label_for(book.page_labels, p) for p in range(lo, hi + 1)},
    }


@router.get("/api/pages/{book_id}/{page}.webp")
def page_image(book_id: int, page: int, db: Session = Depends(get_db),
               current_user: User = Depends(require_student_or_admin)):
    _require_viewer(current_user)
    book = db.get(Book, book_id)
    path = pdf_path(book.filename) if book else None
    if not path:
        raise HTTPException(status_code=404, detail="This book's PDF isn't on the server.")
    _count_view(current_user)
    db.commit()   # release the connection: a first render can take a second or two
    try:
        data = render_page(book_id, path, page)
    except IndexError:
        raise HTTPException(status_code=404, detail="That page isn't in the book.")
    return Response(content=data, media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=604800"})


class HighlightRequest(BaseModel):
    book_id: int
    page: int
    text: str


@router.post("/api/pages/highlight")
def page_highlight(req: HighlightRequest, db: Session = Depends(get_db),
                   current_user: User = Depends(require_student_or_admin)):
    _require_viewer(current_user)
    book = db.get(Book, req.book_id)
    path = pdf_path(book.filename) if book else None
    if not path:
        return {"boxes": []}
    db.commit()
    try:
        return {"boxes": find_highlight(path, req.page, req.text[:6000])}
    except Exception:
        return {"boxes": []}
