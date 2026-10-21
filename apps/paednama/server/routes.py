"""PaedNAMA's API (/api/paeds/*): the CPSP curriculum, the learner's profile, and the Paediatrics book library.

Sign-in is medNAMA's own (/api/auth/*, the same accounts and session), so these routes use medNAMA's auth
dependencies as they are.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import registry, settings  # noqa: F401  (backend/ on the path)
from .models import PaedsBookRole, PaedsProfile
from app.auth import require_admin, require_student_or_admin  # noqa: E402
from app.deps import get_db  # noqa: E402
from app.models import Book, User  # noqa: E402

router = APIRouter(prefix="/api/paeds")

# What each book is for (docs/PAEDS_APP_PLAN.md §3). The first matching title fragment wins.
ROLES = {
    "spine": "Read first: the shortest complete account of each topic",
    "reference": "Look up details and the questions you got wrong",
    "neonatal": "Neonatology in practice",
    "protocol": "Local and WHO protocols",
    "reasoning": "Approach to a symptom, differentials and cases",
    "clinical": "History, examination and long and short cases",
    "critical": "Emergency and intensive care",
    "radiology": "Images and radiology",
    "mcq": "MCQ critiques (used for retrieval, never copied as questions)",
}
DEFAULT_ROLES = [
    ("nelson essentials", "spine"), ("nelson", "reference"),
    ("cloherty", "neonatal"), ("neonatal resuscitation", "neonatal"),
    ("who hospital care", "protocol"), ("hospital care for children", "protocol"),
    ("decision making", "reasoning"), ("case files", "reasoning"),
    ("aruchamy", "clinical"), ("clinical pediatrics", "clinical"), ("essential clinical skills", "clinical"),
    ("long cases", "clinical"), ("critical care", "critical"), ("radiology", "radiology"), ("prep", "mcq"),
]
TITLE_HINTS = [k for k, _ in DEFAULT_ROLES] + ["pediatric", "paediatric"]


def default_role(title: str, filename: str) -> str | None:
    hay = f"{title} {filename}".lower()
    return next((role for frag, role in DEFAULT_ROLES if frag in hay), None)


# ─── curriculum ───────────────────────────────────────────────────────────────────────────────────

@router.get("/exams")
def exams(user: User = Depends(require_student_or_admin)):
    return registry.summary()


@router.get("/syllabus")
def syllabus(stage: str = Query("imm"), user: User = Depends(require_student_or_admin)):
    st = registry.stage(stage)
    if not st:
        raise HTTPException(404, "Unknown stage")
    return {"stage": stage, "title": st["title"], "systems": st["syllabus"],
            "mini_cex": st["mini_cex"], "dops": st["dops"]}


@router.get("/topics/search")
def topic_search(q: str = Query(..., min_length=2), stage: str = Query("imm"),
                 user: User = Depends(require_student_or_admin)):
    return {"results": registry.search(stage, q)}


# ─── profile ──────────────────────────────────────────────────────────────────────────────────────

def profile_payload(p: PaedsProfile | None) -> dict[str, Any]:
    if not p:
        return {"stage": None, "exam_date": None, "training_year": None, "rotation": None, "minutes_per_day": 60,
                "needs_setup": True}
    return {"stage": p.stage, "exam_date": p.exam_date.isoformat() if p.exam_date else None,
            "training_year": p.training_year, "rotation": p.rotation, "minutes_per_day": p.minutes_per_day,
            "needs_setup": False}


ROTATIONS = ["General Paediatrics", "Neonatology", "Emergency", "Paediatric ICU", "Outpatients",
             "Sub-specialty rotation", "Not in a rotation"]


@router.get("/profile")
def get_profile(db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    return {**profile_payload(db.get(PaedsProfile, user.id)), "rotations": ROTATIONS,
            "stages": [{"id": s, "title": registry.stage(s)["title"]} for s in registry.STAGES]}


class ProfileReq(BaseModel):
    stage: str
    exam_date: str | None = None
    training_year: int | None = Field(None, ge=1, le=4)
    rotation: str | None = Field(None, max_length=80)
    minutes_per_day: int = Field(60, ge=10, le=600)


@router.put("/profile")
def put_profile(req: ProfileReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    if req.stage not in registry.STAGES:
        raise HTTPException(422, "Choose IMM or FCPS-II")
    when = None
    if req.exam_date:
        try:
            when = date.fromisoformat(req.exam_date)
        except ValueError:
            raise HTTPException(422, "exam_date must be YYYY-MM-DD")
        if when < date.today():
            raise HTTPException(422, "The exam date is in the past")
    p = db.get(PaedsProfile, user.id) or PaedsProfile(user_id=user.id)
    p.stage, p.exam_date, p.training_year = req.stage, when, req.training_year
    p.rotation, p.minutes_per_day = req.rotation, req.minutes_per_day
    db.add(p)
    db.commit()
    return profile_payload(p)


# ─── books ────────────────────────────────────────────────────────────────────────────────────────

def paeds_books(db: Session) -> list[dict[str, Any]]:
    """Library books that are Paediatrics (by subject, by title, or because an admin gave them a role)."""
    roles = {r.book_id: r for r in db.query(PaedsBookRole).all()}
    q = db.query(Book).filter(or_(Book.subject == "Paediatrics", Book.id.in_(list(roles) or [-1]),
                                  *[Book.title.ilike(f"%{h}%") for h in TITLE_HINTS],
                                  *[Book.filename.ilike(f"%{h}%") for h in TITLE_HINTS]))
    out = []
    for b in q.order_by(Book.title).all():
        r = roles.get(b.id)
        role = r.role if r else default_role(b.title or "", b.filename or "")
        out.append({"id": b.id, "title": b.title, "status": b.status, "pages": b.total_pages, "subject": b.subject,
                    "role": role, "role_label": ROLES.get(role or ""), "role_set": bool(r)})
    return out


def book_ids(db: Session, roles: list[str] | None = None) -> list[int]:
    """Ready Paediatrics books, optionally only those with the given roles: the scope for every retrieval."""
    return [b["id"] for b in paeds_books(db) if b["status"] == "ready" and (not roles or b["role"] in roles)]


@router.get("/books")
def books(db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    return {"books": paeds_books(db), "roles": ROLES}


class RoleReq(BaseModel):
    role: str
    note: str | None = None


@router.put("/books/{book_id}/role")
def set_role(book_id: int, req: RoleReq, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if req.role not in ROLES:
        raise HTTPException(422, "Unknown role")
    if not db.get(Book, book_id):
        raise HTTPException(404, "Book not found")
    r = db.get(PaedsBookRole, book_id) or PaedsBookRole(book_id=book_id)
    r.role, r.note = req.role, req.note
    db.add(r)
    db.commit()
    return {"ok": True}


# ─── Phase C: topics, one-pagers and the reading loop ─────────────────────────────────────────────

from . import study  # noqa: E402

ROLE_ORDER = ["spine", "reference", "neonatal", "protocol", "reasoning", "clinical", "critical", "radiology", "mcq"]


def _ready_books(db: Session) -> list[dict[str, Any]]:
    books = [b for b in paeds_books(db) if b["status"] == "ready"]
    return sorted(books, key=lambda b: (ROLE_ORDER.index(b["role"]) if b["role"] in ROLE_ORDER else 99, b["title"]))


def _study_error(e: Exception):
    if isinstance(e, KeyError):
        raise HTTPException(404, "Topic not found")
    raise HTTPException(503, str(e))


@router.get("/topic")
def topic(stage: str = Query("imm"), topic_id: str = Query(...), db: Session = Depends(get_db),
          user: User = Depends(require_student_or_admin)):
    found = registry.find_topic(stage, topic_id)
    if not found:
        raise HTTPException(404, "Topic not found")
    system, t = found
    sheets = {s.lens for s in db.query(study.PaedsSheet).filter_by(stage=stage, topic_id=topic_id).all()}
    r = db.get(study.PaedsReading, (user.id, stage, topic_id))
    return {"stage": stage, "system": system["system"], "system_page": system["page"], "topic": t,
            "lenses": [{"id": "all", "label": "All books", "ready": "all" in sheets}] +
                      [{"id": str(b["id"]), "label": b["title"], "role": b["role"], "ready": str(b["id"]) in sheets}
                       for b in _ready_books(db)],
            "reading": None if r is None else {"done": r.finished_at is not None, "sections_done": r.sections_done,
                                               "recall": [r.recall_correct, r.recall_total], "cards_added": r.cards_added}}


@router.get("/topic/sheet")
def topic_sheet(stage: str = Query("imm"), topic_id: str = Query(...), lens: str = Query("all"),
                db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    books = _ready_books(db)
    ids = [b["id"] for b in books]
    if lens != "all" and (not lens.isdigit() or int(lens) not in ids):
        raise HTTPException(422, "Choose one of the Paediatrics books")
    try:
        return study.get_sheet(db, stage, topic_id, lens, ids, {b["id"]: b["title"] for b in books})
    except (KeyError, study.StudyUnavailable) as e:
        _study_error(e)


def _pack(db: Session, stage: str, topic_id: str):
    books = _ready_books(db)
    spine = [b["id"] for b in books if b["role"] == "spine"]
    try:
        return study.get_pack(db, stage, topic_id, spine, [b["id"] for b in books])
    except (KeyError, study.StudyUnavailable) as e:
        _study_error(e)


@router.get("/read")
def read_pack(stage: str = Query("imm"), topic_id: str = Query(...), db: Session = Depends(get_db),
              user: User = Depends(require_student_or_admin)):
    return study.public_pack(_pack(db, stage, topic_id))


class CaseReq(BaseModel):
    stage: str
    topic_id: str
    answer: str = Field("", max_length=1500)


@router.post("/read/case")
def read_case(req: CaseReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    """Commit an answer to the opening case first; the book's answer is shown only after."""
    pack = _pack(db, req.stage, req.topic_id)
    r = study.reading(db, user.id, req.stage, req.topic_id)
    r.case_answer = req.answer.strip() or None
    db.commit()
    return {"answer": pack.case.get("answer")}


class AnswerReq(BaseModel):
    stage: str
    topic_id: str
    section: int = Field(..., ge=0)
    index: int = Field(..., ge=0)
    answer: str = Field("", max_length=600)
    confidence: str = Field("sure", pattern="^(sure|unsure|guess)$")


@router.post("/read/answer")
def read_answer(req: AnswerReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    pack = _pack(db, req.stage, req.topic_id)
    if req.section >= len(pack.sections) or req.index >= len(pack.sections[req.section].get("questions", [])):
        raise HTTPException(422, "No such question")
    try:
        return study.grade_answer(db, user.id, pack, req.section, req.index, req.answer, req.confidence)
    except study.StudyUnavailable as e:
        raise HTTPException(503, str(e))


class ExplainReq(BaseModel):
    stage: str
    topic_id: str
    section: int = Field(..., ge=0)
    text: str = Field(..., max_length=3000)


@router.post("/read/explain")
def read_explain(req: ExplainReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    pack = _pack(db, req.stage, req.topic_id)
    if req.section >= len(pack.sections):
        raise HTTPException(422, "No such section")
    try:
        return study.grade_explain(db, user.id, pack, req.section, req.text)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except study.StudyUnavailable as e:
        raise HTTPException(503, str(e))


@router.get("/progress")
def reading_progress(stage: str = Query("imm"), db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    return study.progress(db, user.id, stage)


# ─── Dr. Shifa ────────────────────────────────────────────────────────────────────────────────────

from . import shifa  # noqa: E402


class AskReq(BaseModel):
    text: str = Field(..., max_length=4000)
    mode: str = Field("explain", pattern="^(explain|tutor|reasoning)$")
    stage: str = "imm"
    chat_id: int | None = None
    topic_id: str | None = None


@router.post("/shifa/ask")
def shifa_ask(req: AskReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    if req.stage not in registry.STAGES:
        raise HTTPException(422, "Unknown stage")
    try:
        return shifa.ask(db, user.id, req.text, req.mode, req.stage, req.chat_id, req.topic_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except study.StudyUnavailable as e:
        raise HTTPException(503, str(e))


@router.get("/shifa/chats")
def shifa_chats(topic_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    return {"chats": shifa.chats(db, user.id, topic_id)}


@router.get("/shifa/chats/{chat_id}")
def shifa_chat(chat_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    try:
        return shifa.chat_detail(db, user.id, chat_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.delete("/shifa/chats/{chat_id}")
def shifa_delete(chat_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    try:
        shifa.delete_chat(db, user.id, chat_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}
