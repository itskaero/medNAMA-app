"""How hard each question really is, measured from students' answers.

Each student's FIRST answer to a question counts (later answers are re-tests of something already seen and
would make every question look easy). Once MIN_ANSWERS students have answered, the share who got it right sets a
1-5 label; until then the author's `difficulty` (AI target or import) stands, and many questions have neither.

    85%+ right  1 Easy        50-69%  3 Medium        <30%  5 Very hard
    70-84%      2 Fairly easy 30-49%  4 Hard

`refresh()` writes mcqs.answered_n / correct_rate in one UPDATE that touches only rows whose numbers changed; a
background thread runs it shortly after start-up and then every few hours, so serving a question never computes it.
"""

import logging
import threading
import time

from sqlalchemy import case, text
from sqlalchemy.orm import Session

from app.models import MCQ

logger = logging.getLogger(__name__)

MIN_ANSWERS = 8
REFRESH_EVERY_S = 6 * 3600
LABELS = {1: "Easy", 2: "Fairly easy", 3: "Medium", 4: "Hard", 5: "Very hard"}

REFRESH_SQL = """
UPDATE mcqs m SET answered_n = s.n, correct_rate = s.r
FROM (
  SELECT mcq_id, count(*) AS n, avg(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS r
  FROM (SELECT DISTINCT ON (user_id, mcq_id) user_id, mcq_id, is_correct
        FROM answer_events WHERE mcq_id IS NOT NULL
        ORDER BY user_id, mcq_id, created_at) first_tries
  GROUP BY mcq_id
) s
WHERE m.id = s.mcq_id AND (m.answered_n IS DISTINCT FROM s.n OR m.correct_rate IS DISTINCT FROM s.r)
"""


def bucket(rate: float) -> int:
    return 1 if rate >= 0.85 else 2 if rate >= 0.70 else 3 if rate >= 0.50 else 4 if rate >= 0.30 else 5


def effective_difficulty(m: MCQ) -> tuple[int | None, str | None]:
    """(level 1-5, "students" | "author") or (None, None) when nothing is known."""
    if m.answered_n and m.answered_n >= MIN_ANSWERS and m.correct_rate is not None:
        return bucket(m.correct_rate), "students"
    if m.difficulty:
        return m.difficulty, "author"
    return None, None


def difficulty_info(m: MCQ) -> dict:
    """Fields added to a question sent to the player."""
    level, source = effective_difficulty(m)
    measured = source == "students"
    return {"difficulty": level, "difficulty_label": LABELS.get(level) if level else None, "difficulty_source": source,
            "correct_rate": round(m.correct_rate, 3) if measured else None, "answered_n": m.answered_n if measured else None}


def difficulty_sql():
    """The same rule as effective_difficulty(), as a SQL expression for filtering (NULL when unknown)."""
    rate = MCQ.correct_rate
    measured = case(
        (rate >= 0.85, 1), (rate >= 0.70, 2), (rate >= 0.50, 3), (rate >= 0.30, 4), else_=5)
    return case(((MCQ.answered_n >= MIN_ANSWERS) & rate.isnot(None), measured), else_=MCQ.difficulty)


def refresh(db: Session) -> int:
    changed = db.execute(text(REFRESH_SQL)).rowcount or 0
    db.commit()
    return changed


def _loop() -> None:
    from app.database import SessionLocal
    time.sleep(90)   # let start-up (model warm-up) finish first
    while True:
        db = SessionLocal()
        try:
            n = refresh(db)
            logger.info("answer stats: %d questions updated", n)
        except Exception as e:   # a stats refresh must never take the app down
            db.rollback()
            logger.warning("answer stats refresh failed: %s", e)
        finally:
            db.close()
        time.sleep(REFRESH_EVERY_S)


def start_background_refresh() -> None:
    threading.Thread(target=_loop, name="answer-stats", daemon=True).start()
