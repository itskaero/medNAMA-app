"""What a Practice selection means, in one place: session start, harder versions, twists and the counts.

A scope is a small dict the Practice screen sends:

    {"sources":  ["bank", "past"],                      # Paper 1 bank, FCPS Part 1 past papers (default both)
     "subjects": ["Anatomy"],                           # whole subjects
     "topics":   ["Anatomy|Upper Limb", "Physiology|Renal"],   # subject|topic (topic names repeat across subjects)
     "years":    [2023, 2024]}                          # past papers only: asked in these years

Subjects and topics come from the shared list (app/taxonomy.py) through the fcps_subject / fcps_topic tags
written by scripts/tag_taxonomy.py, so "Upper Limb" means the same questions whichever source they come from.
A subject picked together with some of its topics narrows to those topics.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, aliased

from app.models import MCQ, MCQTag, PastPaper, PastPaperQuestion
from app.retention import access_scope
from app.taxonomy import SUBJECTS, topics_of

BANK = "Paper 1 · Basic sciences"
PAST = "Past papers · FCPS Part 1"
SOURCE_CATEGORY = {"bank": BANK, "past": PAST}
PAST_EXAM = "FCPS Part 1"


def normalise(scope: dict | None) -> dict[str, Any]:
    scope = scope or {}
    sources = [s for s in (scope.get("sources") or ["bank", "past"]) if s in SOURCE_CATEGORY] or ["bank", "past"]
    subjects = [s for s in (scope.get("subjects") or []) if s in SUBJECTS]
    topics = []
    for pair in scope.get("topics") or []:
        s, _, t = str(pair).partition("|")
        if s in SUBJECTS and t in topics_of(s):
            topics.append((s, t))
    years = [int(y) for y in (scope.get("years") or []) if str(y).isdigit()]
    return {"sources": sources, "subjects": subjects, "topics": topics, "years": years}


def buckets(scope: dict) -> list[tuple[str, str | None]]:
    """(subject, topic) groups a selection is spread across (harder versions round-robin over them)."""
    sc = normalise(scope)
    picked_topics = {s for s, _ in sc["topics"]}
    out = list(sc["topics"]) + [(s, None) for s in sc["subjects"] if s not in picked_topics]
    return out


def _tagged(db: Session, axis: str, label: str):
    return db.query(MCQTag.mcq_id).filter(MCQTag.axis == axis, MCQTag.label == label)


def restrict(db: Session, query, scope: dict, bucket: tuple[str, str | None] | None = None):
    """Filter an MCQ query to a scope (or to one of its buckets)."""
    sc = normalise(scope)
    query = query.filter(MCQ.main_category.in_([SOURCE_CATEGORY[s] for s in sc["sources"]]))
    groups = [bucket] if bucket else buckets(scope)
    if groups:
        conds = []
        for s, t in groups:
            c = MCQ.id.in_(_tagged(db, "fcps_subject", s))
            if t:
                c = and_(c, MCQ.id.in_(_tagged(db, "fcps_topic", t)))
            conds.append(c)
        query = query.filter(or_(*conds))
    if sc["years"] and "past" in sc["sources"]:
        in_years = MCQ.id.in_(db.query(PastPaperQuestion.mcq_id).join(PastPaper, PastPaper.id == PastPaperQuestion.paper_id)
                              .filter(PastPaper.exam == PAST_EXAM, PastPaper.year.in_(sc["years"])))
        # The bank has no years: with years picked, bank questions stay in only when the bank is a source too.
        query = query.filter(or_(in_years, MCQ.main_category == BANK) if "bank" in sc["sources"] else in_years)
    return query


def practice_tree(db: Session, user) -> dict[str, Any]:
    """Subjects -> topics with question counts per source, as this user can see them."""
    base = access_scope(db.query(MCQ.id).filter(MCQ.status == "ready", MCQ.twist_of.is_(None),
                                                MCQ.main_category.in_([BANK, PAST])), user).subquery()
    ts, tt = aliased(MCQTag), aliased(MCQTag)
    rows = (db.query(ts.label, tt.label, MCQ.main_category, func.count(MCQ.id))
            .select_from(MCQ).join(base, base.c.id == MCQ.id)
            .join(ts, and_(ts.mcq_id == MCQ.id, ts.axis == "fcps_subject"))
            .outerjoin(tt, and_(tt.mcq_id == MCQ.id, tt.axis == "fcps_topic"))
            .group_by(ts.label, tt.label, MCQ.main_category).all())
    counts: dict[tuple[str, str | None], dict[str, int]] = {}
    for s, t, cat, n in rows:
        key = "bank" if cat == BANK else "past"
        counts.setdefault((s, t), {"bank": 0, "past": 0})[key] += int(n)
    years = [y for (y,) in db.query(PastPaper.year).filter(PastPaper.exam == PAST_EXAM, PastPaper.year.isnot(None))
             .distinct().order_by(PastPaper.year.desc())]
    tree = []
    for s in SUBJECTS:
        topics = [{"topic": t, **counts.get((s, t), {"bank": 0, "past": 0})} for t in topics_of(s)]
        untagged = counts.get((s, None), {"bank": 0, "past": 0})
        tree.append({"subject": s, "topics": topics,
                     "bank": sum(x["bank"] for x in topics) + untagged["bank"],
                     "past": sum(x["past"] for x in topics) + untagged["past"]})
    return {"subjects": tree, "years": years}
