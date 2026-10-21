"""The learner's preparation profile, today's plan, and anonymous screen-usage counts.

- /api/prep/profile   which exam and TOS paper the learner is preparing for, the exam date and daily study time.
                      Set by onboarding; it shapes Today and the navigation (e.g. TOACS for clinical papers).
- /api/today/plan     one next step plus the week's target, from the learner's own TOS progress on their paper.
- /api/usage          +1 for a screen opened today. Anonymous: no user id and no content is stored.
- /api/admin/usage    per-screen totals, for deciding what to keep before building more.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app import exam_levels
from app.auth import require_admin, require_student_or_admin
from app.deps import get_db
from app.models import ConceptReview, UsageCount, User

router = APIRouter()

# TOS exam -> the programme the rest of the app (chat, revision, MCQ generation) reads.
EXAM_LEVEL = {"pget": exam_levels.PGET, "fcps1": exam_levels.FCPS_1, "mbbs": exam_levels.MBBS}
MINUTES = (30, 60, 120)
# Screens that may be counted (the app's view ids); anything else is ignored.
KNOWN_VIEWS = {
    "dashboard", "daily", "sprint", "chat", "revise", "review", "lookalikes", "library", "tos", "toacs", "station",
    "quiz", "pastpapers", "exams", "mock", "paper", "duel", "offline", "mistakes", "study", "bookmarks", "stats",
    "mcq-bank", "check", "highyield", "users", "referee", "reports", "content", "more",
}


def _clinical(exam_id: str | None, paper_id: str | None, level: str | None) -> bool:
    """Does this learner sit a clinical / specialty paper (where viva and TOACS practice belongs)?"""
    if level in (exam_levels.FCPS_2, exam_levels.MD, exam_levels.MS):
        return True
    # PGET Paper II is the specialty paper the stations are written for; FCPS-I is MCQ-only (TOACS is FCPS-II).
    return bool(paper_id and exam_id == "pget" and paper_id.startswith("p2-"))


def profile_payload(user: User) -> dict[str, Any]:
    from app.tos import tos_registry

    p = dict(user.prep_profile or {})
    paper = tos_registry.get_paper(p.get("exam_id") or "", p.get("paper_id") or "") if p.get("paper_id") else None
    level = exam_levels.normalise(user.exam_level)
    return {
        "exam_level": level,
        "exam_date": user.exam_date.isoformat() if user.exam_date else None,
        "exam_id": p.get("exam_id"), "paper_id": p.get("paper_id"),
        "paper_title": paper["title"] if paper else None,
        "minutes_per_day": p.get("minutes_per_day"),
        "onboarded": bool(p.get("onboarded_at")),
        # Existing users who already chose an exam in the sidebar only confirm their paper (no full onboarding).
        "needs_onboarding": not p.get("onboarded_at") and not level,
        "needs_paper": not p.get("onboarded_at") and bool(level),
        "clinical": _clinical(p.get("exam_id"), p.get("paper_id"), level),
    }


@router.get("/api/prep/profile")
def get_profile(db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    from app.tos import tos_registry

    u = db.get(User, user.id)
    options = [{"id": e["id"], "short_name": e["short_name"], "title": e["title"],
                "papers": [{"id": p["id"], "title": p["title"], "category": p.get("category"),
                            "available": p.get("available", True)} for p in e["papers"]]}
               for e in tos_registry.get_examinations_summary()]
    return {**profile_payload(u), "options": options, "other_levels": [
        {"id": exam_levels.FCPS_2, "label": "FCPS-II (clinical, TOACS/viva)"},
        {"id": exam_levels.MD, "label": "MD"}, {"id": exam_levels.MS, "label": "MS"},
        {"id": exam_levels.USMLE, "label": "USMLE"}], "minutes_choices": list(MINUTES)}


class ProfileReq(BaseModel):
    exam_id: str | None = None           # a TOS exam (pget | fcps1 | mbbs), or None for an exam without a TOS here
    paper_id: str | None = None
    exam_level: str | None = None        # only for exams without a TOS here (FCPS-II, MD, MS, USMLE)
    exam_date: str | None = None         # YYYY-MM-DD
    minutes_per_day: int = Field(60, ge=10, le=600)


@router.put("/api/prep/profile")
def put_profile(req: ProfileReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    from app.tos import tos_registry

    u = db.get(User, user.id)
    if req.exam_id:
        if not tos_registry.get_paper(req.exam_id, req.paper_id or ""):
            raise HTTPException(status_code=422, detail="Choose a paper of that exam")
        u.exam_level = EXAM_LEVEL.get(req.exam_id)
    else:
        level = exam_levels.normalise(req.exam_level)
        if not level:
            raise HTTPException(status_code=422, detail="Choose your exam")
        u.exam_level = level
    if req.exam_date:
        try:
            d = date.fromisoformat(req.exam_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="exam_date must be YYYY-MM-DD")
        if d < date.today():
            raise HTTPException(status_code=422, detail="The exam date is in the past")
        u.exam_date = d
    u.prep_profile = {"exam_id": req.exam_id, "paper_id": req.paper_id if req.exam_id else None,
                      "minutes_per_day": req.minutes_per_day, "onboarded_at": datetime.utcnow().isoformat()}
    db.commit()
    return profile_payload(u)


@router.get("/api/today/plan")
def today_plan(db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    """One recommended next step and this week's target, each with its reason (the learner can always choose
    something else). Order: unfinished session -> due reviews -> today's TOS topic -> a mock when ready."""
    from app.routers.hub import today as hub_today
    from app.routers.tos import tos_progress

    u = db.get(User, user.id)
    prof = profile_payload(u)
    days_left = (u.exam_date - date.today()).days if u.exam_date else None
    due = db.query(func.count()).select_from(ConceptReview).filter(
        ConceptReview.user_id == u.id, ConceptReview.next_due <= datetime.utcnow()).scalar() or 0
    cont = (hub_today(db, u) or {}).get("continue")

    tos: dict[str, Any] | None = None
    if prof["paper_id"]:
        try:
            tos = tos_progress(exam_id=prof["exam_id"], paper_id=prof["paper_id"], db=db, current_user=u)
        except HTTPException:
            tos = None
    total = (tos or {}).get("topics_total") or 0
    practised = (tos or {}).get("topics_practised") or 0
    weeks_left = max(1, math.ceil(days_left / 7)) if days_left is not None and days_left > 0 else None
    weekly_target = math.ceil(max(0, total - practised) / weeks_left) if (weeks_left and total) else None
    per_day = max(1, (prof["minutes_per_day"] or 60) // 40)
    topics = _studyable(db, prof["exam_id"], prof["paper_id"], tos)[:per_day] if tos else []

    tos_params = {"exam": prof["exam_id"], "paper": prof["paper_id"]}
    if cont:
        primary = {"kind": "continue", "label": f"Continue: {cont['label']}",
                   "why": f"{cont['answered']} of {cont['total']} answered", "view": "quiz",
                   "params": {"resume": cont["attempt_id"]}}
    elif due >= 5:
        primary = {"kind": "review", "label": f"Review {due} concepts due today",
                   "why": "Spaced review keeps what you have learnt; due items first", "view": "daily", "params": {}}
    elif topics:
        t = topics[0]
        primary = {"kind": "topic", "label": f"Study {t['topic']}", "why": f"{t['subject']} · {t['why']}",
                   "view": "tos", "params": {**tos_params, "topic": t["topic_id"]}}
    elif tos and total and (practised / total >= 0.5 or (days_left is not None and days_left <= 14)):
        primary = {"kind": "mock", "label": "Sit a mock built to your paper's blueprint",
                   "why": f"{practised} of {total} topics practised" + (f", {days_left} days to go" if days_left else ""),
                   "view": "tos", "params": {**tos_params, "section": "test"}}
    else:
        primary = {"kind": "setup", "label": "Choose your paper to get a plan", "why": "Today needs to know your exam",
                   "view": "dashboard", "params": {"onboard": 1}} if not prof["paper_id"] else {
                   "kind": "dose", "label": "Do today's Daily Dose", "why": "A short mixed set to keep going",
                   "view": "daily", "params": {}}
    return {
        "profile": prof, "days_left": days_left, "due_reviews": due,
        "topics_total": total, "topics_practised": practised, "weekly_target": weekly_target,
        "today_topics": [{**t, "params": {**tos_params, "topic": t["topic_id"]}} for t in topics],
        "primary": primary,
    }


def _studyable(db: Session, exam_id: str, paper_id: str, tos: dict[str, Any]) -> list[dict[str, Any]]:
    """Topics to suggest, in tos_progress's order (weak first, then untouched by how much of the paper they carry),
    keeping only topics with something to study: a written refresher or notes sheet, or questions in the bank.
    A topic the library has no passages on and no questions for would send the learner to an empty page."""
    from app.tos import tos_registry
    from app.tos_sheets import scope_key

    paper = tos_registry.get_paper(exam_id, paper_id)
    rows = tos.get("topics", [])
    if not paper or not rows:
        return []
    keys = {scope_key(k, exam_id, paper_id, t["topic_id"], None): t["topic_id"] for t in rows for k in ("refresher", "notes")}
    ready = {keys[k] for (k,) in db.execute(text("SELECT scope_key FROM topic_summaries WHERE scope_key = ANY(:k)"),
                                             {"k": list(keys)})}
    ready |= {label.split(":", 1)[1] for (label,) in db.execute(text(
        "SELECT DISTINCT label FROM mcq_tags WHERE axis = 'tos_topic' AND label = ANY(:l)"),
        {"l": [f"{exam_id}:{t['topic_id']}" for t in rows]})}
    weights = {s["subject_name"]: s.get("official_mcq_count") or 0 for s in paper["subjects"]}
    weak = [t for t in tos.get("suggestions", []) if t.get("answers")]
    untouched = sorted((t for t in rows if not t["answers"] and t["topic_id"] in ready),
                       key=lambda t: -weights.get(t["subject"], 0))
    return weak + [{**t, "why": "not practised yet" + (f"; {weights[t['subject']]} questions on the paper"
                                                      if weights.get(t["subject"]) else "")} for t in untouched]


class UsageReq(BaseModel):
    view: str = Field(..., max_length=40)


@router.post("/api/usage")
def count_usage(req: UsageReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    """+1 for this screen today. Signed-in only (to keep the counts clean), but nothing about the user is kept."""
    if req.view not in KNOWN_VIEWS:
        return {"ok": False}
    db.execute(text("INSERT INTO usage_counts (view, day, n) VALUES (:v, :d, 1) "
                    "ON CONFLICT (view, day) DO UPDATE SET n = usage_counts.n + 1"), {"v": req.view, "d": date.today()})
    db.commit()
    return {"ok": True}


@router.get("/api/admin/usage")
def usage(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    since = date.today() - timedelta(days=days - 1)
    rows = (db.query(UsageCount.view, func.sum(UsageCount.n), func.count(UsageCount.day))
            .filter(UsageCount.day >= since, UsageCount.n > 0).group_by(UsageCount.view).order_by(func.sum(UsageCount.n).desc()).all())
    return {"days": days, "since": since.isoformat(),
            "views": [{"view": v, "opens": int(n), "active_days": int(d)} for v, n, d in rows]}
