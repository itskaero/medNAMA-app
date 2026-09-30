"""Stats: every answer, from every feature, in one place.

Each answer (answer_events) belongs to a feature, derived from where it was recorded and what was answered:
Daily Dose, Final sprint, Challenge, Weekly mock, Timed past papers, Re-tests, Past papers, Twists,
Look-alikes, Practice. It also belongs to a session (session_ref: a quiz attempt, a day's dose, a mock paper,
a duel, or a day of ad-hoc practice). Answers recorded before session_ref existed fall back to one session
per source and day.

Only answers to questions count (mcq_id set); graded "explain it" answers are concept-level and live in the
review schedule instead.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import AnswerEvent, Book, MCQ, User
from app.retention import (NON_FCPS_CATEGORIES, NOT_A_SUBJECT, PART1_SUBJECTS, PASS_LINE,
                           _now, access_scope, streak, subject_for)

# The mock join uses CASE, not AND: Postgres may evaluate either side first, and a practice session_ref
# ('practice:<date>') is not an int.
EVENTS = """
WITH ev AS (
  SELECT e.id, e.is_correct, e.created_at, e.subject, e.mcq_id,
         coalesce(e.session_ref, e.source || ':' || to_char(e.created_at::date, 'YYYY-MM-DD')) AS sref,
         CASE
           WHEN e.source = 'dose' THEN 'Daily Dose'
           WHEN e.source = 'sprint' THEN 'Final sprint'
           WHEN e.source = 'duel' THEN 'Challenge'
           WHEN e.source = 'offline' THEN 'Offline pack'
           WHEN e.source = 'mock' THEN CASE WHEN wm.part = 'pp' THEN 'Timed past papers' ELSE 'Weekly mock' END
           WHEN e.source = 'retest' OR m.main_category = 'Concept re-test' THEN 'Re-tests'
           WHEN m.main_category LIKE 'Past papers%' THEN 'Past papers'
           WHEN m.main_category = 'Past-paper twists' THEN 'Twists'
           WHEN m.main_category = 'Look-alikes' THEN 'Look-alikes'
           ELSE 'Practice'
         END AS feature
  FROM answer_events e
  LEFT JOIN mcqs m ON m.id = e.mcq_id
  LEFT JOIN weekly_mocks wm ON wm.id = CASE WHEN e.session_ref LIKE 'mock:%' THEN split_part(e.session_ref, ':', 2)::int END
  WHERE e.user_id = :u AND e.mcq_id IS NOT NULL
)
"""

SESSION_NAMES = {"dose": "Daily Dose", "sprint": "Final sprint", "duel": "Challenge a friend", "offline": "Offline pack"}


def _pct(correct: int, n: int) -> float | None:
    return round(100.0 * correct / n, 1) if n else None


def overview(db: Session, user: User, sessions_offset: int = 0, sessions_limit: int = 20) -> dict[str, Any]:
    u = {"u": user.id}
    week_ago = _now() - timedelta(days=7)
    n, correct, days, n7, c7 = db.execute(text(EVENTS + """
        SELECT count(*), count(*) FILTER (WHERE is_correct), count(DISTINCT created_at::date),
               count(*) FILTER (WHERE created_at >= :w), count(*) FILTER (WHERE is_correct AND created_at >= :w)
        FROM ev"""), {**u, "w": week_ago}).one()

    features = [{"feature": f, "answered": int(a), "accuracy": _pct(int(c), int(a)), "last_at": last.isoformat()}
                for f, a, c, last in db.execute(text(EVENTS + """
        SELECT feature, count(*), count(*) FILTER (WHERE is_correct), max(created_at) FROM ev
        GROUP BY feature ORDER BY count(*) DESC"""), u)]

    weeks = [{"week_start": w.date().isoformat(), "answered": int(a), "accuracy": _pct(int(c), int(a))}
             for w, a, c in db.execute(text(EVENTS + """
        SELECT date_trunc('week', created_at), count(*), count(*) FILTER (WHERE is_correct) FROM ev
        WHERE created_at >= date_trunc('week', now()) - interval '11 weeks'
        GROUP BY 1 ORDER BY 1"""), u)]

    subjects = [{"subject": s, "answered": int(a), "accuracy": _pct(int(c), int(a))}
                for s, a, c in db.execute(text(EVENTS + """
        SELECT subject, count(*), count(*) FILTER (WHERE is_correct) FROM ev
        WHERE subject IS NOT NULL GROUP BY subject ORDER BY count(*) DESC"""), u)
                if s not in NOT_A_SUBJECT and s != "Other"]
    weakest = sorted((s for s in subjects if s["answered"] >= 10), key=lambda s: s["accuracy"] or 0)[:3]

    total_sessions = db.execute(text(EVENTS + "SELECT count(DISTINCT sref) FROM ev"), u).scalar() or 0
    rows = db.execute(text(EVENTS + """
        SELECT sref, mode() WITHIN GROUP (ORDER BY feature), count(*), count(*) FILTER (WHERE is_correct),
               min(created_at), max(created_at)
        FROM ev GROUP BY sref ORDER BY max(created_at) DESC OFFSET :o LIMIT :l"""),
        {**u, "o": max(0, sessions_offset), "l": max(1, min(100, sessions_limit))}).all()
    # Older answers have no session_ref; their fallback key is '<source>:<date>', which is not a row id.
    def ref_id(sref: str, kind: str) -> int | None:
        k, _, ref = sref.partition(":")
        return int(ref) if k == kind and ref.isdigit() else None

    quiz_ids = [i for i in (ref_id(r[0], "quiz") for r in rows) if i is not None]
    mock_ids = [i for i in (ref_id(r[0], "mock") for r in rows) if i is not None]
    quiz_labels = dict(db.execute(text("SELECT id, label FROM quiz_attempts WHERE id = ANY(:i)"),
                                  {"i": quiz_ids or [-1]}).all())
    mock_titles = dict(db.execute(text("SELECT id, title FROM weekly_mocks WHERE id = ANY(:i)"),
                                  {"i": mock_ids or [-1]}).all())
    sessions = []
    for sref, feature, a, c, started, ended in rows:
        kind = sref.partition(":")[0]
        quiz_id, mock_id = ref_id(sref, "quiz"), ref_id(sref, "mock")
        if quiz_id is not None:
            label = quiz_labels.get(quiz_id) or feature
        elif mock_id is not None:
            label = mock_titles.get(mock_id) or feature
        else:
            label = SESSION_NAMES.get(kind) or feature
        sessions.append({
            "ref": sref, "feature": feature, "label": label, "answered": int(a), "correct": int(c),
            "accuracy": _pct(int(c), int(a)), "started_at": started.isoformat(), "ended_at": ended.isoformat(),
            "review": ({"quiz_attempt_id": quiz_id} if quiz_id is not None
                       else {"mock_id": mock_id} if mock_id is not None else None),
        })

    return {
        "totals": {"answered": int(n), "correct": int(correct), "accuracy": _pct(int(correct), int(n)),
                   "active_days": int(days), "last7_answered": int(n7), "last7_accuracy": _pct(int(c7), int(n7)),
                   "streak": streak(db, user.id), "pass_line": round(PASS_LINE * 100)},
        "features": features,
        "weeks": weeks,
        "subjects": subjects,
        "weakest": weakest,
        "sessions": {"total": int(total_sessions), "offset": sessions_offset, "items": sessions},
    }


def accuracy(db: Session, user: User) -> float | None:
    """All-time accuracy over every answered question (the dashboard's Avg Accuracy)."""
    n, c = db.execute(text("SELECT count(*), count(*) FILTER (WHERE is_correct) FROM answer_events "
                           "WHERE user_id = :u AND mcq_id IS NOT NULL"), {"u": user.id}).one()
    return _pct(int(c), int(n))


# ─── Mastery map (topic-level coverage and accuracy) ───────────────────────

# Question categories the map is *not* about: their topics are concept/book titles
# (the row that says "Guytong Hall Physiology" under a subject is noise, not a topic).
BANK_EXCLUDED_CATEGORIES = ("Concept re-test", "High-yield", "AI MCQs", "Look-alikes",
                            "Past-paper twists", "Spot the diagnosis")
# topic labels that are archive buckets rather than a real topic. PART1_SUBJECTS are
# the subject *axis* (a Book/Paper-1 label reused as a topic in some imported sets).
NOISE_TOPIC_KEYS = {"mixed", "golden questions", "dentistry", "minor subjects", "general",
                    "not specified", "unspecified", "n/a", "none", "miscellaneous", "misc", "all"}
NOISE_TOPIC_KEYS |= {s.lower() for s in PART1_SUBJECTS}

EVENT_TOPIC_SQL = """
SELECT e.subject AS subject, m.topic AS topic, count(*) AS n,
       count(*) FILTER (WHERE e.is_correct) AS correct
FROM answer_events e JOIN mcqs m ON m.id = e.mcq_id
WHERE e.user_id = :u AND e.subject IS NOT NULL
  AND m.topic IS NOT NULL AND trim(m.topic) != ''
GROUP BY 1, 2"""


def _bank_scope() -> tuple:
    """SQLAlchemy conditions for the questions the map counts: ready, real practice sets,
    subject buckets that say nothing about the subject dropped."""
    return (
        MCQ.status == "ready",
        func.coalesce(MCQ.main_category, "").notin_(list(NON_FCPS_CATEGORIES + BANK_EXCLUDED_CATEGORIES)),
        func.coalesce(MCQ.sub_category, "").notin_(list(NOT_A_SUBJECT)),
    )


def mastery(db: Session, user: User) -> dict[str, Any]:
    """Topic-level coverage of the practice bank: per subject, per topic, how many
    questions exist, how many the student has answered and the accuracy on them.

    Everything else (subject readiness, predicted score) stops at the subject; this
    exists to show *which topics inside a subject* are weak or still untouched."""
    books = dict(db.query(Book.id, Book.title).all())

    # Bank: ready questions of real practice sets this student can open, bucketed by derived subject -> topic.
    # Counted in SQL per (book, category, sub-category, topic): a few thousand groups, not ~74k rows.
    bank: dict[str, dict[str, Any]] = {}
    groups = access_scope(db.query(MCQ.book_id, MCQ.main_category, MCQ.sub_category, MCQ.topic, func.count(MCQ.id))
                          .filter(*_bank_scope()), user)         .group_by(MCQ.book_id, MCQ.main_category, MCQ.sub_category, MCQ.topic)
    for book_id, main, sub, topic, count in groups:
        subject = subject_for(books.get(book_id), main, sub)
        if not subject:
            continue
        t = (topic or "").strip()
        if not t:
            continue
        s = bank.setdefault(subject, {"bank": 0, "topics": {}})
        s["bank"] += int(count)
        s["topics"][t] = s["topics"].get(t, 0) + int(count)

    # Answers: use the subject recorded at answer time (matches subject_for) and the
    # question's current topic, so bank and answered rows line up by (subject, topic).
    events: dict[tuple[str, str], tuple[int, int]] = {}
    for subject, topic, n, correct in db.execute(text(EVENT_TOPIC_SQL), {"u": user.id}):
        events[(subject, topic)] = (int(n), int(correct))

    subjects = []
    focus: list[dict[str, Any]] = []
    for subject, s in bank.items():
        topics: list[dict[str, Any]] = []
        for topic, count in s["topics"].items():
            if topic.lower() in NOISE_TOPIC_KEYS or topic.lower() == subject.lower():
                continue
            answered, correct = events.get((subject, topic), (0, 0))
            topics.append({
                "topic": topic,
                "bank": int(count),
                "answered": answered,
                "correct": correct,
                "accuracy": _pct(correct, answered),
            })
        topics.sort(key=lambda t: (
            0 if 0 < t["answered"] < 4 else 1 if t["answered"] >= 4 and (t["accuracy"] or 0) < PASS_LINE * 100
            else 2 if t["answered"] == 0 else 3,
            -(t["answered"] or 0), -(t["bank"] or 0)))
        answered = sum(t["answered"] for t in topics)
        correct = sum(t["correct"] for t in topics)   # exact counts, not rebuilt from rounded percentages
        subjects.append({
            "subject": subject,
            "bank": s["bank"],
            "answered": answered,
            "accuracy": _pct(correct, answered),
            "coverage": round(100.0 * answered / s["bank"], 1) if s["bank"] else None,
            "topics": topics,
        })
        for t in topics:
            if t["answered"] >= 4 and (t["accuracy"] or 0) < PASS_LINE * 100:
                focus.append({"subject": subject, "topic": t["topic"], "answered": t["answered"],
                              "accuracy": t["accuracy"]})

    # How much of the whole bank (across every subject) has been touched.
    bank_total = sum(s["bank"] for s in bank.values())
    bank_answered = (access_scope(db.query(func.count(func.distinct(MCQ.id)))
                                  .join(AnswerEvent, AnswerEvent.mcq_id == MCQ.id)
                                  .filter(AnswerEvent.user_id == user.id, *_bank_scope()), user).scalar() or 0)

    def subject_risk(s: dict[str, Any]) -> int:
        """0 = studied and needs attention, 1 = studied and healthy, 2 = not touched yet."""
        if s["answered"] == 0:
            return 2
        weak = any(0 < t["answered"] < 4 or (t["answered"] >= 4 and (t["accuracy"] or 0) < PASS_LINE * 100)
                   for t in s["topics"])
        return 0 if weak else 1

    subjects.sort(key=lambda s: (subject_risk(s), -s["answered"]))
    focus.sort(key=lambda f: (f["accuracy"] or 0))

    return {
        "bank": bank_total,
        "answered": int(bank_answered),
        "coverage": _pct(int(bank_answered), bank_total) if bank_total else None,
        "pass_line": round(PASS_LINE * 100),
        "subjects": subjects,
        "focus": focus[:12],
        "note": "Topics come from the question bank; some older questions have no topic and fall out of the map.",
    }
