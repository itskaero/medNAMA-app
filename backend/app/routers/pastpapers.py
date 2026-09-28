"""Past papers, recall versions, twists, timed papers, question images.

Moved verbatim from app/main.py (routes keep their paths)."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models import User, MCQ
from app.auth import require_admin, require_student_or_admin, rate_limiter
from app.deps import MockAnswersRequest, get_db

router = APIRouter()

# ─── past papers (imported exam-year archives; restricted by PAST_PAPERS_ACCESS) ──

class PastPaperScopeRequest(BaseModel):
    exam: str
    years: list[int] | None = None
    tags: dict[str, list[str]] | None = None
    count: int | None = None      # timed paper: number of questions
    minutes: int | None = None    # timed paper: time limit

def _require_past_papers(user: User) -> None:
    from app.retention import restricted_allowed

    if not restricted_allowed(user):
        raise HTTPException(status_code=403, detail="Past papers are not available on this account.")

@router.get("/api/past-papers")
def past_papers_overview(db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """Exams and their years, with question counts and your progress."""
    from app.past_papers import overview
    from app.retention import restricted_allowed

    if not restricted_allowed(current_user):
        return {"locked": True, "exams": []}
    return {"locked": False, **overview(db, current_user)}

@router.post("/api/past-papers/scope")
def past_papers_scope(req: PastPaperScopeRequest, db: Session = Depends(get_db),
                      current_user: User = Depends(require_student_or_admin)):
    """Matching-question count, your progress on them, and subject/topic/specialty counts."""
    from app.past_papers import scope

    _require_past_papers(current_user)
    return scope(db, current_user, req.exam, req.years, req.tags)

def _past_paper_mcq(db: Session, mcq_id: int, user: User) -> MCQ:
    _require_past_papers(user)
    mcq = db.get(MCQ, mcq_id)
    if mcq is None or mcq.status == "private":
        raise HTTPException(status_code=404, detail="Question not found.")
    return mcq

@router.get("/api/mcqs/{mcq_id}/recalls")
def mcq_recalls(mcq_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """The other archives' versions of this past-paper question (same recalled question, own wording and key)."""
    from app.past_papers import recall_versions

    return {"versions": recall_versions(db, _past_paper_mcq(db, mcq_id, current_user))}

@router.post("/api/mcqs/{mcq_id}/check-key", dependencies=[Depends(rate_limiter(limit=10, window=60))])
def mcq_check_key(mcq_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """Answer-Key Referee on a past-paper question whose archives disagree: what do the textbooks say?"""
    from app.referee import judge

    mcq = _past_paper_mcq(db, mcq_id, current_user)
    try:
        return judge(db, mcq.question_text, options=mcq.options, key=mcq.correct_option)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/api/mcqs/{mcq_id}/twists")
def mcq_twists(mcq_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """Twists of a past-paper question: {status: none | running | done | failed, twists?}."""
    from app import twists

    mcq = _past_paper_mcq(db, mcq_id, current_user)
    if not twists.is_seed(mcq):
        raise HTTPException(status_code=400, detail="Twists are written from past-paper questions.")
    return twists.status(db, mcq.id)

@router.post("/api/mcqs/{mcq_id}/twists", dependencies=[Depends(rate_limiter(limit=20, window=60))])
def mcq_twists_start(mcq_id: int, db: Session = Depends(get_db),
                     current_user: User = Depends(require_student_or_admin)):
    """Write twists of a past-paper question (same concept, a different ask), or return the stored ones."""
    from app import twists

    mcq = _past_paper_mcq(db, mcq_id, current_user)
    if not twists.is_seed(mcq):
        raise HTTPException(status_code=400, detail="Twists are written from past-paper questions.")
    current = twists.status(db, mcq.id)
    if current["status"] in ("done", "running"):
        return current
    twists.start(mcq.id)
    return {"status": "running"}

@router.get("/api/twists/seeds")
def twist_seeds(min_years: int = 2, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Past-paper questions (one per recalled question) asked in at least min_years years, for pre-generation."""
    from app import twists

    ids = twists.most_asked_seeds(db, max(1, min_years))
    have = {i for (i,) in db.query(MCQ.twist_of).filter(MCQ.twist_of.in_(ids)).distinct()} if ids else set()
    return {"seeds": ids, "with_twists": sorted(have)}

@router.post("/api/past-papers/timed", status_code=201)
def past_papers_timed(req: PastPaperScopeRequest, db: Session = Depends(get_db),
                      current_user: User = Depends(require_student_or_admin)):
    """A personal timed paper (default 100 questions / 120 min) from the filtered past-paper questions."""
    from app.past_papers import create_timed_paper

    _require_past_papers(current_user)
    mock = create_timed_paper(db, current_user, req.exam, req.years, req.tags,
                              req.count or 100, req.minutes or 120)
    if not mock.mcq_ids:
        raise HTTPException(status_code=400, detail="No questions match these filters.")
    return {"mock_id": mock.id, "title": mock.title, "total": len(mock.mcq_ids), "duration_min": mock.duration_min}

def _owned_mock_or_404(db: Session, user: User, mock_id: int):
    from app.study_modes import owned_mock

    mock = owned_mock(db, user, mock_id)
    if mock is None:
        raise HTTPException(status_code=404, detail="Paper not found.")
    return mock

@router.post("/api/mocks/{mock_id}/start")
def mock_start_by_id(mock_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    from app.study_modes import start_mock

    mock = _owned_mock_or_404(db, current_user, mock_id)
    try:
        return start_mock(db, db.get(User, current_user.id), mock=mock)
    except ValueError:
        raise HTTPException(status_code=409, detail="You have already submitted this paper.")

@router.put("/api/mocks/{mock_id}/progress")
def mock_progress_by_id(mock_id: int, req: MockAnswersRequest, db: Session = Depends(get_db),
                        current_user: User = Depends(require_student_or_admin)):
    from app.study_modes import save_mock_progress

    save_mock_progress(db, db.get(User, current_user.id), req.answers, mock=_owned_mock_or_404(db, current_user, mock_id))
    return {"saved": True}

@router.post("/api/mocks/{mock_id}/submit")
def mock_submit_by_id(mock_id: int, req: MockAnswersRequest, db: Session = Depends(get_db),
                      current_user: User = Depends(require_student_or_admin)):
    from app.study_modes import submit_mock

    try:
        return submit_mock(db, db.get(User, current_user.id), req.answers, mock=_owned_mock_or_404(db, current_user, mock_id))
    except ValueError:
        raise HTTPException(status_code=409, detail="Start this paper first.")

@router.get("/api/mocks/{mock_id}/result")
def mock_result_by_id(mock_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    from app.study_modes import mock_result

    try:
        return mock_result(db, db.get(User, current_user.id), mock=_owned_mock_or_404(db, current_user, mock_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Not submitted yet.")

@router.get("/api/mcq-media/{media_id}")
def mcq_media(media_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_student_or_admin)):
    """An image attached to a question or its explanation (same access rules as the question)."""
    from app.models import MCQMedia
    from app.retention import can_see_mcq

    media = db.get(MCQMedia, media_id)
    mcq = db.get(MCQ, media.mcq_id) if media else None
    if media is None or mcq is None or not can_see_mcq(current_user, mcq):
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(content=media.data, media_type=media.mime, headers={"Cache-Control": "private, max-age=86400"})
