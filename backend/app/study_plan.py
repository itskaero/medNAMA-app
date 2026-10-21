"""Today's topics, weighted by how often the exam asks them.

Competitors pick "weak topics" by accuracy alone. Here a topic's priority is weakness x exam yield: the share of
FCPS Part 1 past-paper questions on that subject/topic (the shared fcps_subject / fcps_topic tags, the same ones the
Custom exam filters on; recalled copies of one question across archives count once via recall_group).

  weak       answered >= MIN_ANSWERED in the last 90 days, accuracy under the pass line; priority (1 - acc) * yield
  untouched  answered fewer than MIN_ANSWERED times, highest yield first: where to start, so a new student gets a plan too

Paper 1 (basic sciences) only: Paper 2 questions carry no topic tags yet.
"""

import threading
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.practice_scope import PAST

PASS_LINE = 0.75
MIN_ANSWERED = 4
_YIELD_TTL_S = 3600
_yield_cache: dict[str, Any] = {"at": 0.0, "data": None}
_yield_lock = threading.Lock()

YIELD_SQL = """
SELECT ts.label, tt.label, count(DISTINCT coalesce('g' || m.recall_group::text, 'm' || m.id::text))
FROM mcqs m
JOIN mcq_tags ts ON ts.mcq_id = m.id AND ts.axis = 'fcps_subject'
JOIN mcq_tags tt ON tt.mcq_id = m.id AND tt.axis = 'fcps_topic'
WHERE m.main_category = :past AND m.twist_of IS NULL
GROUP BY 1, 2
"""

USER_SQL = """
SELECT ts.label, tt.label, count(*), sum(CASE WHEN e.is_correct THEN 1 ELSE 0 END)
FROM answer_events e
JOIN mcq_tags ts ON ts.mcq_id = e.mcq_id AND ts.axis = 'fcps_subject'
JOIN mcq_tags tt ON tt.mcq_id = e.mcq_id AND tt.axis = 'fcps_topic'
WHERE e.user_id = :u AND e.mcq_id IS NOT NULL AND e.created_at > now() - interval '90 days'
GROUP BY 1, 2
"""


def exam_yield(db: Session) -> dict[tuple[str, str], int]:
    """(subject, topic) -> distinct past-paper questions; the same for every student, so cached for an hour."""
    with _yield_lock:
        if _yield_cache["data"] is None or time.monotonic() - _yield_cache["at"] > _YIELD_TTL_S:
            _yield_cache["data"] = {(s, t): int(n) for s, t, n in db.execute(text(YIELD_SQL), {"past": PAST})}
            _yield_cache["at"] = time.monotonic()
        return _yield_cache["data"]


def _item(kind: str, subject: str, topic: str, asked: int, answered: int = 0, accuracy: float | None = None) -> dict:
    return {"kind": kind, "subject": subject, "topic": topic, "past_questions": asked, "answered": answered,
            "accuracy": None if accuracy is None else round(accuracy * 100),
            "scope": {"paper": "1", "sources": ["bank", "past"], "subjects": [subject], "topics": [f"{subject}|{topic}"],
                      "years": []}}


def focus_topics(db: Session, user_id: int, limit: int = 3) -> list[dict]:
    yields = exam_yield(db)
    if not yields:
        return []
    mine = {(s, t): (int(n), int(c or 0)) for s, t, n, c in db.execute(text(USER_SQL), {"u": user_id})}

    weak = []
    for (s, t), (n, c) in mine.items():
        acc = c / n if n else 0.0
        if n >= MIN_ANSWERED and acc < PASS_LINE:
            asked = yields.get((s, t), 0)
            weak.append(((1 - acc) * (asked + 1), _item("weak", s, t, asked, n, acc)))
    weak.sort(key=lambda x: -x[0])
    out = [w[1] for w in weak[:limit]]

    if len(out) < limit:
        # Not started, or too few answers to judge yet: the highest-yield of these is where to go next.
        fresh = sorted(((n, s, t) for (s, t), n in yields.items() if mine.get((s, t), (0, 0))[0] < MIN_ANSWERED),
                       reverse=True)
        out += [_item("untouched", s, t, n, mine.get((s, t), (0, 0))[0]) for n, s, t in fresh[: limit - len(out)]]
    return out
