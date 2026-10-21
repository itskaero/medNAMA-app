"""TOS-based examination preparation.

Endpoints (all under /api/tos):
- /examinations              examination families and their papers (with TOS source, version, weighting)
- /paper                     one paper's blueprint: official counts, bank counts, this learner's answers
- /topic/refresher           evidence-grounded concept refresher (cached per topic)
- /topic/notes               1-3 minute revision sheet: Seed Studio's validated sheet, else generated and cached
- /topic/mcqs                practice questions tagged to the topic only, with how many exist
- /topic/generate-mcqs       more questions through the app's generation pipeline, tagged to the topic
- /mock/audit                blueprint allocation vs bank coverage, official vs internal counts named
- /mock/build                a TOS mock saved as a personal paper (sat through /api/mocks/{id}/*)
- /session/submit            marks a topic session and records every answer like any other practice
- /progress                  this learner's progress over the paper: topics, trend, repeated misses

Topic membership comes from mcq_tags (app/tos_tags.py, scripts/tag_tos.py), never string matching.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import require_admin, require_student_or_admin
from app.database import get_db
from app.llm import llm_configured
from app.models import MCQ, MCQTag, StudySession, User, WeeklyMock
from app import tos_sheets
from app.tos import (
    MASTERY_MIN_ANSWERS,
    _format_mcq,
    _user_label_stats,
    fetch_topic_mcqs,
    find_topic,
    get_paper_tree_with_metrics,
    plan_mock,
    tos_registry,
)
from app.tos_tags import (AXIS_REVIEW, AXIS_SUBTOPIC, AXIS_TOPIC, paper_prefix, record_review, slug,
                          tag_new_questions)

logger = logging.getLogger("app.routers.tos")
router = APIRouter(prefix="/api/tos", tags=["TOS Examination Preparation"])

MIN_MOCK_QUESTIONS = 5


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    """The signed-in user if any (blueprints are browsable signed out; progress needs an account)."""
    import jwt

    from app.auth import JWT_ALGORITHM, JWT_SECRET_KEY

    token = request.cookies.get("access_token")
    header = request.headers.get("Authorization") or ""
    if not token and header.startswith("Bearer "):
        token = header.split(" ", 1)[1]
    if not token:
        return None
    try:
        username = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM]).get("sub")
    except Exception:
        return None
    return db.query(User).filter(User.username == username).first() if username else None


def _paper_topic(exam_id: str, paper_id: str, topic_id: str, subtopic: str | None = None):
    paper = tos_registry.get_paper(exam_id, paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Examination paper not found")
    found = find_topic(paper, topic_id)
    if not found:
        raise HTTPException(status_code=404, detail="Topic not found in this paper's TOS")
    subject, topic = found
    if subtopic and subtopic not in topic.get("subtopics", []):
        raise HTTPException(status_code=404, detail="Subtopic not found under this topic")
    return paper, subject, topic


# ─── Blueprint ────────────────────────────────────────────────────────────────

@router.get("/examinations")
def list_examinations():
    """Examination families with their papers, TOS source/version and whether each paper is weighted."""
    return tos_registry.get_examinations_summary()


@router.get("/paper")
def get_paper_tree(
    exam_id: str = Query(...),
    paper_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    tree = get_paper_tree_with_metrics(db, exam_id, paper_id, user=current_user)
    if not tree:
        raise HTTPException(status_code=404, detail="Requested examination paper not found")
    return tree


# ─── Topic workspace ──────────────────────────────────────────────────────────

@router.get("/topic/refresher")
def get_topic_refresher(
    exam_id: str = Query(...),
    paper_id: str = Query(...),
    topic_id: str = Query(...),
    subtopic: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Concept refresher from the retrieved textbook passages, cached so revisits cost nothing."""
    paper, subject, topic = _paper_topic(exam_id, paper_id, topic_id, subtopic)
    return _sheet("refresher", exam_id, paper, subject, topic, subtopic, db)


def _sheet(kind: str, exam_id: str, paper: dict, subject: dict, topic: dict, subtopic: str | None, db: Session):
    try:
        return tos_sheets.get_sheet(db, kind, exam_id, paper, subject, topic, subtopic)
    except tos_sheets.SheetUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


def _note_viewed(db: Session, user: User, label: str) -> None:
    """Remember which sheets were opened (Progress: revision-note access). One row per user per day."""
    try:
        today = date.today()
        row = db.query(StudySession).filter_by(user_id=user.id, kind="tos_notes", day=today).first()
        if row is None:
            db.add(StudySession(user_id=user.id, kind="tos_notes", day=today, items=[label]))
        elif label not in (row.items or []):
            row.items = [*(row.items or []), label]
        db.commit()
    except Exception as e:
        db.rollback()
        logger.info("Note view not recorded: %s", e)


@router.get("/topic/notes")
def get_topic_notes(
    exam_id: str = Query(...),
    paper_id: str = Query(...),
    topic_id: str = Query(...),
    subtopic: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Pre-generated sheets first (Seed Studio, validated), else one generated from the textbooks and cached."""
    paper, subject, topic = _paper_topic(exam_id, paper_id, topic_id, subtopic)
    _note_viewed(db, current_user, f"{exam_id}:{topic_id}" + (f"#{slug(subtopic)}" if subtopic else ""))
    return _sheet("notes", exam_id, paper, subject, topic, subtopic, db)


@router.get("/topic/mcqs")
def get_topic_mcqs(
    exam_id: str = Query(...),
    paper_id: str = Query(...),
    topic_id: str = Query(...),
    subtopic: str | None = Query(None),
    difficulty: str | None = Query(None, description="1-5, 'mixed' or 'adaptive'"),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Questions tagged to this topic only. `available` is how many exist for the filter; when it is below
    `requested` the screen says so instead of borrowing questions from other topics."""
    _paper_topic(exam_id, paper_id, topic_id, subtopic)
    if difficulty not in (None, "", "mixed", "adaptive") and not (difficulty.isdigit() and 1 <= int(difficulty) <= 5):
        raise HTTPException(status_code=422, detail="difficulty must be 1-5, 'mixed' or 'adaptive'")
    diff = None if difficulty in (None, "", "mixed") else difficulty
    return fetch_topic_mcqs(db, current_user, exam_id, paper_id, topic_id, subtopic, diff, limit)


class GenerateMCQsRequest(BaseModel):
    exam_id: str
    paper_id: str
    topic_id: str
    subtopic: str | None = None
    count: int = Field(5, ge=1, le=10)
    difficulty: int | None = Field(None, ge=1, le=5)


@router.post("/topic/generate-mcqs")
def generate_topic_mcqs(
    req: GenerateMCQsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """More questions for a thin topic, through the app's generation pipeline (textbook retrieval, duplicate
    rejection, answer-support check). A question whose answer the cited passage does not support is kept
    but labelled AI knowledge, never presented as textbook-backed or as a past-paper question."""
    from app.quiz_generation import QuizGenerationError, generate_quiz_set

    paper, subject, topic = _paper_topic(req.exam_id, req.paper_id, req.topic_id, req.subtopic)
    if not llm_configured():
        raise HTTPException(status_code=503, detail="The AI model is not configured on this server.")
    subs = [req.subtopic] if req.subtopic else topic.get("subtopics", [])[:8]
    prompt = f"{subject['subject_name']}: {topic['topic_name']}" + (f" ({', '.join(subs)})" if subs else "")
    level = "PGET" if req.exam_id == "pget" else None
    try:
        result = generate_quiz_set(db, prompt_text=prompt, book_id=None, page_num=None, mcq_count=req.count,
                                   difficulty=req.difficulty, exam_profile="fcps", request_id=uuid.uuid4().hex,
                                   level=level)
    except QuizGenerationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    ids = [m["id"] for m in result.get("mcqs", [])]
    if ids:
        tag_new_questions(db, req.exam_id, req.topic_id, ids, req.subtopic)
    mcqs = db.query(MCQ).filter(MCQ.id.in_(ids)).all() if ids else []
    return {
        "generated_count": len(mcqs),
        "duplicates_skipped": result.get("duplicates_skipped", 0),
        "grounding_counts": result.get("grounding_counts", {}),
        "questions": [_format_mcq(m) for m in mcqs],
    }


# ─── Mocks ────────────────────────────────────────────────────────────────────

@router.get("/mock/audit")
def audit_mock(
    exam_id: str = Query(...),
    paper_id: str = Query(...),
    mini_subject: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Allocation per subject (official or internal, named), what the bank holds, and any deviation."""
    plan = plan_mock(db, current_user, exam_id, paper_id, mini_subject)
    if plan.get("error"):
        raise HTTPException(status_code=404, detail=plan["error"])
    return plan


@router.post("/mock/build")
def build_mock(
    exam_id: str = Query(...),
    paper_id: str = Query(...),
    mini_subject: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Assemble the paper to the blueprint and save it as a personal paper, so it is sat, autosaved,
    marked and reviewed by the same /api/mocks/{id}/* flow as every other timed paper."""
    user = db.get(User, current_user.id)
    plan = plan_mock(db, user, exam_id, paper_id, mini_subject, pick=True)
    if plan.get("error"):
        raise HTTPException(status_code=404, detail=plan["error"])
    ids = plan["mcq_ids"] or []
    if len(ids) < MIN_MOCK_QUESTIONS:
        raise HTTPException(status_code=400, detail=(
            "This paper's topics are not mapped to questions yet." if not plan["tagged"]
            else "Not enough questions in the bank for this blueprint yet."))
    paper = tos_registry.get_paper(exam_id, paper_id)
    exam = tos_registry.examinations[exam_id]
    what = f"{plan['mini_subject']} mini-mock" if plan["mini_subject"] else "full mock"
    mock = WeeklyMock(week_start=date.today(), part="custom", track=f"u{user.id}:{uuid.uuid4().hex[:10]}",
                      title=f"{exam['short_name']} · {paper['title']} · TOS {what} · {len(ids)} questions",
                      mcq_ids=ids, duration_min=plan["minutes"])
    db.add(mock)
    db.commit()
    db.refresh(mock)
    plan.pop("mcq_ids", None)
    return {"mock_id": mock.id, "title": mock.title, "questions": len(ids), "minutes": plan["minutes"], "plan": plan}


# ─── Sessions and progress ────────────────────────────────────────────────────

class SubmitAnswerItem(BaseModel):
    mcq_id: int
    selected_option: str = ""
    confidence: str = "sure"


class SubmitSessionRequest(BaseModel):
    exam_id: str
    paper_id: str
    topic_id: str
    subtopic: str | None = None
    mode: str = "study"  # study | exam
    answers: list[SubmitAnswerItem]


@router.post("/session/submit")
def submit_session(
    req: SubmitSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Mark a topic session. Each answered question is recorded like any other practice answer (Progress,
    Mistakes, re-test schedule); an unanswered one counts as wrong in the score but is not recorded."""
    from app.retention import record_answer

    paper, subject, topic = _paper_topic(req.exam_id, req.paper_id, req.topic_id, req.subtopic)
    if not req.answers:
        raise HTTPException(status_code=400, detail="No answers submitted")
    mcqs = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_([a.mcq_id for a in req.answers])).all()}
    ref = f"tos:{req.exam_id}:{req.topic_id}:{uuid.uuid4().hex[:8]}"
    detail, diff_stats = [], {}
    for a in req.answers:
        m = mcqs.get(a.mcq_id)
        if m is None:
            continue
        sel = (a.selected_option or "").strip().upper()
        ok = bool(sel) and sel == (m.correct_option or "").strip().upper()
        if sel:
            record_answer(db, current_user.id, m, sel, a.confidence or "sure", source="tos", session_ref=ref)
        d = m.difficulty or 3
        s = diff_stats.setdefault(d, {"total": 0, "correct": 0})
        s["total"] += 1
        s["correct"] += ok
        detail.append({"mcq_id": m.id, "question_text": m.question_text, "selected_option": sel or None,
                       "correct_option": m.correct_option, "is_correct": ok, "difficulty": d,
                       "explanation_markdown": m.explanation_markdown or "", "source": _format_mcq(m)["source"]})

    total = len(detail)
    correct = sum(r["is_correct"] for r in detail)
    missed_ids = [r["mcq_id"] for r in detail if not r["is_correct"]]
    # Weak subtopics: the subtopics of the questions missed (from their tags), with how many were missed.
    weak: dict[str, int] = {}
    if missed_ids:
        prefix = f"{req.exam_id}:{req.topic_id}#"
        for (label,) in db.query(MCQTag.label).filter(MCQTag.mcq_id.in_(missed_ids), MCQTag.axis == AXIS_SUBTOPIC,
                                                       MCQTag.label.like(prefix + "%")).all():
            weak[label] = weak.get(label, 0) + 1
    by_slug = {slug(st): st for st in topic.get("subtopics", [])}
    weak_list = sorted(({"subtopic": by_slug.get(lbl.split("#", 1)[1], lbl), "missed": n} for lbl, n in weak.items()),
                       key=lambda x: -x["missed"])
    topic_stats = _user_label_stats(db, current_user.id, AXIS_TOPIC, f"{req.exam_id}:{req.topic_id}").get(
        f"{req.exam_id}:{req.topic_id}")
    return {
        "total_attempted": total,
        "answered": sum(1 for r in detail if r["selected_option"]),
        "correct_count": correct,
        "accuracy": round(correct / total * 100, 1) if total else 0.0,
        "mode": req.mode,
        "difficulty_breakdown": [{"difficulty": d, "total": s["total"], "correct": s["correct"],
                                  "accuracy": round(s["correct"] / s["total"] * 100, 1)}
                                 for d, s in sorted(diff_stats.items())],
        "weak_subtopics": weak_list,
        "missed_questions": [r for r in detail if not r["is_correct"]],
        # Over all recorded answers on the topic, not just this session; "mastery" only past the threshold.
        "topic_answers": topic_stats["answers"] if topic_stats else 0,
        "topic_accuracy": round(topic_stats["correct"] / topic_stats["answers"] * 100, 1) if topic_stats else None,
        "mastery_threshold": MASTERY_MIN_ANSWERS,
    }


@router.get("/progress")
def tos_progress(
    exam_id: str = Query(...),
    paper_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """The learner's preparation on this paper (separate from what the TOS asks for): topics practised,
    weekly accuracy, repeatedly missed questions, sheets opened, and where to go next."""
    paper = tos_registry.get_paper(exam_id, paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Examination paper not found")
    prefix = paper_prefix(exam_id, paper_id)
    like = prefix.replace("_", r"\_") + "%"
    uid = current_user.id
    topic_stats = _user_label_stats(db, uid, AXIS_TOPIC, prefix)
    names = {f"{exam_id}:{t['id']}": (s["subject_name"], t["topic_name"], s.get("official_mcq_count"))
             for s in paper["subjects"] for t in s["topics"]}

    since = datetime.utcnow() - timedelta(weeks=8)
    trend = [{"week": r[0].date().isoformat(), "answers": int(r[1]), "accuracy": round(float(r[2]) * 100, 1)}
             for r in db.execute(text(
                 "SELECT date_trunc('week', e.created_at) AS wk, count(*), avg(CASE WHEN e.is_correct THEN 1 ELSE 0 END) "
                 "FROM answer_events e WHERE e.user_id = :u AND e.created_at >= :since AND e.mcq_id IN "
                 "(SELECT mcq_id FROM mcq_tags WHERE axis = 'tos_topic' AND label LIKE :p ESCAPE '\\') "
                 "GROUP BY 1 ORDER BY 1"), {"u": uid, "since": since, "p": like}).fetchall()]

    repeated = db.execute(text(
        "SELECT e.mcq_id, count(*) AS misses, min(t.label), left(min(m.question_text), 160) "
        "FROM answer_events e JOIN mcqs m ON m.id = e.mcq_id "
        "JOIN mcq_tags t ON t.mcq_id = e.mcq_id AND t.axis = 'tos_topic' AND t.label LIKE :p ESCAPE '\\' "
        "WHERE e.user_id = :u AND NOT e.is_correct GROUP BY e.mcq_id HAVING count(*) >= 2 "
        "ORDER BY misses DESC LIMIT 15"), {"u": uid, "p": like}).fetchall()

    notes_opened = set()
    for (items,) in db.query(StudySession.items).filter(StudySession.user_id == uid, StudySession.kind == "tos_notes").all():
        notes_opened.update(i for i in (items or []) if i.startswith(prefix))

    topics = []
    for label, (subj, tname, _) in names.items():
        st = topic_stats.get(label)
        topics.append({"topic_id": label.split(":", 1)[1], "subject": subj, "topic": tname,
                       "answers": st["answers"] if st else 0, "questions": st["questions"] if st else 0,
                       "accuracy": round(st["correct"] / st["answers"] * 100, 1) if st else None,
                       "estimate": "mastery" if st and st["answers"] >= MASTERY_MIN_ANSWERS else "observed",
                       "notes_opened": label in notes_opened or any(n.startswith(label + "#") for n in notes_opened)})
    practised = [t for t in topics if t["answers"]]
    weak = sorted((t for t in practised if t["answers"] >= 5 and (t["accuracy"] or 0) < 60), key=lambda t: t["accuracy"])
    weights = {s["subject_name"]: s.get("official_mcq_count") or 0 for s in paper["subjects"]}
    untouched = sorted((t for t in topics if not t["answers"]), key=lambda t: -weights.get(t["subject"], 0))
    suggestions = ([{**t, "why": f"{t['accuracy']}% over {t['answers']} answers"} for t in weak[:5]]
                   + [{**t, "why": "not practised yet" + (f"; {weights[t['subject']]} questions on the paper"
                                                          if weights.get(t["subject"]) else "")} for t in untouched[:5]])
    return {
        "paper": paper["title"],
        "topics_total": len(topics),
        "topics_practised": len(practised),
        "answers": sum(t["answers"] for t in practised),
        "accuracy": (round(sum((t["accuracy"] or 0) * t["answers"] for t in practised) /
                           max(1, sum(t["answers"] for t in practised)), 1) if practised else None),
        "mastery_threshold": MASTERY_MIN_ANSWERS,
        "trend": trend,
        "difficulty": [{"difficulty": int(r[0]), "answers": int(r[1]), "accuracy": round(float(r[2]) * 100, 1)}
                       for r in db.execute(text(
                           "SELECT coalesce(m.difficulty, 3), count(*), avg(CASE WHEN e.is_correct THEN 1 ELSE 0 END) "
                           "FROM answer_events e JOIN mcqs m ON m.id = e.mcq_id WHERE e.user_id = :u AND e.mcq_id IN "
                           "(SELECT mcq_id FROM mcq_tags WHERE axis = 'tos_topic' AND label LIKE :p ESCAPE '\\') "
                           "GROUP BY 1 ORDER BY 1"), {"u": uid, "p": like}).fetchall()],
        "repeatedly_missed": [{"mcq_id": r[0], "misses": int(r[1]),
                               "topic": names.get(r[2], ("", r[2], None))[1], "stem": r[3]} for r in repeated],
        "notes_opened": len(notes_opened),
        "suggestions": suggestions,
        "topics": topics,
    }


# ─── Admin: close calls held back by the tagger ───────────────────────────────

@router.get("/review")
def review_queue(
    exam_id: str = Query(...),
    paper_id: str = Query(...),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Questions whose best topic did not clearly beat the runner-up (app/tos_tags.py MARGIN), so they are kept
    out of practice until an admin accepts the suggestion, picks another topic, or rules them off-syllabus."""
    paper = tos_registry.get_paper(exam_id, paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Examination paper not found")
    like = paper_prefix(exam_id, paper_id).replace("_", r"\_") + "%"
    base = (db.query(MCQTag.mcq_id, MCQTag.label, MCQ.question_text, MCQ.options, MCQ.correct_option, MCQ.main_category)
            .join(MCQ, MCQ.id == MCQTag.mcq_id)
            .filter(MCQTag.axis == AXIS_REVIEW, MCQTag.label.like(like, escape="\\"), MCQ.status == "ready"))
    total = base.count()
    names = {f"{exam_id}:{t['id']}": (s["subject_name"], t["topic_name"]) for s in paper["subjects"] for t in s["topics"]}
    rows = base.order_by(MCQTag.label, MCQTag.mcq_id).offset(offset).limit(limit).all()
    return {
        "total": total,
        "offset": offset,
        "items": [{"mcq_id": mid, "suggested_topic_id": label.split(":", 1)[1],
                   "suggested": " › ".join(names.get(label, ("?", label))),
                   "question_text": stem, "options": opts, "correct_option": key, "source": cat}
                  for mid, label, stem, opts, key, cat in rows],
    }


class ReviewDecision(BaseModel):
    exam_id: str
    paper_id: str
    action: str                      # accept | reassign | reject
    topic_id: str | None = None      # reassign: the topic to file it under
    subtopic: str | None = None


@router.post("/review/{mcq_id}")
def review_decide(
    mcq_id: int,
    req: ReviewDecision,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Settle one close call. The decision is stored as a 'tos_manual' tag, which a re-run of the tagger keeps."""
    paper = tos_registry.get_paper(req.exam_id, req.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Examination paper not found")
    like = paper_prefix(req.exam_id, req.paper_id).replace("_", r"\_") + "%"
    held = db.query(MCQTag.label).filter(MCQTag.mcq_id == mcq_id, MCQTag.axis == AXIS_REVIEW,
                                         MCQTag.label.like(like, escape="\\")).first()
    if req.action == "accept":
        if not held:
            raise HTTPException(status_code=404, detail="This question is not waiting for review on this paper")
        topic_id = held[0].split(":", 1)[1]
    elif req.action == "reassign":
        if not req.topic_id or not find_topic(paper, req.topic_id):
            raise HTTPException(status_code=422, detail="Choose a topic of this paper")
        topic_id = req.topic_id
        if req.subtopic and req.subtopic not in find_topic(paper, topic_id)[1].get("subtopics", []):
            raise HTTPException(status_code=422, detail="That subtopic is not under this topic")
    elif req.action == "reject":
        topic_id = None
    else:
        raise HTTPException(status_code=422, detail="action must be accept, reassign or reject")
    if db.get(MCQ, mcq_id) is None:
        raise HTTPException(status_code=404, detail="Question not found")
    record_review(db, req.exam_id, req.paper_id, mcq_id, topic_id, req.subtopic if req.action == "reassign" else None)
    logger.info("TOS review: %s %s mcq %s on %s/%s -> %s", admin.username, req.action, mcq_id,
                req.exam_id, req.paper_id, topic_id or "off-syllabus")
    return {"mcq_id": mcq_id, "action": req.action, "topic_id": topic_id}
