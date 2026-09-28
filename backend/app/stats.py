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

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import User
from app.retention import NOT_A_SUBJECT, PASS_LINE, _now, streak

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

SESSION_NAMES = {"dose": "Daily Dose", "sprint": "Final sprint", "duel": "Challenge a friend"}


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
