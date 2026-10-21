"""What a Practice selection means, in one place: session start, harder versions, twists, timed papers and counts.

A scope is a small dict the Practice screen sends. FCPS Part 1 is sat as two papers, and a scope names one:

    Paper 1 - basic sciences, by subject and topic
    {"paper":    "1",                                   # the default when absent (older clients)
     "sources":  ["bank", "past"],                      # Paper 1 bank, FCPS Part 1 past papers (default both)
     "subjects": ["Anatomy"],                           # whole subjects
     "topics":   ["Anatomy|Upper Limb", "Physiology|Renal"],   # subject|topic (topic names repeat across subjects)
     "years":    [2023, 2024]}                          # past papers only: asked in these years

    Paper 2 - the candidate's specialty
    {"paper":       "2",
     "sources":     ["bank", "past"],                   # Paper 2 faculty bank, FCPS Part 1 past papers
     "specialties": ["Medicine", "Gynaecology & Obstetrics"]}

Subjects and topics come from the shared list (app/taxonomy.py) through the fcps_subject / fcps_topic tags
written by scripts/tag_taxonomy.py, so "Upper Limb" means the same questions whichever source they come from.
A subject picked together with some of its topics narrows to those topics.

Specialty is recorded differently by the two sources, so SPECIALTIES maps one name to both: the Paper 2 bank
keeps it in MCQ.sub_category ("Obstetrics & Gynaecology"), past papers in the 'specialty' tag ("Gynae & Obs"),
because each FCPS Part 1 past paper was sat by one faculty ("FCPS Part 1 · Psychiatry · Feb 2023").
A past-paper question therefore belongs to both papers: to Paper 1 through its basic-science subject and to
Paper 2 through the faculty that sat it. The archive does not record which of the two papers it was asked in.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, aliased

from app.models import MCQ, MCQTag, PastPaper, PastPaperQuestion
from app.retention import access_scope
from app.taxonomy import SUBJECTS, topics_of

BANK = "Paper 1 · Basic sciences"
BANK2 = "Paper 2 · Faculty"
PAST = "Past papers · FCPS Part 1"
PAST_DENTAL = "Past papers · FCPS Part 1 (Dentistry)"
# MCQs students checked in Utilities that the app did not hold (app/checked_mcqs.py). They join
# the bank of whichever paper their neighbours placed them in: Paper 1 when they carry a subject
# tag, Paper 2 when they carry a specialty tag.
CHECKED = "Checked MCQs"
PGET_BANK1 = "Seed · PGET Paper I (MBBS)"
SOURCE_CATEGORY = {"bank": BANK, "past": PAST}
PAPER_CATEGORIES = {
    "1": {"bank": (BANK, CHECKED), "past": (PAST,)},
    "2": {"bank": (BANK2, CHECKED), "past": (PAST, PAST_DENTAL)},
    "pget1": {"bank": (PGET_BANK1,), "past": ()},
}
PAST_EXAM = "FCPS Part 1"
PAST_EXAMS = {"1": (PAST_EXAM,), "2": (PAST_EXAM, "FCPS Part 1 (Dentistry)"), "pget1": ()}

# Paper 2 specialties: the name shown -> how each source spells it. A specialty with no bank
# spelling is only in the past papers (no Paper 2 bank exists for it).
SPECIALTIES: dict[str, dict[str, tuple[str, ...]]] = {
    "Medicine": {"bank": ("Medicine",), "past": ("Medicine",)},
    "Surgery": {"bank": ("Surgery",), "past": ("Surgery",)},
    "Gynaecology & Obstetrics": {"bank": ("Obstetrics & Gynaecology",), "past": ("Gynae & Obs",)},
    "ENT": {"bank": ("ENT",), "past": ("ENT",)},
    "Ophthalmology": {"bank": ("Ophthalmology",), "past": ("Ophthalmology",)},
    "Radiology": {"bank": ("Radiology",), "past": ("Radiology",)},
    "Psychiatry": {"bank": ("Psychiatry",), "past": ("Psychiatry",)},
    "Anaesthesia": {"bank": ("Anaesthesia",), "past": ("Anesthesia",)},
    "Pathology": {"bank": (), "past": ("Pathology",)},
    "Dentistry": {"bank": (), "past": ("Dentistry",)},
}


# Difficulty bands for the builder (app/answer_stats.py: measured from students where enough answered, else the
# author's rating). A band keeps only questions with a known difficulty, so it narrows the pool a lot at first.
DIFFICULTY_BANDS = {"easier": (1, 2), "medium": (3, 3), "harder": (4, 5)}


def normalise(scope: dict | None) -> dict[str, Any]:
    scope = scope or {}
    raw_p = str(scope.get("paper") or "1")
    if raw_p in ("pget1", "pget_1", "pget"):
        paper = "pget1"
    elif raw_p == "2":
        paper = "2"
    else:
        paper = "1"
    sources = [s for s in (scope.get("sources") or ["bank", "past"]) if s in SOURCE_CATEGORY or paper == "pget1"] or ["bank"]
    subjects: list[str] = [str(s) for s in (scope.get("subjects") or [])]
    topics: list[tuple[str, str]] = []
    specialties: list[str] = []
    if paper == "1":
        subjects = [s for s in subjects if s in SUBJECTS]
        for pair in scope.get("topics") or []:
            s, _, t = str(pair).partition("|")
            if s in SUBJECTS and t in topics_of(s):
                topics.append((s, t))
    elif paper == "2":
        specialties = [s for s in (scope.get("specialties") or []) if s in SPECIALTIES]
    years = [int(y) for y in (scope.get("years") or []) if str(y).isdigit()]
    difficulty = scope.get("difficulty") if scope.get("difficulty") in DIFFICULTY_BANDS else None
    return {"paper": paper, "sources": sources, "subjects": subjects, "topics": topics,
            "specialties": specialties, "years": years, "difficulty": difficulty}


def buckets(scope: dict) -> list[tuple[str, str | None]]:
    """Groups a selection is spread across (harder versions round-robin over them): (subject, topic) on
    Paper 1, (specialty, None) on Paper 2, (subject, None) on PGET Paper 1."""
    sc = normalise(scope)
    if sc["paper"] == "pget1":
        return [(s, None) for s in sc["subjects"]]
    if sc["paper"] == "2":
        return [(s, None) for s in sc["specialties"]]
    picked_topics = {s for s, _ in sc["topics"]}
    out = list(sc["topics"]) + [(s, None) for s in sc["subjects"] if s not in picked_topics]
    return out


def _tagged(db: Session, axis: str, label: str):
    return db.query(MCQTag.mcq_id).filter(MCQTag.axis == axis, MCQTag.label == label)


def _categories(sc: dict) -> list[str]:
    return [c for s in sc["sources"] for c in PAPER_CATEGORIES[sc["paper"]][s]]


def _specialty_condition(db: Session, sc: dict, name: str):
    """One Paper 2 specialty across both sources, each matched by its own spelling."""
    spell = SPECIALTIES[name]
    conds = []
    tagged = MCQ.id.in_(db.query(MCQTag.mcq_id).filter(MCQTag.axis == "specialty", MCQTag.label.in_(spell["past"])))
    if "bank" in sc["sources"] and spell["bank"]:
        conds.append(and_(MCQ.main_category == BANK2, MCQ.sub_category.in_(spell["bank"])))
    if "bank" in sc["sources"]:
        conds.append(and_(MCQ.main_category == CHECKED, tagged))
    if "past" in sc["sources"]:
        conds.append(and_(MCQ.main_category.in_(PAPER_CATEGORIES["2"]["past"]), tagged))
    return or_(*conds) if conds else MCQ.id.is_(None)


def restrict(db: Session, query, scope: dict, bucket: tuple[str, str | None] | None = None):
    """Filter an MCQ query to a scope (or to one of its buckets)."""
    sc = normalise(scope)
    query = query.filter(MCQ.main_category.in_(_categories(sc)))
    groups = [bucket] if bucket else buckets(scope)
    if sc["paper"] == "pget1":
        if sc["subjects"]:
            query = query.filter(MCQ.sub_category.in_(sc["subjects"]))
        if sc["difficulty"]:
            from app.answer_stats import difficulty_sql
            lo, hi = DIFFICULTY_BANDS[sc["difficulty"]]
            query = query.filter(difficulty_sql().between(lo, hi))
        return query

    if not groups and sc["paper"] == "2":
        # Paper 2 with no specialty picked is every specialty, not every past-paper question: a
        # question no faculty sat is not a Paper 2 question.
        groups = [(s, None) for s in SPECIALTIES]
    if groups:
        conds = []
        for s, t in groups:
            if sc["paper"] == "2":
                if s in SPECIALTIES:
                    conds.append(_specialty_condition(db, sc, s))
                continue
            c = MCQ.id.in_(_tagged(db, "fcps_subject", s))
            if t:
                c = and_(c, MCQ.id.in_(_tagged(db, "fcps_topic", t)))
            conds.append(c)
        query = query.filter(or_(*conds) if conds else MCQ.id.is_(None))
    elif sc["paper"] == "1":
        # All of Paper 1: a checked MCQ belongs only once its neighbours gave it a subject.
        query = query.filter(or_(MCQ.main_category != CHECKED,
                                 MCQ.id.in_(db.query(MCQTag.mcq_id).filter(MCQTag.axis == "fcps_subject"))))
    if sc["years"] and "past" in sc["sources"]:
        in_years = MCQ.id.in_(db.query(PastPaperQuestion.mcq_id).join(PastPaper, PastPaper.id == PastPaperQuestion.paper_id)
                              .filter(PastPaper.exam.in_(PAST_EXAMS[sc["paper"]]), PastPaper.year.in_(sc["years"])))
        # The bank has no years: with years picked, bank questions stay in only when the bank is a source too.
        bank = PAPER_CATEGORIES[sc["paper"]]["bank"]
        query = query.filter(or_(in_years, MCQ.main_category.in_(bank)) if "bank" in sc["sources"] else in_years)
    if sc["difficulty"]:
        from app.answer_stats import difficulty_sql
        lo, hi = DIFFICULTY_BANDS[sc["difficulty"]]
        query = query.filter(difficulty_sql().between(lo, hi))
    return query


def pget_counts(db: Session, user) -> list[dict[str, Any]]:
    """PGET Paper 1: each subject with its question count."""
    rows = (access_scope(db.query(MCQ.sub_category, func.count(MCQ.id))
                         .filter(MCQ.status == "ready", MCQ.twist_of.is_(None),
                                 MCQ.main_category == PGET_BANK1), user)
            .group_by(MCQ.sub_category).order_by(MCQ.sub_category).all())
    return [{"subject": sub, "count": int(cnt)} for sub, cnt in rows if sub]


def practice_tree(db: Session, user) -> dict[str, Any]:
    """Subjects -> topics with question counts per source, as this user can see them."""
    base = access_scope(db.query(MCQ.id).filter(MCQ.status == "ready", MCQ.twist_of.is_(None),
                                                MCQ.main_category.in_([BANK, CHECKED, PAST])), user).subquery()
    ts, tt = aliased(MCQTag), aliased(MCQTag)
    rows = (db.query(ts.label, tt.label, MCQ.main_category, func.count(MCQ.id))
            .select_from(MCQ).join(base, base.c.id == MCQ.id)
            .join(ts, and_(ts.mcq_id == MCQ.id, ts.axis == "fcps_subject"))
            .outerjoin(tt, and_(tt.mcq_id == MCQ.id, tt.axis == "fcps_topic"))
            .group_by(ts.label, tt.label, MCQ.main_category).all())
    counts: dict[tuple[str, str | None], dict[str, int]] = {}
    for s, t, cat, n in rows:
        key = "past" if cat == PAST else "bank"
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
    return {"subjects": tree, "years": years, "specialties": specialty_counts(db, user), "pget": pget_counts(db, user)}


def specialty_counts(db: Session, user) -> list[dict[str, Any]]:
    """Paper 2: each specialty with its question count per source, as this user can see them."""
    def ready(cats):
        return access_scope(db.query(MCQ).filter(MCQ.status == "ready", MCQ.twist_of.is_(None),
                                                 MCQ.main_category.in_(cats)), user)

    bank = dict(ready([BANK2]).with_entities(MCQ.sub_category, func.count(MCQ.id))
                .group_by(MCQ.sub_category).all())
    def by_tag(cats):
        return dict(ready(cats).join(MCQTag, and_(MCQTag.mcq_id == MCQ.id, MCQTag.axis == "specialty"))
                    .with_entities(MCQTag.label, func.count(func.distinct(MCQ.id)))
                    .group_by(MCQTag.label).all())

    past = by_tag(list(PAPER_CATEGORIES["2"]["past"]))
    checked = by_tag([CHECKED])
    return [{"specialty": name,
             "bank": sum(int(bank.get(x, 0)) for x in spell["bank"]) + sum(int(checked.get(x, 0)) for x in spell["past"]),
             "past": sum(int(past.get(x, 0)) for x in spell["past"])}
            for name, spell in SPECIALTIES.items()]
