"""Past papers: imported recalled exam papers from two archives.

Radiant (scripts/seed_past_papers.py) groups every recalled question of a year into one paper
(FCPS Part 1 - 2024); MediVerse (scripts/seed_mediverse.py) has the individual sittings
(Surgery · 18 Sep 2019 (M+E)) plus a few undated collections. Questions live in the normal bank
(mcqs) and are linked to their papers through past_paper_questions, so a question can belong to
several papers and still be one row for the retention engine.

The archives are not merged: a question both recalled is two rows sharing mcqs.recall_group
(scripts/link_recalls.py). Counts, sessions and "seen" treat a group as one question.
Filters: the standard subject (sub_category), mcq_tags topic / specialty (the faculty whose
paper it was) / system, the archive, and optionally specific sittings (tags["paper"]).

Restricted rows (mcqs.access = 'restricted') are only visible to users allowed by
PAST_PAPERS_ACCESS; callers check restricted_allowed() before anything here.
"""

import difflib
import re
import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session, aliased

from app.models import MCQ, AnswerEvent, MCQMedia, MCQTag, PastPaper, PastPaperQuestion, User, WeeklyMock

# Filter axes. subject is the standard subject (mcqs.sub_category, as readiness uses) so it covers both
# archives; source is the archive; the rest are mcq_tags axes. "paper" (past_papers ids) narrows to sittings.
AXES = ("subject", "topic", "specialty", "system", "source")
TAG_AXES = ("topic", "specialty", "system")
SOURCES = {"Radiant": "pastpaper:radiant-notes", "MediVerse": "pastpaper:mediverse"}
SOURCE_LABEL = {v: k for k, v in SOURCES.items()}
BANK = "Question bank"   # a bank question an archive linked to its paper instead of importing a copy


def source_label(source: str | None) -> str:
    return SOURCE_LABEL.get(source or "", BANK)
TIMED_DEFAULT_QUESTIONS = 100
TIMED_DEFAULT_MINUTES = 120


_ANSWER_STOP = {"the", "of", "and", "in", "to", "a", "an", "is", "for", "with", "by", "on", "or", "at", "from"}
_GENERIC_ANSWER = {"increased", "decreased", "increase", "decrease", "normal", "raised", "reduced", "high", "low",
                   "none", "above", "both", "true", "false", "absent", "present", "left", "right"}


def answer_norm(s: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


# Near-identical spellings that mean opposite things; fuzzy matching must never join them.
_OPPOSED = (("hyper", "hypo"), ("post", "pre"), ("medial", "lateral"), ("anterior", "posterior"),
            ("superior", "inferior"), ("left", "right"), ("upper", "lower"), ("increase", "decrease"),
            ("positive", "negative"), ("acute", "chronic"), ("antagonist", "agonist"), ("afferent", "efferent"),
            ("abduct", "adduct"), ("flexion", "extension"), ("proximal", "distal"), ("endo", "exo"),
            ("intra", "extra"), ("inhibit", "stimulat"), ("sensitive", "resistant"), ("benign", "malignant"))


def _opposed(a: str, b: str) -> bool:
    """'hypokalemia' vs 'hyperkalemia', 'vitamin b12' vs 'vitamin b1', 'agonist' vs 'antagonist'."""
    da, db_ = re.findall(r"\d+", a), re.findall(r"\d+", b)
    if da and db_ and da != db_:   # both give numbers, and they differ
        return True
    for p, q in _OPPOSED:
        pa, pb, qa, qb = p in a, p in b, q in a, q in b
        if (pa and qb and not pb) or (qa and pb and not pa):
            return True
    return False


def _token_match(a: str, b: str) -> bool:
    """One word written two ways: plural ('hamstrings'), an initial ('e' for 'escherichia'), a spelling slip."""
    if a == b or a.rstrip("s") == b.rstrip("s"):
        return True
    if len(a) == 1 or len(b) == 1:
        return b.startswith(a) if len(a) == 1 else a.startswith(b)
    return (min(len(a), len(b)) >= 5 and not _opposed(a, b)
            and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)


def answers_agree(a: str, b: str) -> bool:
    """Two normalised answers name the same thing: 'Sitagliptin' ~ 'Sitagliptin (DPP-4 inhibitor)',
    'E. coli' ~ 'Escherichia coli', 'hamstrings' ~ 'hamstring muscles', 'M'Fadyean' ~ 'McFadyen'."""
    if not a or not b:
        return False
    if a == b:
        return True
    if _opposed(a, b):
        return False
    if min(len(a), len(b)) >= 6 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85:
        return True
    ta = {t for t in a.split() if t not in _ANSWER_STOP}
    tb = {t for t in b.split() if t not in _ANSWER_STOP}
    if not ta or not tb:
        return False
    if len(ta & tb) / len(ta | tb) >= 0.6:
        return True
    # One answer spells out the other ('Sitagliptin' in 'Sitagliptin DPP 4 inhibitor'), provided the
    # shorter one is specific: 'Increased' inside 'Increased renin' is not the same key.
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    big_words = (a if big is ta else b).split()
    # Acronyms of consecutive words: 'ct' ~ 'connective tissue', 'dic' ~ 'disseminated intravascular coagulation'
    acronyms = {"".join(w[0] for w in big_words[i:j]) for i in range(len(big_words))
                for j in range(i + 2, min(len(big_words), i + 6) + 1)}
    covered = all(any(_token_match(s, t) for t in big) or (2 <= len(s) <= 5 and s in acronyms) for s in small)
    return covered and any((len(t) >= 4 or t in acronyms) and t not in _GENERIC_ANSWER for t in small)


def group_key():
    """One value per question as the student sees it: rows of one recall group (the same exam question
    recalled by two archives, scripts/link_recalls.py) share it."""
    return func.coalesce(MCQ.recall_group, -MCQ.id)


def one_per_recall_group(db: Session, query):
    """Keep one row (the lowest id in the selection) of each recall group."""
    sub = query.with_entities(MCQ.id.label("id"), MCQ.recall_group.label("g")).order_by(None).subquery()
    reps = db.query(func.min(sub.c.id)).filter(sub.c.g.isnot(None)).group_by(sub.c.g)
    return query.filter(or_(MCQ.recall_group.is_(None), MCQ.id.in_(reps)))


def with_recall_groups(db: Session, ids_query):
    """ids_query plus every row that shares a recall group with one of them (answering one version of a
    question counts as having seen the others)."""
    groups = db.query(MCQ.recall_group).filter(MCQ.id.in_(ids_query), MCQ.recall_group.isnot(None))
    return db.query(MCQ.id).filter(MCQ.recall_group.in_(groups)).union(ids_query)


def distinct_by_group(mcqs: list[MCQ], n: int) -> list[MCQ]:
    """First n questions of an ordered list, skipping a second version of an already-picked question."""
    out, taken = [], set()
    for m in mcqs:
        key = m.recall_group if m.recall_group is not None else -m.id
        if key in taken:
            continue
        taken.add(key)
        out.append(m)
        if len(out) >= n:
            break
    return out


def past_paper_filter(db: Session, query, exam: str | None, years: list[int] | None,
                      tags: dict[str, list[str]] | None):
    """Restrict an MCQ query to questions of an exam's past papers (optionally some years or sittings),
    carrying at least one of the chosen labels on every axis given."""
    papers = db.query(PastPaperQuestion.mcq_id).join(PastPaper, PastPaper.id == PastPaperQuestion.paper_id)
    if exam:
        papers = papers.filter(PastPaper.exam == exam)
    if years:
        papers = papers.filter(PastPaper.year.in_([int(y) for y in years]))
    paper_ids = [int(p) for p in (tags or {}).get("paper") or [] if str(p).isdigit()]
    if paper_ids:
        papers = papers.filter(PastPaper.id.in_(paper_ids))
    query = query.filter(MCQ.id.in_(papers))
    for axis, labels in (tags or {}).items():
        if not labels:
            continue
        if axis == "subject":
            query = query.filter(MCQ.sub_category.in_(labels))
        elif axis == "source":
            conds = [MCQ.source.in_([SOURCES[lab] for lab in labels if lab in SOURCES])]
            if BANK in labels:
                conds.append(or_(MCQ.source.is_(None), MCQ.source.notin_(list(SOURCES.values()))))
            query = query.filter(or_(*conds))
        elif axis in TAG_AXES:
            query = query.filter(MCQ.id.in_(
                db.query(MCQTag.mcq_id).filter(MCQTag.axis == axis, MCQTag.label.in_(labels))))
    return query


def overview(db: Session, user: User) -> dict[str, Any]:
    """Exams -> years -> sittings, with question counts and this user's progress.

    Counts are of questions as the student sees them (recall groups), so a question recalled by both
    archives, or sitting in several papers, counts once at each level. A year of None holds collections
    (undated pools such as 'FCPS Old Pool')."""
    rows = db.execute(text(
        "WITH ag AS (SELECT coalesce(m.recall_group, -m.id) AS k, bool_or(e.is_correct) AS ok "
        "            FROM answer_events e JOIN mcqs m ON m.id = e.mcq_id WHERE e.user_id = :u GROUP BY 1) "
        "SELECT p.exam, p.year, p.id, max(p.title), max(p.source), "
        "  count(DISTINCT coalesce(m.recall_group, -m.id)) AS total, "
        "  count(DISTINCT ag.k) AS answered, count(DISTINCT ag.k) FILTER (WHERE ag.ok) AS correct, "
        "  GROUPING(p.year) AS gy, GROUPING(p.id) AS gi "
        "FROM past_papers p JOIN past_paper_questions q ON q.paper_id = p.id JOIN mcqs m ON m.id = q.mcq_id "
        "LEFT JOIN ag ON ag.k = coalesce(m.recall_group, -m.id) "
        "GROUP BY GROUPING SETS ((p.exam, p.year, p.id), (p.exam, p.year), (p.exam)) "
        "ORDER BY p.exam, p.year DESC NULLS LAST, max(p.title)"), {"u": user.id}).all()
    exams: dict[str, dict[str, Any]] = {}
    years: dict[tuple[str, int | None], dict[str, Any]] = {}
    for exam, year, pid, title, source, total, answered, correct, gy, gi in rows:
        stats = {"total": int(total), "answered": int(answered), "correct": int(correct)}
        e = exams.setdefault(exam, {"exam": exam, "years": [], "papers": []})
        if gy:          # exam total
            e.update(stats)
            continue
        y = years.get((exam, year))
        if y is None:
            y = years[(exam, year)] = {"year": year, "papers": []}
            e["years"].append(y)
        if gi:          # year total
            y.update(stats)
            continue
        paper = {"id": pid, "year": year, "title": title, "source": SOURCE_LABEL.get(source, source), **stats}
        y["papers"].append(paper)
        e["papers"].append(paper)
    return {"exams": list(exams.values())}


def scope(db: Session, user: User, exam: str | None, years: list[int] | None,
          tags: dict[str, list[str]] | None) -> dict[str, Any]:
    """How many questions match, the user's progress on them, and label counts for the filters.
    Each axis's counts ignore that axis's own selection (so choices can be widened)."""
    def base(tags_):
        return past_paper_filter(db, db.query(MCQ.id).filter(MCQ.status == "ready"), exam, years, tags_)

    # Counted per question as the student sees it: both archives' versions of one question count once.
    matching = base(tags).with_entities(MCQ.id, group_key().label("k")).subquery()
    total = db.query(func.count(func.distinct(matching.c.k))).scalar() or 0
    # Per question: answered at all (any version, also one outside this scope, e.g. the other archive's
    # wording), and ever answered correctly ("missed" = answered, never right).
    member = aliased(MCQ)
    direct = (db.query(matching.c.k.label("k"), AnswerEvent.is_correct.label("ok"))
              .join(AnswerEvent, AnswerEvent.mcq_id == matching.c.id).filter(AnswerEvent.user_id == user.id))
    via_group = (db.query(matching.c.k.label("k"), AnswerEvent.is_correct.label("ok"))
                 .join(member, member.recall_group == matching.c.k)
                 .join(AnswerEvent, AnswerEvent.mcq_id == member.id).filter(AnswerEvent.user_id == user.id))
    events = direct.union_all(via_group).subquery()
    per_q = db.query(events.c.k, func.bool_or(events.c.ok).label("ok")).group_by(events.c.k).subquery()
    answered = db.query(func.count()).select_from(per_q).scalar()
    missed = db.query(func.count()).select_from(per_q).filter(~per_q.c.ok).scalar()
    facets: dict[str, list[dict[str, Any]]] = {}
    for axis in AXES:
        others = {a: v for a, v in (tags or {}).items() if a != axis}
        ids = base(others).subquery()
        if axis in ("subject", "source"):
            col = MCQ.sub_category if axis == "subject" else MCQ.source
            counts = (db.query(col, func.count(MCQ.id)).filter(MCQ.id.in_(db.query(ids.c.id)), col.isnot(None))
                      .group_by(col).order_by(func.count(MCQ.id).desc()).all())
            if axis == "source":   # rows an archive linked instead of copying are the bank's own
                merged: dict[str, int] = {}
                for s, n in counts:
                    merged[source_label(s)] = merged.get(source_label(s), 0) + int(n)
                counts = sorted(merged.items(), key=lambda kv: -kv[1])
        else:
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
    # One version of each question (a recall group), then round-robin by subject like the real paper.
    rows = query.with_entities(MCQ.id, MCQ.recall_group, MCQ.sub_category).order_by(func.random()).limit(6000).all()
    groups: dict[str, list[int]] = {}
    taken: set[int] = set()
    for i, g, subject in rows:
        key = g if g is not None else -i
        if key in taken:
            continue
        taken.add(key)
        groups.setdefault(subject or "Other", []).append(i)
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


def recall_info(db: Session, mcqs: list[MCQ]) -> dict[int, dict[str, Any]]:
    """Per question for quiz payloads: its archive, how many other archives' versions it has, and
    whether their keys disagree (flag=key-conflict, scripts/link_recalls.py)."""
    groups = {m.recall_group for m in mcqs if m.recall_group is not None}
    sizes = dict(db.query(MCQ.recall_group, func.count()).filter(MCQ.recall_group.in_(groups))
                 .group_by(MCQ.recall_group).all()) if groups else {}
    conflict = {i for (i,) in db.query(MCQTag.mcq_id).filter(
        MCQTag.mcq_id.in_([m.id for m in mcqs] or [-1]), MCQTag.axis == "flag", MCQTag.label == "key-conflict")}
    out: dict[int, dict[str, Any]] = {}
    for m in mcqs:
        info: dict[str, Any] = {}
        if m.source in SOURCE_LABEL:
            info["archive"] = SOURCE_LABEL[m.source]
        if m.recall_group is not None and sizes.get(m.recall_group, 1) > 1:
            info["recall_versions"] = sizes[m.recall_group] - 1
        if m.id in conflict:
            info["key_conflict"] = True
        if info:
            out[m.id] = info
    return out


def recall_versions(db: Session, mcq: MCQ) -> list[dict[str, Any]]:
    """The other archives' wording of the same recalled question, each with its own key."""
    if mcq.recall_group is None:
        return []
    others = (db.query(MCQ).filter(MCQ.recall_group == mcq.recall_group, MCQ.id != mcq.id)
              .order_by(MCQ.id).all())
    years = paper_years(db, [o.id for o in others])
    mine = answer_norm((mcq.options or {}).get(mcq.correct_option))
    out = []
    for o in others:
        answer = str((o.options or {}).get(o.correct_option, ""))
        out.append({
            "id": o.id, "archive": SOURCE_LABEL.get(o.source, o.source), "question_text": o.question_text,
            "options": o.options, "correct_option": o.correct_option, "answer": answer,
            "same_key": answers_agree(answer_norm(answer), mine),
            "explanation_markdown": o.explanation_markdown, "paper_years": years.get(o.id, []),
        })
    return out


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
