"""FSRS (Free Spaced Repetition Scheduler, version 4.5 with its published default weights): the scheduler Anki
uses. Each concept keeps a stability S (days until recall drops to 90%) and a difficulty D (1-10); every review
updates both from how the student answered, and the next re-test is due when recall is predicted to fall to the
target. Compared with fixed 1/3/7/16/35/60-day steps it spaces easy concepts further apart and brings shaky ones
back sooner (FSRS reports 20-30% fewer reviews for the same retention).

Ratings from a medNAMA answer: wrong = Again (1), right but guessed or unsure = Hard (2), right and sure = Good (3).
"""

import math

W = [0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031, 1.6474, 0.1367, 1.0461, 2.1072, 0.0793,
     0.3246, 1.587, 0.2272, 2.8755]
DECAY = -0.5
FACTOR = 19 / 81             # so that retrievability is 0.9 when elapsed time == stability
TARGET_RETENTION = 0.9
MAX_INTERVAL_DAYS = 365
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4


def rating(is_correct: bool, confidence: str) -> int:
    if not is_correct:
        return AGAIN
    return GOOD if confidence == "sure" else HARD


def retrievability(elapsed_days: float, stability: float) -> float:
    return (1 + FACTOR * max(0.0, elapsed_days) / max(stability, 0.01)) ** DECAY


def interval_days(stability: float, retention: float = TARGET_RETENTION) -> int:
    days = stability / FACTOR * (retention ** (1 / DECAY) - 1)
    return int(min(MAX_INTERVAL_DAYS, max(1, round(days))))


def _clamp_d(d: float) -> float:
    return min(10.0, max(1.0, d))


def initial(g: int) -> tuple[float, float]:
    """(stability, difficulty) after the first review."""
    return W[g - 1], _clamp_d(W[4] - (g - 3) * W[5])


def review(stability: float, difficulty: float, elapsed_days: float, g: int) -> tuple[float, float]:
    """(stability, difficulty) after a review rated g, elapsed_days after the previous one."""
    r = retrievability(elapsed_days, stability)
    d0_good = W[4]                                   # initial difficulty of a "Good" first answer
    d = difficulty - W[6] * (g - 3)
    d = _clamp_d(W[7] * d0_good + (1 - W[7]) * d)    # mean reversion
    if g == AGAIN:
        s = W[11] * difficulty ** (-W[12]) * ((stability + 1) ** W[13] - 1) * math.exp(W[14] * (1 - r))
        s = min(s, stability)
    else:
        hard = W[15] if g == HARD else 1.0
        easy = W[16] if g == EASY else 1.0
        s = stability * (math.exp(W[8]) * (11 - difficulty) * stability ** (-W[9])
                         * (math.exp(W[10] * (1 - r)) - 1) * hard * easy + 1)
    return max(0.1, s), d
