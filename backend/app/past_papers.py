"""Past papers: imported exam-year collections (scripts/seed_past_papers.py).

A past paper here is one exam year of an archive (e.g. FCPS Part 1 - 2024), not a
single 200-question sitting: the archive groups every recalled question of a year.
Questions live in the normal bank (mcqs) and are linked to their papers through
past_paper_questions, so the same question can belong to several years and still
be one row for the retention engine. mcq_tags keeps every subject / topic /
specialty label, which is what the filters use.

Restricted rows (mcqs.access = 'restricted') are only visible to users allowed by
PAST_PAPERS_ACCESS; callers check restricted_allowed() before anything here.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import MCQ, AnswerEvent, MCQMedia, MCQTag, PastPaper, PastPaperQuestion, User, WeeklyMock

AXES = ("subject", "topic", "specialty")
TIMED_DEFAULT_QUESTIONS = 100
TIMED_DEFAULT_MINUTES = 120


def past_paper_filter(db: Session, query, exam: str | None, years: list[int] | None,
                      tags: dict[str, list[str]] | None):
    """Restrict an MCQ query to questions of an exam's past papers (optionally some years),
    carrying at least one of the chosen labels on every axis given."""
    papers = db.query(PastPaperQuestion.mcq_id).join(PastPaper, PastPaper.id == PastPaperQuestion.paper_id)
    if exam:
        papers = papers.filter(PastPaper.exam == exam)
    if years:
        papers = papers.filter(PastPaper.year.in_([int(y) for y in years]))
    query = query.filter(MCQ.id.in_(papers))
    for axis, labels in (tags or {}).items():
        if axis in AXES and labels:
            query = query.filter(MCQ.id.in_(
                db.query(MCQTag.mcq_id).filter(MCQTag.axis == axis, MCQTag.label.in_(labels))))
    return query


def overview(db: Session, user: User) -> dict[str, Any]:
    """Exams -> years, with question counts and this user's progress."""
    rows = db.execute(text(
        "SELECT p.id, p.exam, p.year, p.title, count(DISTINCT q.mcq_id) AS total, "
        "count(DISTINCT e.mcq_id) AS answered, "
        "count(DISTINCT e.mcq_id) FILTER (WHERE e.is_correct) AS correct "
        "FROM past_papers p JOIN past_paper_questions q ON q.paper_id = p.id "
        "LEFT JOIN answer_events e ON e.mcq_id = q.mcq_id AND e.user_id = :u "
        "GROUP BY p.id ORDER BY p.exam, p.year DESC"), {"u": user.id}).all()
    exams: dict[str, dict[str, Any]] = {}
    for pid, exam, year, title, total, answered, correct in rows:
        e = exams.setdefault(exam, {"exam": exam, "papers": [], "total": 0, "answered": 0, "correct": 0})
        e["papers"].append({"id": pid, "year": year, "title": title, "total": int(total),
                            "answered": int(answered), "correct": int(correct)})
        e["total"] += int(total)
        e["answered"] += int(answered)
        e["correct"] += int(correct)
    return {"exams": list(exams.values())}


def scope(db: Session, user: User, exam: str | None, years: list[int] | None,
          tags: dict[str, list[str]] | None) -> dict[str, Any]:
    """How many questions match, the user's progress on them, and label counts for the filters.
    Each axis's counts ignore that axis's own selection (so choices can be widened)."""
    def base(tags_):
        return past_paper_filter(db, db.query(MCQ.id).filter(MCQ.status == "ready"), exam, years, tags_)

    matching = base(tags).subquery()
    total = db.query(func.count()).select_from(matching).scalar() or 0
    # Per question: answered at all, and ever answered correctly ("missed" = answered, never right).
    per_q = (db.query(AnswerEvent.mcq_id, func.bool_or(AnswerEvent.is_correct).label("ok"))
             .filter(AnswerEvent.user_id == user.id, AnswerEvent.mcq_id.in_(db.query(matching.c.id)))
             .group_by(AnswerEvent.mcq_id).subquery())
    answered = db.query(func.count()).select_from(per_q).scalar()
    missed = db.query(func.count()).select_from(per_q).filter(~per_q.c.ok).scalar()
    facets: dict[str, list[dict[str, Any]]] = {}
    for axis in AXES:
        others = {a: v for a, v in (tags or {}).items() if a != axis}
        ids = base(others).subquery()
        counts = (db.query(MCQTag.label, func.count(MCQTag.mcq_id))
                  .filter(MCQTag.axis == axis, MCQTag.mcq_id.in_(db.query(ids.c.id)))
                  .group_by(MCQTag.label).order_by(func.count(MCQTag.mcq_id).desc()).all())
        facets[axis] = [{"label": label, "count": int(n)} for label, n in counts]
    return {"count": int(total), "answered": int(answered or 0), "missed": int(missed or 0), "facets": facets}


def create_timed_paper(db: Session, user: User, exam: str, years: list[int] | None,
                       tags: dict[str, list[str]] | None, count: int = TIMED_DEFAULT_QUESTIONS,
                       minutes: int = TIMED_DEFAULT_MINUTES) -> WeeklyMock:
    """A personal timed paper from the filtered past-paper questions, mixed across subjects like the
    real exam (round-robin by subject), skipping questions whose figure is missing."""
    count = max(10, min(200, int(count or TIMED_DEFAULT_QUESTIONS)))
    minutes = max(10, min(240, int(minutes or TIMED_DEFAULT_MINUTES)))
    query = past_paper_filter(db, db.query(MCQ.id).filter(MCQ.status == "ready"), exam, years, tags)
    query = query.filter(~MCQ.id.in_(db.query(MCQTag.mcq_id).filter(MCQTag.axis == "flag",
                                                                    MCQTag.label == "figure-missing")))
    ids = [i for (i,) in query.order_by(func.random()).limit(4000).all()]
    subject_of = dict(db.query(MCQTag.mcq_id, func.min(MCQTag.label)).filter(
        MCQTag.axis == "subject", MCQTag.mcq_id.in_(ids)).group_by(MCQTag.mcq_id).all()) if ids else {}
    groups: dict[str, list[int]] = {}
    for i in ids:
        groups.setdefault(subject_of.get(i) or "Other", []).append(i)
    picked: list[int] = []
    while len(picked) < count and any(groups.values()):
        for g in sorted(groups):
            if groups[g] and len(picked) < count:
                picked.append(groups[g].pop())
    label = exam + (f" {', '.join(str(y) for y in sorted(years))}" if years else " (all years)")
    chosen = [f"{v[0]}" + (f" +{len(v) - 1}" if len(v) > 1 else "") for v in (tags or {}).values() if v]
    mock = WeeklyMock(week_start=date.today(), part="pp", track=f"u{user.id}:{uuid.uuid4().hex[:10]}",
                      title=f"Timed paper · {label}" + (f" · {', '.join(chosen)}" if chosen else ""),
                      mcq_ids=picked, duration_min=minutes)
    db.add(mock)
    db.commit()
    return mock


def question_media(db: Session, mcq_ids: list[int]) -> dict[int, list[int]]:
    """Image ids shown with each question (role 'question')."""
    if not mcq_ids:
        return {}
    out: dict[int, list[int]] = {}
    for mid, media_id in db.query(MCQMedia.mcq_id, MCQMedia.id).filter(
            MCQMedia.mcq_id.in_(mcq_ids), MCQMedia.role == "question").order_by(MCQMedia.id):
        out.setdefault(mid, []).append(media_id)
    return out


def paper_years(db: Session, mcq_ids: list[int]) -> dict[int, list[int]]:
    """Which past-paper years each question was asked in (for 'Past paper 2023, 2025' badges).
    Uses mcqs.asked_years (which counts reworded repeats in other years) when it has been computed."""
    if not mcq_ids:
        return {}
    ranked = {mid: [int(y) for y in ys] for mid, ys in db.query(MCQ.id, MCQ.asked_years).filter(
        MCQ.id.in_(mcq_ids), MCQ.asked_years.isnot(None))}
    out: dict[int, list[int]] = {}
    for mid, year in (db.query(PastPaperQuestion.mcq_id, PastPaper.year)
                      .join(PastPaper, PastPaper.id == PastPaperQuestion.paper_id)
                      .filter(PastPaperQuestion.mcq_id.in_(mcq_ids)).distinct()):
        if year:
            out.setdefault(mid, []).append(int(year))
    out.update(ranked)
    return {k: sorted(v) for k, v in out.items()}
