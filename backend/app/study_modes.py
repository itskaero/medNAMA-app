"""Study modes on top of the retention engine.

Mistake types
  Every wrong answer is tagged when it is recorded:
    confusion      the student picked a look-alike of the right answer (the two
                   options embed close together and differ in more than numbers)
    misconception  wrong while tapping "Sure": a confidently held wrong idea
    gap            any other wrong answer: the fact simply isn't known yet
  A 'confusion' is checked by the LLM in the background (are these really two
  distinct, confusable concepts?) and downgraded to misconception/gap if not.

Confusable pairs
  A confirmed confusion links the answer to a ConfusablePair (shared by all
  students who mix up the same two concepts): a side-by-side comparison with a
  one-line discriminator, verbatim-checked textbook quotes, and two MCQs, one
  answered by each concept. A pair is "cleared" for a student once they answer
  both of its questions correctly after their latest mix-up.

Final sprint
  In the last SPRINT_DAYS before the exam date: the student's weakest concept
  cards (most lapses, lowest box) each followed by a rapid re-test, their open
  confusable pairs, and high-yield questions where enabled.

Weekly mock
  One fixed paper per ISO week (Monday start): up to 100 single-best-answer
  questions, 2 hours, CPSP-style with no negative marking, balanced across
  subjects. One sitting per student; the result shows score against the 75%
  line, rank and percentile among that week's candidates, and a per-subject
  breakdown. Answers feed the retention engine (flagged = unsure).
"""

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.llm import chat_completion, llm_configured
from app.models import (
    MCQ, AnswerEvent, Book, ConceptCard, ConceptReview, ConfusablePair, StudySession, User, WeeklyMock,
    WeeklyMockEntry,
)
from app.retention import (
    PASS_LINE, _embed, _jobs, _norm_ws, _now, _variant_mcq, fcps_only, high_yield_enabled, open_only,
    restricted_allowed, subject_for,
)

logger = logging.getLogger(__name__)

LOOKALIKE_COSINE = 0.82        # calibrated on bank option pairs (>= .85 near-all real look-alikes)
MISTAKE_WINDOW_DAYS = 60
SPRINT_DAYS = 7
SPRINT_FLASH = 10
MOCK_SIZE = 100
MOCK_MINUTES = 120
MOCK_GRACE = timedelta(minutes=2)
MOCK_MIN_CANDIDATES_FOR_PERCENTILE = 3

_GENERIC_OPTION = re.compile(r"^\s*(all|none|both|neither)\b", re.I)


# ─── mistake types ──────────────────────────────────────────────────────────

def _words_without_numbers(s: str) -> list[str]:
    return re.sub(r"[\d.,%/:+\-−×]+", " ", s.lower()).split()


def looks_alike(a: str, b: str) -> bool:
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b or _GENERIC_OPTION.match(a) or _GENERIC_OPTION.match(b):
        return False
    if _words_without_numbers(a) == _words_without_numbers(b):
        return False   # differ only in numbers (doses, values): a recall slip, not a concept mix-up
    va, vb = _embed([a, b])
    return float(va @ vb) >= LOOKALIKE_COSINE


def classify_mistake(mcq: MCQ, selected: str, is_correct: bool, confidence: str) -> str | None:
    if is_correct:
        return None
    options = mcq.options or {}
    try:
        if looks_alike(str(options.get((selected or "").upper(), "")), str(options.get(mcq.correct_option, ""))):
            return "confusion"
    except Exception:
        logger.exception("Look-alike check failed for MCQ %s", mcq.id)
    return "misconception" if confidence == "sure" else "gap"


def queue_pair_for_event(event_id: int) -> None:
    _jobs.submit(_resolve_pair, event_id)


def _norm_term(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9+\- ]", " ", (s or "").lower()).split())


def _candidate_pairs(db: Session, text_: str, limit: int = 15) -> list[ConfusablePair]:
    """Existing pairs sharing a distinctive word with the two options (for the naming step to reuse)."""
    words = sorted({w for w in _norm_term(text_).split() if len(w) > 4}, key=len, reverse=True)[:6]
    if not words:
        return []
    return db.query(ConfusablePair).filter(
        func.lower(ConfusablePair.term_a + " " + ConfusablePair.term_b).op("~")("|".join(re.escape(w) for w in words))
    ).order_by(ConfusablePair.id).limit(limit).all()


def _resolve_pair(event_id: int) -> None:
    """Background: confirm a confusion, name the two concepts, link (or build) their pair."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        ev = db.get(AnswerEvent, event_id)
        mcq = db.get(MCQ, ev.mcq_id) if ev and ev.mcq_id else None
        if mcq is None or not llm_configured("fast"):
            return
        options = mcq.options or {}
        chosen = str(options.get((ev.selected_option or "").upper(), ""))
        right = str(options.get(mcq.correct_option, ""))
        candidates = _candidate_pairs(db, f"{right} {chosen} {mcq.question_text or ''}")
        existing = "\n".join(f"[{c.id}] {c.term_a} vs {c.term_b}" for c in candidates) or "(none)"
        raw = chat_completion(
            [
                {"role": "system", "content": (
                    "A student answered an MCQ wrongly by picking an option that looks like the right one. "
                    "Name the two concepts they confused as textbook terms (2-8 words each) that stand alone "
                    "without the question, including the structure or system they belong to, e.g. "
                    "'Nasojejunal feeding' vs 'Nasogastric feeding', 'Anterior two-thirds of interventricular septum' "
                    "vs 'Posterior one-third of interventricular septum', 'Afferent arteriole' vs 'Efferent arteriole'. "
                    "Use the correct textbook term even when the option wording is imprecise (e.g. the tongue's "
                    "'anterior two-thirds', not 'anterior one-third'). "
                    "If one of the EXISTING PAIRS is the same two concepts (in any wording), reuse it. If the two "
                    "options are NOT two distinct but confusable concepts (they differ only in a number, are "
                    "unrelated, or are two statements about the same thing), return {\"confusable\": false}. "
                    "Otherwise return JSON {\"confusable\": true, \"existing_pair_id\": id or null, "
                    "\"right\": \"term for the correct answer\", \"wrong\": \"term for the chosen answer\"}"
                )},
                {"role": "user", "content": (
                    f"QUESTION: {mcq.question_text}\nCORRECT: {right}\nCHOSEN: {chosen}\n\nEXISTING PAIRS:\n{existing}"
                )},
            ],
            json_mode=True, temperature=0.0, max_tokens=200, label="pair-terms", role="fast",
        )
        out = json.loads(raw)
        term_right, term_wrong = str(out.get("right") or "").strip(), str(out.get("wrong") or "").strip()
        if not out.get("confusable") or not term_right or not term_wrong or _norm_term(term_right) == _norm_term(term_wrong):
            ev.mistake_type = "misconception" if ev.confidence == "sure" else "gap"
            db.commit()
            return
        key = " | ".join(sorted([_norm_term(term_right), _norm_term(term_wrong)]))
        reuse_id = out.get("existing_pair_id")
        pair = next((c for c in candidates if str(c.id) == str(reuse_id)), None) if reuse_id else None
        pair = pair or db.query(ConfusablePair).filter_by(pair_key=key).first()
        if pair is None:
            pair = ConfusablePair(pair_key=key, term_a=term_right, term_b=term_wrong, status="pending")
            db.add(pair)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                pair = db.query(ConfusablePair).filter_by(pair_key=key).first()
        ev.pair_id = pair.id
        db.commit()
        if pair.status == "pending":
            build_pair(db, pair, mcq.question_text or "")
    except Exception:
        logger.exception("Confusable pair resolution failed for answer event %s", event_id)
        db.rollback()
    finally:
        db.close()


def build_pair(db: Session, pair: ConfusablePair, stem: str = "") -> None:
    """Write the comparison card and two telling-apart questions from the textbooks. Commits."""
    from app.retrieval import retrieval_service

    blocks, seen = [], set()
    for q in (f"{pair.term_a} versus {pair.term_b}", pair.term_a, pair.term_b):
        for c in retrieval_service.search(db, q, limit=3).context:
            if c.id not in seen:
                seen.add(c.id)
                blocks.append(c)
    blocks = blocks[:7]
    context = "\n\n".join(
        f"Chunk {i}: [{c.book.title if c.book else 'Textbook'}, Page {c.page_number}]\n{c.content}"
        for i, c in enumerate(blocks, 1)
    ) or "NO TEXTBOOK PASSAGES FOUND."
    try:
        raw = chat_completion(
            [
                {"role": "system", "content": (
                    "You help FCPS candidates tell apart two concepts they confuse. Using the TEXTBOOK PASSAGES "
                    "(and standard knowledge only where they are silent), return JSON:\n"
                    '{"rows": [{"feature": "...", "a": "concept A", "b": "concept B"}], '
                    '"discriminator": "one sentence: the most reliable way to tell them apart in an exam", '
                    '"quotes": [{"chunk": 1, "side": "a", "quote": "ONE sentence copied EXACTLY from a chunk"}], '
                    '"questions": [{"question_text": "...", "options": {"A": "...", "B": "...", "C": "...", "D": "...", '
                    '"E": "..."}, "correct_option": "A", "explanation": "..."}]}\n'
                    "rows: 3-6 of the most exam-relevant differences, short cells. quotes: up to 4, never invented. "
                    "questions: exactly two CPSP-style single-best-answer MCQs; the first's correct answer is concept "
                    "A, the second's is concept B, and each lists the other concept as a distractor. Do not reuse the "
                    "student's missed question."
                )},
                {"role": "user", "content": (
                    f"CONCEPT A: {pair.term_a}\nCONCEPT B: {pair.term_b}\nQUESTION THE STUDENT MISSED: {stem}\n\n"
                    f"TEXTBOOK PASSAGES:\n{context[:9000]}"
                )},
            ],
            json_mode=True, temperature=0.3, max_tokens=2200, label="confusable-pair", role="fast",
        )
        out = json.loads(raw)
    except Exception as e:
        logger.warning("Confusable pair %s generation failed: %s", pair.id, e)
        pair.status = "failed"
        db.commit()
        return

    quotes = []
    for q in out.get("quotes") or []:
        try:
            idx = int(q.get("chunk"))
        except (TypeError, ValueError):
            continue
        quote = str(q.get("quote") or "").strip()
        if 1 <= idx <= len(blocks) and len(quote) >= 12 and _norm_ws(quote) in _norm_ws(blocks[idx - 1].content):
            b = blocks[idx - 1]
            quotes.append({"side": "b" if str(q.get("side")).lower() == "b" else "a", "quote": quote,
                           "chunk_id": b.id, "book_title": b.book.title if b.book else None,
                           "page_number": b.page_number})
    rows = [{"feature": str(r.get("feature") or "").strip(), "a": str(r.get("a") or "").strip(),
             "b": str(r.get("b") or "").strip()}
            for r in (out.get("rows") or []) if isinstance(r, dict) and r.get("feature")][:6]
    grounded = bool(quotes)
    source_line = (f"\n\n**Source**: {quotes[0]['book_title']}, Page {quotes[0]['page_number']}" if grounded
                   else "\n\n*AI clinical knowledge: your textbooks were not quoted for this pair.*")
    mcq_ids = []
    for q in (out.get("questions") or [])[:2]:
        options = {str(k).upper(): str(v).strip() for k, v in (q.get("options") or {}).items() if str(v).strip()}
        correct = str(q.get("correct_option") or "").strip().upper()[:1]
        stem_q = str(q.get("question_text") or "").strip()
        if len(options) < 4 or correct not in options or not stem_q:
            continue
        m = MCQ(
            question_text=stem_q, options=options, correct_option=correct,
            topic=f"{pair.term_a} vs {pair.term_b}", main_category="Look-alikes",
            sub_category=(quotes[0]["book_title"] if grounded else "AI clinical knowledge"),
            explanation_markdown=str(q.get("explanation") or "").strip() + source_line,
            status="ready", tested_concept=f"{pair.term_a} vs {pair.term_b}", grounding="book" if grounded else "ai",
            source_chunk_ids=[x["chunk_id"] for x in quotes] or None, stem_embedding=_embed([stem_q])[0].tolist(),
        )
        db.add(m)
        db.flush()
        mcq_ids.append(m.id)
    pair.card = {"rows": rows, "discriminator": str(out.get("discriminator") or "").strip(), "quotes": quotes}
    pair.mcq_ids = mcq_ids
    pair.grounding = "textbook" if grounded else "ai"
    pair.status = "ready" if rows and mcq_ids else "failed"
    db.commit()


def serialize_pair(pair: ConfusablePair) -> dict[str, Any]:
    return {"id": pair.id, "term_a": pair.term_a, "term_b": pair.term_b, "status": pair.status,
            "grounding": pair.grounding, "card": pair.card or None, "mcq_ids": pair.mcq_ids or []}


def user_pairs(db: Session, user_id: int) -> list[dict[str, Any]]:
    """The student's confusable pairs, open ones first, with their next question to answer."""
    rows = db.execute(text(
        "SELECT pair_id, count(*) AS n, max(created_at) AS last FROM answer_events "
        "WHERE user_id = :u AND pair_id IS NOT NULL GROUP BY pair_id ORDER BY max(created_at) DESC"
    ), {"u": user_id}).all()
    out = []
    for pair_id, n, last in rows:
        pair = db.get(ConfusablePair, pair_id)
        if pair is None:
            continue
        ids = [int(i) for i in (pair.mcq_ids or [])]
        correct_after = set()
        if ids:
            correct_after = {r[0] for r in db.query(AnswerEvent.mcq_id).filter(
                AnswerEvent.user_id == user_id, AnswerEvent.mcq_id.in_(ids), AnswerEvent.is_correct.is_(True),
                AnswerEvent.created_at > last)}
        cleared = pair.status == "ready" and bool(ids) and set(ids) <= correct_after
        out.append({**serialize_pair(pair), "times_confused": int(n), "last_confused_at": last.isoformat(),
                    "cleared": cleared, "next_mcq_id": next((i for i in ids if i not in correct_after), None)})
    out.sort(key=lambda p: (p["cleared"], p["status"] != "ready"))
    return out


def mistake_profile(db: Session, user_id: int) -> dict[str, Any]:
    since = _now() - timedelta(days=MISTAKE_WINDOW_DAYS)
    counts = dict(db.query(AnswerEvent.mistake_type, func.count(AnswerEvent.id)).filter(
        AnswerEvent.user_id == user_id, AnswerEvent.is_correct.is_(False), AnswerEvent.created_at >= since,
        AnswerEvent.mistake_type.isnot(None)).group_by(AnswerEvent.mistake_type).all())
    total = sum(counts.values())
    confident = db.execute(text(
        "SELECT c.id, c.title, count(*) AS n FROM answer_events e JOIN concept_cards c ON c.id = e.concept_id "
        "WHERE e.user_id = :u AND e.mistake_type = 'misconception' AND e.created_at >= :since "
        "GROUP BY c.id, c.title ORDER BY n DESC LIMIT 5"), {"u": user_id, "since": since}).all()
    pairs = user_pairs(db, user_id)
    return {
        "window_days": MISTAKE_WINDOW_DAYS,
        "total_wrong": total,
        "types": {t: {"count": int(counts.get(t, 0)), "share": round(counts.get(t, 0) / total, 3) if total else 0.0}
                  for t in ("confusion", "misconception", "gap")},
        "confident_errors": [{"concept_id": cid, "title": title, "count": int(n)} for cid, title, n in confident],
        "pairs": {"open": sum(1 for p in pairs if not p["cleared"]), "cleared": sum(1 for p in pairs if p["cleared"])},
    }


# ─── final sprint ───────────────────────────────────────────────────────────

def sprint_status(user: User) -> dict[str, Any]:
    days_left = (user.exam_date - date.today()).days if user.exam_date else None
    unlocked = days_left is not None and 0 <= days_left <= SPRINT_DAYS
    return {"unlocked": unlocked, "preview": (not unlocked) and user.role == "admin",
            "days_left": days_left, "unlocks_days_before": SPRINT_DAYS}


def get_or_build_sprint(db: Session, user: User) -> StudySession:
    from app.retention import _high_yield_questions, _new_questions

    today = date.today()
    session = db.query(StudySession).filter_by(user_id=user.id, kind="sprint", day=today).first()
    if session and session.items:
        return session
    items: list[dict[str, Any]] = []
    used: set[int] = set()
    weak = (db.query(ConceptReview).filter(ConceptReview.user_id == user.id)
            .order_by(ConceptReview.lapses.desc(), ConceptReview.box.asc(), ConceptReview.next_due.asc())
            .limit(SPRINT_FLASH).all())
    for review in weak:
        card = db.get(ConceptCard, review.concept_id)
        if card is None:
            continue
        items.append({"type": "flash", "concept_id": card.id, "done": False})
        mcq = _variant_mcq(db, card, used, restricted_allowed(user))
        if mcq is not None:
            items.append({"type": "sprint", "mcq_id": mcq.id, "concept_id": card.id, "done": False})
            used.add(mcq.id)
    for p in user_pairs(db, user.id):
        if p["status"] == "ready" and not p["cleared"] and p["next_mcq_id"] and p["next_mcq_id"] not in used:
            items.append({"type": "pair", "pair_id": p["id"], "mcq_id": p["next_mcq_id"], "done": False})
            used.add(p["next_mcq_id"])
        if sum(1 for i in items if i["type"] == "pair") >= 3:
            break
    if high_yield_enabled(user):
        for mcq, asked in _high_yield_questions(db, used, 5):
            items.append({"type": "new", "mcq_id": mcq.id, "high_yield": True, "times_asked": asked, "done": False})
            used.add(mcq.id)
    if not any(i.get("mcq_id") for i in items):   # no history yet: a rapid mixed set instead
        for mcq in _new_questions(db, user.id, used, 15):
            items.append({"type": "sprint", "mcq_id": mcq.id, "done": False})
    if session is None:
        session = StudySession(user_id=user.id, kind="sprint", day=today, items=items)
        db.add(session)
    else:
        session.items = items
    db.commit()
    return session


# ─── weekly mock ────────────────────────────────────────────────────────────

def week_start(d: date | None = None) -> date:
    d = d or date.today()
    return d - timedelta(days=d.weekday())


PARTS = {"p1": "FCPS Part 1", "p2": "FCPS Part 2"}


def part2_tracks(db: Session) -> list[str]:
    return [t for (t,) in db.query(MCQ.sub_category).filter(
        MCQ.main_category == "FCPS Part 2", MCQ.status == "ready").distinct().order_by(MCQ.sub_category) if t]


def paper_key(db: Session, part: str | None, track: str | None) -> tuple[str, str]:
    """Validate (part, track): Part 1 has no track; a Part 2 track must be a seeded specialty ('' = mixed)."""
    part = part if part in PARTS else "p1"
    track = (track or "").strip() if part == "p2" else ""
    if track and track not in part2_tracks(db):
        track = ""
    return part, track


def _mix_key(part: str, track: str, titles: dict, book_id, main, sub, topic) -> str:
    """What a paper is balanced across: subjects (Part 1), a specialty's topics, or specialties (mixed Part 2)."""
    if part == "p2":
        return (topic or "Mixed") if track else (sub or "Mixed")
    return subject_for(titles.get(book_id), main, sub) or "Mixed"


def get_or_create_weekly_mock(db: Session, part: str = "p1", track: str = "", ws: date | None = None) -> WeeklyMock:
    ws = ws or week_start()
    mock = db.query(WeeklyMock).filter_by(week_start=ws, part=part, track=track).first()
    if mock is not None:
        return mock
    titles = {b.id: b.title for b in db.query(Book.id, Book.title)}
    base = open_only(db.query(MCQ.id, MCQ.book_id, MCQ.main_category, MCQ.sub_category, MCQ.topic).filter(
        MCQ.status == "ready", MCQ.figure_id.is_(None)))
    if part == "p2":
        pool = base.filter(MCQ.main_category == "FCPS Part 2")
        if track:
            pool = pool.filter(MCQ.sub_category == track)
    else:
        # The seeded FCPS Part 1 bank when it can fill a paper, else the FCPS pool minus Part 2.
        part1 = base.filter(MCQ.main_category == "FCPS Part 1")
        pool = part1 if part1.count() >= MOCK_SIZE else fcps_only(base).filter(
            or_(MCQ.main_category.is_(None), MCQ.main_category != "FCPS Part 2"))
    # Deterministic shuffle per week and paper: every candidate sits the same questions.
    rows = pool.order_by(func.md5(func.concat(MCQ.id, ws.isoformat(), part, track))).all()
    groups: dict[str, list[int]] = {}
    for mid, book_id, main, sub, topic in rows:
        groups.setdefault(_mix_key(part, track, titles, book_id, main, sub, topic), []).append(mid)
    ids: list[int] = []   # round-robin across groups: every subject/topic represented, none dominating
    while len(ids) < MOCK_SIZE and any(groups.values()):
        for g in sorted(groups):
            if groups[g] and len(ids) < MOCK_SIZE:
                ids.append(groups[g].pop(0))
    label = PARTS[part] + (f" · {track}" if track else (" · all specialties" if part == "p2" else ""))
    mock = WeeklyMock(week_start=ws, part=part, track=track, title=f"{label} mock · week of {ws:%d %b %Y}",
                      mcq_ids=ids, duration_min=MOCK_MINUTES)
    db.add(mock)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        mock = db.query(WeeklyMock).filter_by(week_start=ws, part=part, track=track).one()
    return mock


def _deadline(mock: WeeklyMock, entry: WeeklyMockEntry) -> datetime:
    return entry.started_at + timedelta(minutes=mock.duration_min)


def _percentile(db: Session, mock: WeeklyMock, entry: WeeklyMockEntry) -> dict[str, Any]:
    scores = [s for (s,) in db.query(WeeklyMockEntry.score).filter(
        WeeklyMockEntry.mock_id == mock.id, WeeklyMockEntry.submitted_at.isnot(None))]
    n = len(scores)
    below = sum(1 for s in scores if s < entry.score)
    ties = sum(1 for s in scores if s == entry.score)
    return {
        "candidates": n,
        "rank": 1 + sum(1 for s in scores if s > entry.score),
        "percentile": round(100 * (below + 0.5 * ties) / n) if n >= MOCK_MIN_CANDIDATES_FOR_PERCENTILE else None,
        "average": round(sum(scores) / n, 1) if n else None,
    }


def mock_overview(db: Session, user: User, part: str = "p1", track: str = "") -> dict[str, Any]:
    part, track = paper_key(db, part, track)
    mock = get_or_create_weekly_mock(db, part, track)
    entry = db.query(WeeklyMockEntry).filter_by(mock_id=mock.id, user_id=user.id).first()
    status = "not_started"
    if entry is not None:
        status = "submitted" if entry.submitted_at else ("expired" if _now() > _deadline(mock, entry) + MOCK_GRACE else "in_progress")
    history = []
    for past_entry, past_mock in (db.query(WeeklyMockEntry, WeeklyMock).join(WeeklyMock, WeeklyMock.id == WeeklyMockEntry.mock_id)
                                  .filter(WeeklyMockEntry.user_id == user.id, WeeklyMockEntry.submitted_at.isnot(None),
                                          WeeklyMock.part.in_(tuple(PARTS)))
                                  .order_by(WeeklyMock.week_start.desc()).limit(8)):
        history.append({"week_start": past_mock.week_start.isoformat(), "title": past_mock.title,
                        "part": past_mock.part, "track": past_mock.track, "score": past_entry.score,
                        "total": past_entry.total, **_percentile(db, past_mock, past_entry)})
    candidates = db.query(func.count(WeeklyMockEntry.id)).filter(
        WeeklyMockEntry.mock_id == mock.id, WeeklyMockEntry.submitted_at.isnot(None)).scalar() or 0
    return {
        "papers": [{"part": "p1", "label": PARTS["p1"], "tracks": []},
                   {"part": "p2", "label": PARTS["p2"], "tracks": part2_tracks(db)}],
        "mock": {"id": mock.id, "part": mock.part, "track": mock.track, "week_start": mock.week_start.isoformat(),
                 "title": mock.title,
                 "total": len(mock.mcq_ids or []), "duration_min": mock.duration_min,
                 "closes_on": (mock.week_start + timedelta(days=6)).isoformat(), "pass_line": PASS_LINE},
        "entry": {"status": status,
                  "started_at": entry.started_at.isoformat() if entry else None,
                  "deadline": _deadline(mock, entry).isoformat() if entry else None,
                  "score": entry.score if entry else None, "total": entry.total if entry else None},
        "candidates": int(candidates),
        "history": history,
    }


def start_mock(db: Session, user: User, part: str = "p1", track: str = "", mock: WeeklyMock | None = None) -> dict[str, Any]:
    mock = mock or get_or_create_weekly_mock(db, *paper_key(db, part, track))
    entry = db.query(WeeklyMockEntry).filter_by(mock_id=mock.id, user_id=user.id).first()
    if entry is not None and entry.submitted_at:
        raise ValueError("already_submitted")
    if entry is None:
        entry = WeeklyMockEntry(mock_id=mock.id, user_id=user.id, started_at=_now(), answers={})
        db.add(entry)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            entry = db.query(WeeklyMockEntry).filter_by(mock_id=mock.id, user_id=user.id).one()
    by_id = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_(mock.mcq_ids or []))}
    from app.past_papers import question_media

    media = question_media(db, list(by_id))
    questions = [{"id": i, "question_text": by_id[i].question_text, "options": by_id[i].options,
                  "media": media.get(i, [])}
                 for i in mock.mcq_ids or [] if i in by_id]
    return {"mock_id": mock.id, "part": mock.part, "title": mock.title, "questions": questions,
            "answers": entry.answers or {}, "started_at": entry.started_at.isoformat(),
            "deadline": _deadline(mock, entry).isoformat(), "server_now": _now().isoformat(),
            "duration_min": mock.duration_min}


def save_mock_progress(db: Session, user: User, answers: dict[str, Any], part: str = "p1", track: str = "",
                       mock: WeeklyMock | None = None) -> None:
    mock = mock or get_or_create_weekly_mock(db, *paper_key(db, part, track))
    entry = db.query(WeeklyMockEntry).filter_by(mock_id=mock.id, user_id=user.id).first()
    if entry is None or entry.submitted_at:
        return
    entry.answers = answers
    db.commit()


def submit_mock(db: Session, user: User, answers: dict[str, Any], part: str = "p1", track: str = "",
                mock: WeeklyMock | None = None) -> dict[str, Any]:
    """Score the sitting once; answers are {mcq_id: {"option": "A", "flagged": bool}} or {mcq_id: "A"}."""
    from app.retention import record_answer

    mock = mock or get_or_create_weekly_mock(db, *paper_key(db, part, track))
    entry = db.query(WeeklyMockEntry).filter_by(mock_id=mock.id, user_id=user.id).first()
    if entry is None:
        raise ValueError("not_started")
    if entry.submitted_at:
        return mock_result(db, user, mock)
    ids = [int(i) for i in mock.mcq_ids or []]
    by_id = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_(ids))}
    clean: dict[str, dict[str, Any]] = {}
    for key, val in (answers or {}).items():
        opt, flagged = (val.get("option"), bool(val.get("flagged"))) if isinstance(val, dict) else (val, False)
        try:
            mid = int(key)
        except (TypeError, ValueError):
            continue
        if mid in by_id and opt:
            clean[str(mid)] = {"option": str(opt).strip().upper()[:1], "flagged": flagged}
    score = sum(1 for k, v in clean.items() if v["option"] == (by_id[int(k)].correct_option or "").upper())
    now = _now()
    entry.answers = clean
    entry.score, entry.total = score, len(ids)
    entry.submitted_at = now
    entry.overtime = now > _deadline(mock, entry) + MOCK_GRACE
    db.commit()
    for k, v in clean.items():   # feed the retention engine (concept cards for misses)
        try:
            record_answer(db, user.id, by_id[int(k)], v["option"], "unsure" if v["flagged"] else "sure", source="mock")
        except Exception:
            logger.exception("Retention logging failed for mock MCQ %s", k)
            db.rollback()
    return mock_result(db, user, mock)


def mock_result(db: Session, user: User, mock: WeeklyMock | None = None, part: str = "p1",
                track: str = "") -> dict[str, Any]:
    mock = mock or get_or_create_weekly_mock(db, *paper_key(db, part, track))
    entry = db.query(WeeklyMockEntry).filter_by(mock_id=mock.id, user_id=user.id).first()
    if entry is None or not entry.submitted_at:
        raise ValueError("not_submitted")
    titles = {b.id: b.title for b in db.query(Book.id, Book.title)}
    by_id = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_(mock.mcq_ids or []))}
    subjects: dict[str, dict[str, int]] = {}
    review = []
    for i in mock.mcq_ids or []:
        m = by_id.get(i)
        if m is None:
            continue
        ans = (entry.answers or {}).get(str(i)) or {}
        chosen = ans.get("option")
        correct = chosen == (m.correct_option or "").upper()
        subject = _mix_key(mock.part, mock.track, titles, m.book_id, m.main_category, m.sub_category, m.topic)
        s = subjects.setdefault(subject, {"correct": 0, "total": 0})
        s["total"] += 1
        s["correct"] += int(correct)
        review.append({"id": m.id, "question_text": m.question_text, "options": m.options,
                       "correct_option": m.correct_option, "selected": chosen, "flagged": bool(ans.get("flagged")),
                       "is_correct": correct, "explanation_markdown": m.explanation_markdown, "subject": subject})
    total = entry.total or len(review) or 1
    return {
        "mock_id": mock.id, "title": mock.title, "part": mock.part, "track": mock.track,
        "week_start": mock.week_start.isoformat(),
        "score": entry.score, "total": entry.total, "fraction": round((entry.score or 0) / total, 3),
        "pass_line": PASS_LINE, "passed": (entry.score or 0) / total >= PASS_LINE, "overtime": entry.overtime,
        "time_taken_min": round((entry.submitted_at - entry.started_at).total_seconds() / 60, 1),
        **_percentile(db, mock, entry),
        "subjects": [{"subject": k, **v} for k, v in sorted(subjects.items(), key=lambda kv: kv[1]["correct"] / max(1, kv[1]["total"]))],
        "review": review,
    }


def owned_mock(db: Session, user: User, mock_id: int) -> WeeklyMock | None:
    """A paper this user may sit: a shared weekly paper, or their own personal timed paper."""
    mock = db.get(WeeklyMock, mock_id)
    if mock is None:
        return None
    if mock.part in PARTS:
        return mock
    return mock if (mock.track or "").startswith(f"u{user.id}:") else None
