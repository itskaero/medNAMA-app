"""CSV exports.

Moved verbatim from app/main.py (routes keep their paths)."""

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from app.models import User, MCQ, MCQBookmark, ConceptBookmark, Note
from app.auth import require_student_or_admin
from app.deps import get_db

router = APIRouter()

# ======================== EXPORT ========================

@router.get("/api/export/mcqs")
def export_mcqs_csv(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Download the MCQ bank as a CSV: the questions this user may see (restricted past papers only with
    PAST_PAPERS_ACCESS; recall-derived private rows never). Streamed, so 70k rows stay light on the NAS."""
    import csv
    import io

    from app.retention import access_scope

    query = access_scope(db.query(MCQ).filter(MCQ.status != "private"), current_user).order_by(MCQ.id.asc())

    def rows():
        buf = io.StringIO()
        writer = csv.writer(buf)
        buf.write("\ufeff")  # BOM so Excel renders UTF-8 correctly
        writer.writerow([
            "id", "quiz_set_title", "topic", "main_category", "sub_category",
            "difficulty", "question_text", "option_a", "option_b", "option_c",
            "option_d", "option_e", "correct_option", "explanation_markdown",
        ])
        for m in query.yield_per(1000):
            opts = m.options if isinstance(m.options, dict) else {}
            writer.writerow([
                m.id, m.quiz_set_title, m.topic, m.main_category, m.sub_category,
                m.difficulty if m.difficulty is not None else "",
                m.question_text,
                opts.get("A", ""), opts.get("B", ""), opts.get("C", ""), opts.get("D", ""), opts.get("E", ""),
                m.correct_option,
                (m.explanation_markdown or "").replace("\n", " "),
            ])
            if buf.tell() > 256_000:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)
        yield buf.getvalue()

    return StreamingResponse(
        rows(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mednama_mcq_bank.csv"'},
    )

@router.get("/api/export/notes")
def export_notes_csv(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Download the current user's study notes as a CSV."""
    import csv
    import io

    notes = db.query(Note).filter(Note.user_id == current_user.id).order_by(Note.updated_at.desc()).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "title", "content", "book_title", "page_number", "source_context", "updated_at"])
    for n in notes:
        writer.writerow([
            n.id, n.title, (n.content or "").replace("\n", " "),
            n.book_title or "", n.page_number or "",
            n.source_context or "",
            n.updated_at.strftime("%Y-%m-%d %H:%M") if n.updated_at else "",
        ])
    csv_content = "\ufeff" + buf.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mednama_notes.csv"'},
    )

@router.get("/api/export/bookmarks")
def export_bookmarks_csv(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Download the current user's concept bookmarks as a CSV."""
    import csv
    import io

    bookmarks = db.query(ConceptBookmark).filter(
        ConceptBookmark.user_id == current_user.id
    ).order_by(ConceptBookmark.created_at.desc()).all()

    mcq_bookmarks = db.query(MCQBookmark).filter(
        MCQBookmark.user_id == current_user.id
    ).order_by(MCQBookmark.created_at.desc()).all()
    mcq_map = {b.mcq_id: b for b in mcq_bookmarks}
    mcqs = db.query(MCQ).filter(MCQ.id.in_(list(mcq_map.keys()))).all() if mcq_map else []
    mcq_text = {m.id: m.question_text for m in mcqs}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["type", "content", "book_title", "page_number", "created_at"])
    for b in bookmarks:
        writer.writerow(["concept", (b.content or "").replace("\n", " "), b.book_title or "", b.page_number or "", b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else ""])
    for mcq_mark, m in mcq_map.items():
        writer.writerow(["mcq", (mcq_text.get(mcq_mark) or "").replace("\n", " "), "", "", m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else ""])

    csv_content = "\ufeff" + buf.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mednama_bookmarks.csv"'},
    )
