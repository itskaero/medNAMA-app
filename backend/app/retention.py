"""Retention engine: Mistake -> Concept -> Retest, Daily Dose, streaks, readiness.

The loop a student goes through every day:
  1. Answer questions, tapping how sure they were (sure / unsure / guess).
  2. A wrong or guessed answer creates (or reuses) a **concept card**: the concept
     in 2-3 lines, a *verbatim* textbook quote with book and page, the textbook
     figure when there is one, and a mnemonic. Quotes are string-checked
     against the retrieved passage; an unverifiable quote is dropped and the
     card is labelled AI knowledge.
  3. The concept is scheduled on widening intervals (1, 3, 7, 16, 35 days). A
     re-test uses a **different** question on the same concept, so the student
     learns the idea, not the answer letter.
  4. Each day's Daily Dose bundles due re-tests, new questions from the
     weakest subject, one "spot the diagnosis" textbook figure and one pearl.
  5. Readiness is estimated from confidence-weighted accuracy per FCPS subject
     against the 75% pass line.

Card and variant generation run in background threads so answering is never
blocked on the LLM; the UI polls /api/concepts/by-mcq/{id}.
"""

import json
import logging
import math
import random
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session

from app.llm import chat_completion, llm_configured
from app.models import MCQ, AnswerEvent, Book, ConceptCard, ConceptReview, DailySession, Figure, User

logger = logging.getLogger(__name__)

INTERVAL_DAYS = [1, 3, 7, 16, 35, 60]      # by box 0..5
MASTERED_BOX = 3
CONCEPT_REUSE_COSINE = 0.88                # same concept worded differently
VARIANT_SIMILAR_COSINE = 0.80              # an existing MCQ that tests this concept
DOSE_REVIEWS, DOSE_NEW = 5, 5
HIGH_YIELD_PER_DOSE = 2          # of DOSE_NEW, from the most-recalled past-paper topics
HIGH_YIELD_QUEUE_PER_DOSE = 3    # new high-yield questions written in the background per Dose built
PASS_LINE = 0.75
CONFIDENCE_WEIGHT = {"sure": 1.0, "unsure": 0.6, "guess": 0.2}

# FCPS subject from the book an MCQ came from (titles as ingested).
SUBJECT_BY_BOOK = [
    ("snell", "Anatomy"), ("guyton", "Physiology"), ("robbins", "Pathology"), ("robins", "Pathology"),
    ("ramada", "Pathology"), ("katzung", "Pharmacology"), ("kaztung", "Pharmacology"),
    ("microbiology", "Microbiology"), ("levinson", "Microbiology"), ("davidson", "Medicine"),
    ("bailey", "Surgery"), ("dhingra", "ENT"), ("nelson", "Paediatrics"), ("first aid", "Mixed (First Aid)"),
]
PART1_SUBJECTS = ["Anatomy", "Physiology", "Pathology", "Pharmacology", "Microbiology", "Biochemistry",
                  "Behavioural Sciences", "Community Medicine"]
# Seeded banks (scripts/seed_mcqs.py) name their subject/specialty in sub_category.
SEEDED_SUBJECT_CATEGORIES = ("FCPS Part 1", "FCPS Part 2")
# Seeded sets that are not FCPS practice: kept out of the Daily Dose, duels and the weekly mock.
NON_FCPS_CATEGORIES = ("English", "NTS mocks")

_pending: set[int] = set()          # mcq ids whose concept card is being built
_pending_recalls: set[int] = set()  # recall ids whose high-yield question is being written
_pending_lock = threading.Lock()
# One worker: each card runs a full textbook search (CPU-heavy). Queuing them
# keeps a 20-question quiz from launching 20 parallel searches on a small server.
_jobs = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="concept")


# ─── helpers ────────────────────────────────────────────────────────────────

def subject_for_title(title: str | None) -> str | None:
    t = (title or "").lower()
    for key, subject in SUBJECT_BY_BOOK:
        if key in t:
            return subject
    return None


def subject_for(book_title: str | None, main_category: str | None, sub_category: str | None) -> str | None:
    if main_category in SEEDED_SUBJECT_CATEGORIES and sub_category:
        return sub_category
    return subject_for_title(book_title) or subject_for_title(sub_category)


def subject_for_mcq(db: Session, mcq: MCQ) -> str | None:
    title = None
    if mcq.book_id:
        title = db.query(Book.title).filter(Book.id == mcq.book_id).scalar()
    return subject_for(title, mcq.main_category, mcq.sub_category)


def fcps_only(query):
    """Restrict an MCQ query to FCPS practice (drops the seeded English / NTS-mock sets)."""
    return query.filter(or_(MCQ.main_category.is_(None), MCQ.main_category.notin_(NON_FCPS_CATEGORIES)))


def subject_filter(db: Session, subject: str | None):
    """SQL condition for questions of a subject: from its textbooks, or seeded under that subject."""
    if not subject:
        return None
    book_ids = _books_for_subject(db, subject)
    seeded = and_(MCQ.main_category.in_(SEEDED_SUBJECT_CATEGORIES), MCQ.sub_category == subject)
    return or_(MCQ.book_id.in_(book_ids), seeded) if book_ids else seeded


def _embed(texts: list[str]) -> np.ndarray:
    from app.ingestion import get_embedding_model

    return np.asarray(get_embedding_model().encode(texts, normalize_embeddings=True), dtype=np.float32)


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _now() -> datetime:
    return datetime.utcnow()


# ─── concept cards ──────────────────────────────────────────────────────────

def serialize_card(db: Session, card: ConceptCard) -> dict[str, Any]:
    figure = None
    if card.figure_id:
        f = db.query(Figure.id, Figure.figure_label, Figure.caption, Figure.page_number).filter(
            Figure.id == card.figure_id).first()
        if f:
            figure = {"id": f.id, "figure_label": f.figure_label, "caption": f.caption,
                      "page_number": f.page_number, "book_title": card.book_title}
    return {
        "id": card.id,
        "title": card.title,
        "summary": card.summary,
        "quote": card.quote,
        "chunk_id": card.chunk_id,
        "book_title": card.book_title,
        "page_number": card.page_number,
        "figure": figure,
        "mnemonic": card.mnemonic,
        "subject": card.subject,
        "source": card.source,
        "grounding": card.grounding,
    }


def _concept_label(mcq: MCQ) -> tuple[str, str]:
    answer = str((mcq.options or {}).get(mcq.correct_option, ""))
    label = (mcq.tested_concept or "").strip() or " ".join((mcq.question_text or "").split()[:14])
    return label, answer


def _find_existing_card(db: Session, vec: np.ndarray) -> ConceptCard | None:
    row = db.execute(text(
        "SELECT id, 1 - (embedding <=> CAST(:v AS vector)) AS sim FROM concept_cards "
        "WHERE embedding IS NOT NULL AND visibility = 'all' ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1"
    ), {"v": str(vec.tolist())}).first()
    if row and float(row.sim) >= CONCEPT_REUSE_COSINE:
        return db.get(ConceptCard, row.id)
    return None


def build_concept_card(db: Session, mcq: MCQ) -> ConceptCard:
    """Create (or reuse) the concept card for an MCQ and link it. Commits."""
    if mcq.concept_id:
        return db.get(ConceptCard, mcq.concept_id)
    label, answer = _concept_label(mcq)
    vec = _embed([f"{label} {answer}"])[0]
    existing = _find_existing_card(db, vec)
    if existing:
        mcq.concept_id = existing.id
        db.commit()
        return existing

    from app.retrieval import retrieval_service

    result = retrieval_service.search(db, f"{mcq.question_text} {answer}", limit=4)
    blocks = result.context
    context = "\n\n".join(
        f"Chunk {i}: [{c.book.title if c.book else 'Textbook'}, Page {c.page_number}]\n{c.content}"
        for i, c in enumerate(blocks, 1)
    ) or "NO TEXTBOOK PASSAGES FOUND."
    card_json: dict[str, Any] = {}
    if llm_configured("fast"):
        try:
            raw = chat_completion(
                [
                    {"role": "system", "content": (
                        "You write short revision concept cards for FCPS exam candidates.\n"
                        "Given an MCQ, its correct answer and textbook passages, return JSON:\n"
                        '{"title": "concept in max 8 words", '
                        '"summary": "2-3 sentences: the concept and why the answer is right, exam-focused", '
                        '"quote": "ONE sentence copied EXACTLY, character for character, from a passage that supports '
                        'the concept, or empty string if none does", '
                        '"chunk": 1, '
                        '"mnemonic": "a well-known mnemonic from the passages if present; otherwise a short, genuinely '
                        'useful memory aid ending with (AI mnemonic); leave empty rather than force a weak one"}\n'
                        "Never invent quotes, books or pages."
                    )},
                    {"role": "user", "content": (
                        f"MCQ: {mcq.question_text}\nCorrect answer: {answer}\n"
                        f"Concept tested: {label}\n\nTEXTBOOK PASSAGES:\n{context[:7000]}"
                    )},
                ],
                json_mode=True, temperature=0.2, max_tokens=700, label="concept-card", role="fast",
            )
            card_json = json.loads(raw)
        except Exception as e:
            logger.warning("Concept card generation failed for MCQ %s: %s", mcq.id, e)

    quote = str(card_json.get("quote") or "").strip()
    block = None
    try:
        idx = int(card_json.get("chunk") or 0)
        block = blocks[idx - 1] if 1 <= idx <= len(blocks) else None
    except (TypeError, ValueError):
        block = None
    if quote and block is None:
        block = next((b for b in blocks if _norm_ws(quote) in _norm_ws(b.content)), None)
    if not quote or block is None or _norm_ws(quote) not in _norm_ws(block.content):
        if quote:
            logger.info("Dropping unverifiable concept quote for MCQ %s", mcq.id)
        quote, block = "", None

    figure_id = None
    if block is not None:
        figure_id = next((f["id"] for f in result.figures
                          if f.get("page_number") == block.page_number), None)

    card = ConceptCard(
        title=(str(card_json.get("title") or label).strip())[:200],
        summary=(str(card_json.get("summary") or f"{label}: {answer}").strip())[:1500],
        quote=quote[:1000] or None,
        chunk_id=block.id if block is not None else None,
        book_title=(block.book.title if block is not None and block.book else None),
        page_number=(block.page_number if block is not None else None),
        figure_id=figure_id,
        mnemonic=(str(card_json.get("mnemonic") or "").strip()[:300] or None),
        subject=subject_for_mcq(db, mcq),
        source="textbook",
        grounding="textbook" if block is not None else "ai",
        embedding=vec.tolist(),
    )
    db.add(card)
    db.flush()
    mcq.concept_id = card.id
    db.commit()
    return card


def _variant_mcq(db: Session, card: ConceptCard, exclude_ids: set[int]) -> MCQ | None:
    """An existing MCQ on this concept that isn't in exclude_ids."""
    q = db.query(MCQ).filter(MCQ.concept_id == card.id, MCQ.status != "private")
    if exclude_ids:
        q = q.filter(MCQ.id.notin_(exclude_ids))
    found = q.order_by(func.random()).first()
    if found:
        return found
    row = db.execute(text(
        "SELECT id FROM mcqs WHERE stem_embedding IS NOT NULL AND figure_id IS NULL AND status <> 'private' "
        "AND 1 - (stem_embedding <=> (SELECT embedding FROM concept_cards WHERE id = :c)) >= :m "
        + ("AND id <> ALL(:ex) " if exclude_ids else "") +
        "ORDER BY stem_embedding <=> (SELECT embedding FROM concept_cards WHERE id = :c) LIMIT 1"
    ), {"c": card.id, "m": VARIANT_SIMILAR_COSINE, **({"ex": list(exclude_ids)} if exclude_ids else {})}).first()
    return db.get(MCQ, row.id) if row else None


def generate_variant(db: Session, card: ConceptCard, avoid_stems: list[str]) -> MCQ | None:
    """Write one new FCPS-style (A-E) question testing the card's concept."""
    if not llm_configured("fast"):
        return None
    evidence = f"{card.summary}\n" + (f'Textbook ({card.book_title}, p.{card.page_number}): "{card.quote}"' if card.quote else "")
    try:
        raw = chat_completion(
            [
                {"role": "system", "content": (
                    "Write ONE CPSP FCPS-style single-best-answer MCQ with five options (A-E) that tests the given "
                    "concept from a DIFFERENT angle than the questions listed (new scenario, different correct "
                    "answer wording). Base it on the evidence. Return JSON: "
                    '{"question_text": "...", "options": {"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}, '
                    '"correct_option": "A", "explanation": "why right and why each other option is wrong"}'
                )},
                {"role": "user", "content": (
                    f"CONCEPT: {card.title}\nEVIDENCE:\n{evidence}\n\nAVOID THESE QUESTIONS:\n"
                    + "\n".join(f"- {s[:160]}" for s in avoid_stems[:5])
                )},
            ],
            json_mode=True, temperature=0.5, max_tokens=900, label="variant", role="fast",
        )
        q = json.loads(raw)
    except Exception as e:
        logger.warning("Variant generation failed for concept %s: %s", card.id, e)
        return None
    options = {str(k).upper(): str(v).strip() for k, v in (q.get("options") or {}).items() if str(v).strip()}
    correct = str(q.get("correct_option") or "").strip().upper()[:1]
    stem = str(q.get("question_text") or "").strip()
    if len(options) < 4 or correct not in options or not stem:
        return None
    explanation = str(q.get("explanation") or "").strip()
    if card.book_title and card.page_number:
        explanation += f"\n\n**Source**: {card.book_title}, Page {card.page_number}"
    mcq = MCQ(
        question_text=stem, options=options, correct_option=correct,
        topic=card.title, main_category="Concept re-test", sub_category=card.book_title or "AI clinical knowledge",
        explanation_markdown=explanation, status="ready", concept_id=card.id,
        tested_concept=card.title, grounding="book" if card.quote else "ai",
        stem_embedding=_embed([stem])[0].tolist(),
    )
    db.add(mcq)
    db.commit()
    return mcq


def _build_card_and_variant(mcq_id: int) -> None:
    """Background: build the concept card for an MCQ and pre-generate one re-test variant."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        mcq = db.get(MCQ, mcq_id)
        if mcq is None:
            return
        card = build_concept_card(db, mcq)
        if card and _variant_mcq(db, card, {mcq_id}) is None:
            generate_variant(db, card, [mcq.question_text or ""])
    except Exception:
        logger.exception("Concept card background job failed for MCQ %s", mcq_id)
        db.rollback()
    finally:
        db.close()
        with _pending_lock:
            _pending.discard(mcq_id)


def _prepare_variant(concept_id: int) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        card = db.get(ConceptCard, concept_id)
        if card is not None:
            stems = [m.question_text for m in db.query(MCQ).filter(MCQ.concept_id == concept_id).limit(5)]
            generate_variant(db, card, stems)
    except Exception:
        logger.exception("Variant preparation failed for concept %s", concept_id)
        db.rollback()
    finally:
        db.close()


def ensure_concept_async(mcq_id: int) -> str:
    """Start building the concept card for an MCQ if needed. Returns 'ready' or 'pending'."""
    with _pending_lock:
        if mcq_id in _pending:
            return "pending"
        _pending.add(mcq_id)
    _jobs.submit(_build_card_and_variant, mcq_id)
    return "pending"


def concept_status_for_mcq(db: Session, mcq_id: int) -> tuple[str, ConceptCard | None]:
    mcq = db.get(MCQ, mcq_id)
    if mcq is None:
        return "missing", None
    db.refresh(mcq)
    if mcq.concept_id:
        return "ready", db.get(ConceptCard, mcq.concept_id)
    with _pending_lock:
        pending = mcq_id in _pending
    return ("pending" if pending else "none"), None


# ─── scheduling ─────────────────────────────────────────────────────────────

def _schedule(review: ConceptReview, is_correct: bool, confidence: str) -> None:
    now = _now()
    review.reviews = (review.reviews or 0) + 1
    if not is_correct or confidence == "guess":
        review.box = 0
        review.lapses = (review.lapses or 0) + (0 if is_correct else 1)
        review.next_due = now + timedelta(days=INTERVAL_DAYS[0])
        review.last_result = "guess" if is_correct else "wrong"
    elif confidence == "unsure":
        box = review.box or 0
        review.next_due = now + timedelta(days=max(1, INTERVAL_DAYS[min(box, len(INTERVAL_DAYS) - 1)] // 2))
        review.last_result = "unsure"
    else:
        review.box = min((review.box or 0) + 1, len(INTERVAL_DAYS) - 1)
        review.next_due = now + timedelta(days=INTERVAL_DAYS[review.box])
        review.last_result = "correct"


def record_answer(db: Session, user_id: int, mcq: MCQ, selected: str, confidence: str = "sure",
                  source: str = "quiz") -> dict[str, Any]:
    """Log an answer, update the concept schedule, and start a concept card when needed."""
    from app.study_modes import classify_mistake, queue_pair_for_event

    confidence = confidence if confidence in CONFIDENCE_WEIGHT else "sure"
    is_correct = (selected or "").strip().upper() == (mcq.correct_option or "").strip().upper()
    mistake = classify_mistake(mcq, selected, is_correct, confidence)
    event = AnswerEvent(user_id=user_id, mcq_id=mcq.id, concept_id=mcq.concept_id,
                        selected_option=selected, is_correct=is_correct, confidence=confidence,
                        subject=subject_for_mcq(db, mcq), source=source, mistake_type=mistake)
    db.add(event)
    needs_card = (not is_correct) or confidence == "guess"
    concept_status = "none"
    if mcq.concept_id:
        review = db.query(ConceptReview).filter_by(user_id=user_id, concept_id=mcq.concept_id).first()
        if review is None and needs_card:
            review = ConceptReview(user_id=user_id, concept_id=mcq.concept_id, box=0, lapses=0, reviews=0)
            db.add(review)
        if review is not None:
            _schedule(review, is_correct, confidence)
        concept_status = "ready"
    elif needs_card:
        concept_status = ensure_concept_async(mcq.id)
    db.commit()
    if mistake == "confusion":
        queue_pair_for_event(event.id)   # name the two concepts, then build their comparison
    return {"is_correct": is_correct, "correct_option": mcq.correct_option,
            "concept_status": concept_status, "concept_id": mcq.concept_id, "mistake_type": mistake}


def attach_pending_reviews(db: Session, user_id: int) -> int:
    """Create schedules for wrong/guessed answers whose concept card finished in the background."""
    rows = db.execute(text(
        "SELECT DISTINCT m.concept_id FROM answer_events e JOIN mcqs m ON m.id = e.mcq_id "
        "WHERE e.user_id = :u AND m.concept_id IS NOT NULL AND (NOT e.is_correct OR e.confidence = 'guess') "
        "AND NOT EXISTS (SELECT 1 FROM concept_reviews r WHERE r.user_id = :u AND r.concept_id = m.concept_id)"
    ), {"u": user_id}).fetchall()
    for (cid,) in rows:
        db.add(ConceptReview(user_id=user_id, concept_id=cid, box=0, lapses=1, reviews=1,
                             next_due=_now() + timedelta(days=INTERVAL_DAYS[0]), last_result="wrong"))
    if rows:
        db.commit()
    return len(rows)


# ─── Daily Dose ─────────────────────────────────────────────────────────────

def _seen_mcq_ids(db: Session, user_id: int) -> set[int]:
    ids = {r[0] for r in db.query(AnswerEvent.mcq_id).filter(AnswerEvent.user_id == user_id,
                                                              AnswerEvent.mcq_id.isnot(None)).distinct()}
    ids |= {r[0] for r in db.execute(text(
        "SELECT DISTINCT a.mcq_id FROM attempt_answers a JOIN quiz_attempts q ON q.id = a.quiz_attempt_id "
        "WHERE q.user_id = :u"), {"u": user_id})}
    return ids


def subject_accuracy(db: Session, user_id: int, days: int = 60) -> dict[str, dict[str, float]]:
    since = _now() - timedelta(days=days)
    stats: dict[str, dict[str, float]] = {}
    for subject, correct, conf in db.query(AnswerEvent.subject, AnswerEvent.is_correct, AnswerEvent.confidence).filter(
            AnswerEvent.user_id == user_id, AnswerEvent.created_at >= since):
        s = stats.setdefault(subject or "Other", {"n": 0, "score": 0.0})
        s["n"] += 1
        s["score"] += CONFIDENCE_WEIGHT.get(conf, 1.0) if correct else 0.0
    for s in stats.values():
        s["mastery"] = s["score"] / s["n"] if s["n"] else 0.0
    return stats


def _weakest_subject(db: Session, user_id: int) -> str | None:
    stats = subject_accuracy(db, user_id)
    tried = [(v["mastery"], k) for k, v in stats.items() if v["n"] >= 3]
    return min(tried)[1] if tried else None


def _books_for_subject(db: Session, subject: str | None) -> list[int]:
    if not subject:
        return []
    return [b.id for b in db.query(Book.id, Book.title).all() if subject_for_title(b.title) == subject]


def _new_questions(db: Session, user_id: int, seen: set[int], n: int) -> list[MCQ]:
    weakest = _weakest_subject(db, user_id)
    base = fcps_only(db.query(MCQ).filter(MCQ.status == "ready", MCQ.figure_id.is_(None)))
    if seen:
        base = base.filter(MCQ.id.notin_(seen))
    picked: list[MCQ] = []
    cond = subject_filter(db, weakest)
    if cond is not None:
        picked = base.filter(cond).order_by(func.random()).limit(n).all()
    if len(picked) < n:
        more = base.filter(MCQ.id.notin_([m.id for m in picked] or [-1])).order_by(func.random()).limit(n - len(picked)).all()
        picked += more
    return picked


# ─── high-yield (past-paper frequency) ──────────────────────────────────────
# The recall bank (a private source) is never shown. It only decides which
# topics get extra practice; the questions themselves are written from the
# verified textbook quotes the Referee found, and are stored as status
# 'private' so no other pool (quizzes, duels, re-tests, browser) serves them.

def high_yield_enabled(user: User) -> bool:
    from app.config import settings

    mode = (settings.high_yield_dose or "off").lower()
    return mode == "all" or (mode == "admin" and user.role == "admin")


def can_see_mcq(user: User, mcq: MCQ) -> bool:
    return mcq.status != "private" or high_yield_enabled(user)


def _high_yield_questions(db: Session, seen: set[int], n: int) -> list[tuple[MCQ, int]]:
    """Up to n written high-yield questions, sampled in proportion to how often past papers ask them."""
    rows = db.execute(text(
        "SELECT r.mcq_id, COALESCE(r.times_asked, 1) AS asked FROM recall_items r "
        "JOIN mcqs m ON m.id = r.mcq_id AND m.status = 'private' "
        "WHERE r.kind = 'headline'" + (" AND r.mcq_id <> ALL(:seen)" if seen else "") +
        # Efraimidis-Spirakis weighted sampling: smallest -ln(U)/w first.
        " ORDER BY -ln(1 - random()) / GREATEST(COALESCE(r.times_asked, 1), 1) LIMIT :n"
    ), {"n": n, **({"seen": list(seen)} if seen else {})}).all()
    out = []
    for mcq_id, asked in rows:
        mcq = db.get(MCQ, mcq_id)
        if mcq is not None:
            out.append((mcq, int(asked)))
    return out


def generate_high_yield_mcq(db: Session, recall: Any) -> MCQ | None:
    """Write one FCPS question on a past-paper topic from the Referee's verified textbook quotes."""
    evidence = [e for e in (recall.evidence or []) if e.get("quote")]
    if not evidence or not llm_configured("fast"):
        return None
    quotes = "\n".join(f'{i}. ({e.get("book_title")}, p.{e.get("page_number")}) "{e["quote"]}"'
                       for i, e in enumerate(evidence, 1))
    try:
        raw = chat_completion(
            [
                {"role": "system", "content": (
                    "Write ONE CPSP FCPS-style single-best-answer MCQ with five options (A-E) on the fact stated in "
                    "the TEXTBOOK QUOTES. The topic line says which fact past papers test; write your own new stem "
                    "and options (do not copy the topic wording). The correct answer must be stated by a quote; "
                    "distractors must be plausible. Return JSON: "
                    '{"question_text": "...", "options": {"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}, '
                    '"correct_option": "A", "quote": 1, "concept": "concept tested, max 8 words", '
                    '"explanation": "why right (name the book, never say quote 1/2) and why each other option is wrong"}'
                )},
                {"role": "user", "content": (
                    f"TOPIC: {recall.question} -> {recall.textbook_answer or recall.answer}\n\nTEXTBOOK QUOTES:\n{quotes}"
                )},
            ],
            json_mode=True, temperature=0.4, max_tokens=900, label="high-yield", role="fast",
        )
        q = json.loads(raw)
    except Exception as e:
        logger.warning("High-yield question failed for recall %s: %s", recall.id, e)
        return None
    options = {str(k).upper(): str(v).strip() for k, v in (q.get("options") or {}).items() if str(v).strip()}
    correct = str(q.get("correct_option") or "").strip().upper()[:1]
    stem = str(q.get("question_text") or "").strip()
    if len(options) < 4 or correct not in options or not stem:
        return None
    try:
        ev = evidence[int(q.get("quote") or 1) - 1]
    except (TypeError, ValueError, IndexError):
        ev = evidence[0]
    book_id = None
    if ev.get("chunk_id"):
        book_id = db.execute(text("SELECT book_id FROM chunks WHERE id = :c"), {"c": ev["chunk_id"]}).scalar()
    explanation = str(q.get("explanation") or "").strip()
    explanation += f'\n\n> "{ev["quote"]}"\n\n**Source**: {ev.get("book_title")}, Page {ev.get("page_number")}'
    concept = str(q.get("concept") or "").strip() or recall.question[:80]
    mcq = MCQ(
        question_text=stem, options=options, correct_option=correct, book_id=book_id,
        topic=concept, main_category="High-yield", sub_category=ev.get("book_title") or "Textbook",
        explanation_markdown=explanation, status="private", tested_concept=concept, grounding="book",
        source_chunk_ids=[e["chunk_id"] for e in evidence if e.get("chunk_id")],
        stem_embedding=_embed([stem])[0].tolist(),
    )
    db.add(mcq)
    db.flush()
    recall.mcq_id = mcq.id
    db.commit()
    return mcq


def _prepare_high_yield(recall_id: int) -> None:
    from app.database import SessionLocal
    from app.models import RecallItem

    db = SessionLocal()
    try:
        recall = db.get(RecallItem, recall_id)
        if recall is not None and recall.mcq_id is None:
            generate_high_yield_mcq(db, recall)
    except Exception:
        logger.exception("High-yield preparation failed for recall %s", recall_id)
        db.rollback()
    finally:
        db.close()
        with _pending_lock:
            _pending_recalls.discard(recall_id)


def queue_high_yield(db: Session, k: int = HIGH_YIELD_QUEUE_PER_DOSE) -> int:
    """Write k more high-yield questions in the background, favouring the most-asked topics."""
    ids = db.execute(text(
        "SELECT id FROM recall_items WHERE kind = 'headline' AND verdict = 'supported' AND mcq_id IS NULL "
        "AND jsonb_typeof(evidence) = 'array' AND jsonb_array_length(evidence) > 0 "
        "ORDER BY -ln(1 - random()) / GREATEST(COALESCE(times_asked, 1), 1) LIMIT :k"
    ), {"k": k}).scalars().all()
    queued = 0
    for rid in ids:
        with _pending_lock:
            if rid in _pending_recalls:
                continue
            _pending_recalls.add(rid)
        _jobs.submit(_prepare_high_yield, rid)
        queued += 1
    return queued


def spot_diagnosis_mcq(db: Session, user_id: int, seen: set[int]) -> MCQ | None:
    """An image question from a captioned textbook figure (reuses one if it exists)."""
    existing = db.query(MCQ).filter(MCQ.figure_id.isnot(None))
    if seen:
        existing = existing.filter(MCQ.id.notin_(seen))
    found = existing.order_by(func.random()).first()
    if found is not None and random.random() < 0.5:
        return found
    fig = db.execute(text(
        "SELECT f.id, f.caption, f.figure_label, f.page_number, b.title FROM figures f JOIN books b ON b.id = f.book_id "
        "WHERE f.caption_source = 'printed' AND NOT f.is_decorative AND length(f.caption) BETWEEN 40 AND 400 "
        "AND NOT EXISTS (SELECT 1 FROM mcqs m WHERE m.figure_id = f.id) ORDER BY random() LIMIT 1"
    )).first()
    if fig is None or not llm_configured("fast"):
        return found
    try:
        raw = chat_completion(
            [
                {"role": "system", "content": (
                    "Turn a textbook figure caption into ONE 'spot the diagnosis / identify' MCQ for FCPS students. "
                    "The student sees only the image. The stem asks what the image shows (or the most likely diagnosis, "
                    "structure or finding). The correct option must be exactly what the caption states; four distractors "
                    "must be plausible look-alikes. Return JSON: {\"question_text\": \"...\", \"options\": {\"A\": \"...\", "
                    "\"B\": \"...\", \"C\": \"...\", \"D\": \"...\", \"E\": \"...\"}, \"correct_option\": \"A\", "
                    "\"explanation\": \"what the image shows and the distinguishing features\"}. If the caption is not "
                    "specific enough for a fair question, return {\"skip\": true}."
                )},
                {"role": "user", "content": f"Caption ({fig.title}, p.{fig.page_number}): {fig.caption}"},
            ],
            json_mode=True, temperature=0.4, max_tokens=700, label="spot-diagnosis", role="fast",
        )
        q = json.loads(raw)
    except Exception as e:
        logger.warning("Spot-diagnosis generation failed for figure %s: %s", fig.id, e)
        return found
    options = {str(k).upper(): str(v).strip() for k, v in (q.get("options") or {}).items() if str(v).strip()}
    correct = str(q.get("correct_option") or "").strip().upper()[:1]
    if q.get("skip") or len(options) < 4 or correct not in options:
        return found
    mcq = MCQ(
        question_text=str(q.get("question_text") or "What does this textbook figure show?").strip(),
        options=options, correct_option=correct, topic="Spot the diagnosis", main_category="Spot the diagnosis",
        sub_category=fig.title, status="ready", figure_id=fig.id, grounding="book",
        explanation_markdown=(str(q.get("explanation") or "").strip()
                              + f"\n\n**Source**: {fig.title}, Page {fig.page_number} - {fig.figure_label}: {fig.caption}"),
    )
    db.add(mcq)
    db.commit()
    return mcq


def _pearl(db: Session, user_id: int, used: set[int], is_admin: bool = False) -> ConceptCard | None:
    q = db.query(ConceptCard).filter(~ConceptCard.id.in_(
        db.query(ConceptReview.concept_id).filter(ConceptReview.user_id == user_id)))
    if not is_admin:
        q = q.filter(ConceptCard.visibility == "all")   # private-source pearls stay admin-only
    if used:
        q = q.filter(ConceptCard.id.notin_(used))
    # Prefer pearls from a pearls/recall source, then textbook-quoted cards.
    return (q.filter(ConceptCard.source == "recall_book").order_by(func.random()).first()
            or q.filter(ConceptCard.quote.isnot(None)).order_by(func.random()).first())


def _apply_streak_freeze(db: Session, user: User, today: date) -> None:
    """If yesterday was missed but the day before was completed, spend a freeze to keep the streak."""
    yesterday, before = today - timedelta(days=1), today - timedelta(days=2)
    y = db.query(DailySession).filter_by(user_id=user.id, day=yesterday).first()
    if y and (y.completed_at or y.freeze_used):
        return
    b = db.query(DailySession).filter_by(user_id=user.id, day=before).first()
    if not (b and (b.completed_at or b.freeze_used)) or (user.streak_freezes or 0) <= 0:
        return
    if y is None:
        y = DailySession(user_id=user.id, day=yesterday, items=[])
        db.add(y)
    y.freeze_used = True
    user.streak_freezes = (user.streak_freezes or 0) - 1
    db.commit()


def streak(db: Session, user_id: int, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    rows = {r.day: r for r in db.query(DailySession).filter(DailySession.user_id == user_id,
                                                              DailySession.day >= today - timedelta(days=400))}
    kept = lambda d: d in rows and (rows[d].completed_at is not None or rows[d].freeze_used)  # noqa: E731
    day = today if kept(today) else today - timedelta(days=1)
    count = 0
    while kept(day):
        count += 1
        day -= timedelta(days=1)
    best, run = 0, 0
    for d in sorted(rows):
        run = run + 1 if kept(d) and kept(d - timedelta(days=1)) else (1 if kept(d) else 0)
        best = max(best, run)
    return {"current": count, "best": max(best, count), "done_today": kept(today) and rows[today].completed_at is not None}


def get_or_build_daily_session(db: Session, user: User, today: date | None = None) -> DailySession:
    today = today or date.today()
    session = db.query(DailySession).filter_by(user_id=user.id, day=today).first()
    if session and session.items:
        return session
    _apply_streak_freeze(db, user, today)
    attach_pending_reviews(db, user.id)
    seen = _seen_mcq_ids(db, user.id)
    items: list[dict[str, Any]] = []

    due = (db.query(ConceptReview).filter(ConceptReview.user_id == user.id,
                                          ConceptReview.next_due <= datetime.combine(today, datetime.max.time()))
           .order_by(ConceptReview.next_due).limit(DOSE_REVIEWS).all())
    for review in due:
        card = db.get(ConceptCard, review.concept_id)
        mcq = _variant_mcq(db, card, seen) if card else None
        if mcq is None and card is not None:
            _jobs.submit(_prepare_variant, card.id)   # ready for the next Dose
            continue
        if mcq is not None:
            items.append({"type": "review", "mcq_id": mcq.id, "concept_id": card.id, "done": False})
            seen.add(mcq.id)

    from app.study_modes import user_pairs

    for p in user_pairs(db, user.id):
        if p["status"] == "ready" and not p["cleared"] and p["next_mcq_id"] and p["next_mcq_id"] not in seen:
            items.append({"type": "pair", "pair_id": p["id"], "mcq_id": p["next_mcq_id"], "done": False})
            seen.add(p["next_mcq_id"])
            break

    n_new = DOSE_NEW
    if high_yield_enabled(user):
        for mcq, asked in _high_yield_questions(db, seen, HIGH_YIELD_PER_DOSE):
            items.append({"type": "new", "mcq_id": mcq.id, "high_yield": True, "times_asked": asked, "done": False})
            seen.add(mcq.id)
            n_new -= 1
        queue_high_yield(db)   # grow the pool for the coming days
    for mcq in _new_questions(db, user.id, seen, n_new):
        items.append({"type": "new", "mcq_id": mcq.id, "done": False})
        seen.add(mcq.id)

    spot = spot_diagnosis_mcq(db, user.id, seen)
    if spot is not None:
        items.append({"type": "image", "mcq_id": spot.id, "figure_id": spot.figure_id, "done": False})

    used_pearls = {i.get("concept_id") for s in db.query(DailySession).filter(DailySession.user_id == user.id)
                   for i in (s.items or []) if i.get("type") == "pearl"}
    pearl = _pearl(db, user.id, {p for p in used_pearls if p}, is_admin=(user.role == "admin"))
    if pearl is not None:
        items.append({"type": "pearl", "concept_id": pearl.id, "done": False})

    if session is None:
        session = DailySession(user_id=user.id, day=today, items=items)
        db.add(session)
    else:
        session.items = items
    db.commit()
    return session


def mark_item_done(db: Session, session: DailySession, index: int, correct: bool | None = None) -> None:
    items = list(session.items or [])
    if 0 <= index < len(items):
        items[index] = {**items[index], "done": True, **({"correct": correct} if correct is not None else {})}
        session.items = items
        if all(i.get("done") for i in items) and session.completed_at is None:
            session.completed_at = _now()
        db.commit()


# ─── readiness ──────────────────────────────────────────────────────────────

def readiness(db: Session, user: User) -> dict[str, Any]:
    stats = subject_accuracy(db, user.id)
    subjects = []
    for name in sorted(stats, key=lambda k: -stats[k]["n"]):
        s = stats[name]
        subjects.append({"subject": name, "answered": int(s["n"]), "mastery": round(s["mastery"], 3),
                         "enough_data": s["n"] >= 10})
    scored = [s for s in subjects if s["enough_data"]]
    predicted = (sum(s["mastery"] * s["answered"] for s in scored) / sum(s["answered"] for s in scored)) if scored else None
    reviews = db.query(ConceptReview).filter(ConceptReview.user_id == user.id)
    total_concepts = reviews.count()
    mastered = reviews.filter(ConceptReview.box >= MASTERED_BOX).count()
    now = _now()
    due_now = reviews.filter(ConceptReview.next_due <= now).count()
    due_week = reviews.filter(ConceptReview.next_due <= now + timedelta(days=7)).count()
    days_left = (user.exam_date - date.today()).days if user.exam_date else None
    daily_target = None
    if days_left and days_left > 0:
        answered_total = db.query(func.count(AnswerEvent.id)).filter(AnswerEvent.user_id == user.id).scalar() or 0
        bank = db.query(func.count(MCQ.id)).filter(MCQ.status == "ready").scalar() or 0
        remaining_new = max(0, bank - answered_total)
        daily_target = int(min(80, max(15, math.ceil(remaining_new / days_left) + math.ceil(due_week / 7))))
    return {
        "predicted_score": round(predicted, 3) if predicted is not None else None,
        "pass_line": PASS_LINE,
        "subjects": subjects,
        "missing_part1_subjects": [s for s in PART1_SUBJECTS if s not in stats],
        "concepts": {"tracked": total_concepts, "mastered": mastered, "due_now": due_now, "due_this_week": due_week},
        "exam_date": user.exam_date.isoformat() if user.exam_date else None,
        "days_left": days_left,
        "daily_target": daily_target,
        "streak": streak(db, user.id),
        "streak_freezes": user.streak_freezes,
        "note": "Estimate from your confidence-weighted accuracy; needs 10+ answers per subject.",
        "mistakes": _mistakes(db, user.id),
        "sprint": _sprint(user),
    }


def _mistakes(db: Session, user_id: int) -> dict[str, Any]:
    from app.study_modes import mistake_profile

    return mistake_profile(db, user_id)


def _sprint(user: User) -> dict[str, Any]:
    from app.study_modes import sprint_status

    return sprint_status(user)



# ─── Explain it back (Feynman check) ───────────────────────────────────────

def grade_explanation(db: Session, user_id: int, card: ConceptCard, explanation: str) -> dict[str, Any]:
    """Mark a student's own explanation of a concept against the card and its textbook passage."""
    explanation = (explanation or "").strip()
    if len(explanation) < 15:
        raise ValueError("Write at least a sentence in your own words.")
    passage = ""
    if card.chunk_id:
        from app.models import Chunk

        chunk = db.get(Chunk, card.chunk_id)
        passage = chunk.content if chunk else ""
    reference = (
        f"CONCEPT: {card.title}\nKEY SUMMARY: {card.summary}\n"
        + (f'TEXTBOOK QUOTE ({card.book_title}, p.{card.page_number}): "{card.quote}"\n' if card.quote else "")
        + (f"TEXTBOOK PASSAGE:\n{passage[:3000]}" if passage else "")
    )
    if not llm_configured("fast"):
        raise RuntimeError("The AI service is not configured.")
    raw = chat_completion(
        [
            {"role": "system", "content": (
                "You mark a medical student's explanation of a concept, like a friendly FCPS examiner. Compare it with "
                "the reference. Identify the 2-4 key points the reference says matter. Return JSON: "
                '{"score": 0-100, "points_hit": ["..."], "points_missed": ["..."], '
                '"errors": ["anything the student stated that is wrong"], '
                '"feedback": "2 sentences, encouraging and specific"}. '
                "Judge meaning, not wording. Never reward confident but wrong statements."
            )},
            {"role": "user", "content": f"REFERENCE:\n{reference}\n\nSTUDENT'S EXPLANATION:\n{explanation[:1500]}"},
        ],
        json_mode=True, temperature=0.0, max_tokens=700, label="explain-back", role="fast",
    )
    out = json.loads(raw)
    try:
        score = max(0, min(100, int(out.get("score") or 0)))
    except (TypeError, ValueError):
        score = 0
    # Feed the schedule: a solid explanation counts as a confident correct review.
    review = db.query(ConceptReview).filter_by(user_id=user_id, concept_id=card.id).first()
    if review is None:
        review = ConceptReview(user_id=user_id, concept_id=card.id, box=0, lapses=0, reviews=0)
        db.add(review)
    _schedule(review, is_correct=score >= 50, confidence="sure" if score >= 80 else "unsure")
    db.add(AnswerEvent(user_id=user_id, concept_id=card.id, is_correct=score >= 70, confidence="sure",
                       subject=card.subject, source="explain"))
    db.commit()
    return {
        "score": score,
        "points_hit": [str(x) for x in (out.get("points_hit") or [])][:6],
        "points_missed": [str(x) for x in (out.get("points_missed") or [])][:6],
        "errors": [str(x) for x in (out.get("errors") or [])][:4],
        "feedback": str(out.get("feedback") or "").strip(),
        "reference": {"quote": card.quote, "book_title": card.book_title, "page_number": card.page_number},
        "next_review": review.next_due.isoformat() if review.next_due else None,
    }
