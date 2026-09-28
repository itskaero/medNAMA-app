"""Past-paper Twists: new questions written from a past-paper question that ask a different thing.

A twist keeps the seed question's tested concept and changes the ask (next step instead of diagnosis,
the mechanism behind the answer, a changed finding that flips the answer to its look-alike...), with a
new correct answer and new options of the same kind. It is written from textbook passages and has to
pass, in order:

  1. shape       five options A-E, one key (quiz_generation._normalise_question)
  2. new ask     its answer is not the seed's answer (past_papers.answers_agree)
  3. new text    not a reworded copy of the seed, its other recalled versions, other twists or the bank
                 (quiz_generation._is_duplicate: stem cosine / similar stem with the same answer)
  4. grounding   the cited passage states the answer (quiz_generation._answer_supported), else 'ai'
  5. referee     the Answer-Key Referee on the finished question: contradicted, books_conflict or a
                 key the textbooks do not back is dropped; textbooks_silent is kept as AI knowledge

Twists are stored as MCQs with twist_of = seed (main_category 'Past-paper twists', access of the seed)
and tags twist=<type>, referee=<verdict>. Generation runs in a background thread per seed; asking again
returns the stored twists. scripts/prewarm_twists.py pre-generates them for the most-asked questions.
"""

import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.llm import chat_completion, llm_configured
from app.models import MCQ, ConfusablePair, MCQTag
from app.past_papers import answer_norm, answers_agree

logger = logging.getLogger(__name__)

TWISTS_PER_SEED = 3
CATEGORY = "Past-paper twists"
CONTEXT_CHAR_BUDGET = 9000
TWIST_TYPES = {
    "discriminator": ("Changed finding",
                      "Keep the scenario but change ONE key finding so that the correct answer becomes the "
                      "look-alike instead of the original answer. The explanation must name the finding that "
                      "changed and why it moves the answer."),
    "next-step": ("Next step",
                  "Keep the same patient or scenario and ask for the next best investigation or the first-line "
                  "management instead of the diagnosis or fact the original asked for."),
    "mechanism": ("Mechanism",
                  "Ask for the mechanism, pathophysiology or drug action that explains the original answer."),
    "reverse": ("Reverse",
                "Put the original answer in the stem and ask for its classic feature, association or cause."),
    "complication": ("Complication",
                     "Ask about a complication, prognosis or most common cause of death related to the original answer."),
    "numbers": ("Numbers",
                "Ask for a value, dose, threshold or time interval that the TEXTBOOK PASSAGES state about the topic."),
}
DROP_VERDICTS = {"contradicted", "books_conflict"}

_jobs: dict[int, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


class TwistError(Exception):
    pass


def is_seed(mcq: MCQ) -> bool:
    """Twists are written from past-paper questions only."""
    return mcq is not None and (mcq.main_category or "").startswith("Past papers") and mcq.status != "private"


def _looks_clinical(stem: str) -> bool:
    s = stem.lower()
    return len(s) > 120 or any(w in s for w in ("year old", "year-old", "presents", "presented", "complains", "patient"))


def _lookalike(db: Session, answer: str) -> ConfusablePair | None:
    a = answer_norm(answer)
    if len(a) < 4:
        return None
    for pair in db.query(ConfusablePair).all():
        for term in (pair.term_a, pair.term_b):
            t = answer_norm(term)
            if t and (t == a or answers_agree(t, a)):
                return pair
    return None


def pick_types(stem: str, has_lookalike: bool) -> list[str]:
    order = (["discriminator"] if has_lookalike else []) + (["next-step"] if _looks_clinical(stem) else [])
    order += [t for t in ("mechanism", "reverse", "complication", "numbers") if t not in order]
    return order[:TWISTS_PER_SEED]


def _passages(db: Session, seed: MCQ, concept: str, answer: str) -> list:
    from app.retrieval import retrieval_service

    blocks, seen, size = [], set(), 0
    for q in (f"{concept} {answer}", f"{' '.join(seed.question_text.split())[:300]} {answer}"):
        for c in retrieval_service.search(db, q[:600], limit=6).context:
            if c.id in seen or size + len(c.content or "") > CONTEXT_CHAR_BUDGET:
                continue
            seen.add(c.id)
            size += len(c.content or "")
            blocks.append(c)
    return blocks


def _prompt(seed: MCQ, answer: str, concept: str, types: list[str], pair: ConfusablePair | None,
            context: str) -> list[dict]:
    from app.quiz_generation import PROFILES

    # Models put the key at B far too often; a fixed random letter per twist keeps keys balanced
    # (shuffling afterwards would break explanations that name options by letter).
    rng = random.Random(seed.id)
    wanted = "\n".join(f'- "{t}": {TWIST_TYPES[t][1]} Put its correct answer at option {rng.choice("ABCDE")}.'
                       for t in types)
    look = (f"\nLOOK-ALIKE for the changed-finding twist: {pair.term_a} vs {pair.term_b}." if pair else "")
    system = (
        "You are an FCPS Part 1 examiner. From an ORIGINAL past-paper question you write TWISTS: new questions on "
        "the same concept that ask a DIFFERENT thing, with a DIFFERENT correct answer and new options.\n"
        f"{PROFILES['fcps']['style']}\n"
        "Each twist must stand alone (do not refer to 'the original question'). Options must be of one kind "
        "(all drugs, all investigations...). Exactly one best answer.\n"
        "GROUNDING: base each twist on the TEXTBOOK PASSAGES and set \"source_chunk\" to the passage number that "
        "states the correct answer; if none does, set it to null. Never invent book names or page numbers.\n"
        "Return ONLY JSON: {\"twists\": [{\"type\": \"<one of the requested types>\", \"question_text\": \"...\", "
        "\"options\": {\"A\": \"...\", \"B\": \"...\", \"C\": \"...\", \"D\": \"...\", \"E\": \"...\"}, "
        "\"correct_option\": \"A\", \"explanation\": \"why the answer is right and why each other option is wrong\", "
        "\"tested_concept\": \"max 8 words\", \"source_chunk\": 1}]}"
    )
    opts = "\n".join(f"{k}. {v}" for k, v in sorted((seed.options or {}).items()))
    user = (
        f"ORIGINAL QUESTION:\n{seed.question_text}\n{opts}\nKEY: {seed.correct_option}. {answer}\n"
        f"CONCEPT: {concept}{look}\n\n"
        f"Write exactly {len(types)} twists, one of each type:\n{wanted}\n\n"
        f"TEXTBOOK PASSAGES:\n{context or 'NO TEXTBOOK PASSAGES FOUND.'}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _existing_pool(db: Session, seed: MCQ, seed_vec: np.ndarray) -> tuple[np.ndarray, list[str], list[str]]:
    """Questions a twist must not duplicate: the bank's nearest, the seed's recalled versions and its twists."""
    from app.quiz_generation import _similar_existing

    near = _similar_existing(db, seed_vec, limit=40)
    have = {n["id"] for n in near}
    related = [MCQ.twist_of == seed.id, MCQ.id == seed.id]
    if seed.recall_group is not None:
        related.append(MCQ.recall_group == seed.recall_group)
    extra = db.query(MCQ).filter(or_(*related), MCQ.stem_embedding.isnot(None)).all()
    vecs = [n["vec"] for n in near] + [np.asarray(m.stem_embedding, dtype=np.float32) for m in extra if m.id not in have]
    answers = [n["answer"].strip().lower() for n in near] + [
        str((m.options or {}).get(m.correct_option, "")).strip().lower() for m in extra if m.id not in have]
    stems = [n["stem"] for n in near] + [m.question_text for m in extra if m.id not in have]
    return (np.stack(vecs) if vecs else np.zeros((0, 1024), dtype=np.float32)), answers, stems


def generate_twists(db: Session, seed: MCQ) -> list[MCQ]:
    """Write, check and store up to TWISTS_PER_SEED twists of a past-paper question. Idempotent per seed."""
    from app.quiz_generation import _answer_supported, _embed, _is_duplicate, _normalise_question
    from app.referee import judge
    from app.retention import build_concept_card

    done = db.query(MCQ).filter(MCQ.twist_of == seed.id).order_by(MCQ.id).all()
    if done:
        return done
    if not llm_configured("chat"):
        raise TwistError("The AI service is not configured.")

    answer = str((seed.options or {}).get(seed.correct_option, ""))
    try:
        card = build_concept_card(db, seed)
    except Exception as e:   # the card is a help, not a requirement
        logger.warning("Concept card for twist seed %s failed: %s", seed.id, e)
        db.rollback()
        card = None
    concept = (card.title if card else None) or seed.tested_concept or seed.topic or answer
    pair = _lookalike(db, answer)
    types = pick_types(seed.question_text, pair is not None)
    blocks = _passages(db, seed, concept, answer)
    context = "\n\n".join(f"Chunk {i}: [{b.book.title if b.book else 'Textbook'}, Page {b.page_number}]\n{b.content}"
                          for i, b in enumerate(blocks, 1))

    raw = chat_completion(_prompt(seed, answer, concept, types, pair, context), json_mode=True,
                          temperature=0.5, max_tokens=4000, label=f"twists seed {seed.id}", role="chat")
    items = (json.loads(raw) or {}).get("twists") or []

    seed_vec = (np.asarray(seed.stem_embedding, dtype=np.float32) if seed.stem_embedding is not None
                else _embed([seed.question_text])[0])
    pool_vecs, pool_answers, pool_stems = _existing_pool(db, seed, seed_vec)
    seed_answer = answer_norm(answer)
    kept: list[tuple[dict, np.ndarray]] = []
    dropped: dict[str, int] = {}

    def drop(why: str) -> None:
        dropped[why] = dropped.get(why, 0) + 1

    candidates = []
    for item in items:
        q = _normalise_question(item, list("ABCDE"))
        kind = str((item or {}).get("type") or "").strip().lower()
        if q is None:
            drop("shape")
            continue
        if kind not in TWIST_TYPES:
            kind = next((t for t in types if t not in {c[0] for c in candidates}), types[0])
        if answers_agree(answer_norm(q["options"][q["correct_option"]]), seed_answer):
            drop("same answer as the original")
            continue
        candidates.append((kind, q))
    vecs = _embed([q["question_text"] for _, q in candidates])

    # 1) Cheap checks on this thread: duplicates, and whether the cited passage states the answer.
    survivors = []   # (kind, question, vector, source) where source is None or plain values of the chunk
    for (kind, q), v in zip(candidates, vecs):
        if _is_duplicate(q, v, pool_vecs, pool_answers, pool_stems, kept):
            drop("duplicate")
            continue
        kept.append((q, v))
        chunk = None
        try:
            src = q.get("source_chunk")
            if src is not None and 1 <= int(src) <= len(blocks):
                chunk = blocks[int(src) - 1]
        except (TypeError, ValueError):
            chunk = None
        if chunk is not None and not _answer_supported(q["options"][q["correct_option"]], chunk.content or ""):
            chunk = None
        source = None if chunk is None else {
            "title": chunk.book.title if chunk.book else "Textbook", "page": chunk.page_number,
            "chunk_ids": chunk.chunk_ids, "book_id": chunk.book_id}
        survivors.append((kind, q, v, source))

    # 2) The Answer-Key Referee, all twists at once (each check is a retrieval + an AI call). Each twist is
    #    saved the moment its check passes, so the panel can show it while the others are still being checked.
    def referee(q: dict) -> dict:
        from app.database import SessionLocal

        s = SessionLocal()
        try:
            return judge(s, q["question_text"], options=q["options"], key=q["correct_option"])
        except Exception as e:
            logger.warning("Referee failed for a twist of %s: %s", seed.id, e)
            return {"verdict": None}
        finally:
            s.close()

    created: list[MCQ] = []
    with ThreadPoolExecutor(max_workers=max(1, len(survivors))) as pool:
        futures = {pool.submit(referee, sv[1]): sv for sv in survivors}
        for fut in as_completed(futures):
            kind, q, v, source = futures[fut]
            verdict = fut.result()
            v_name = verdict.get("verdict")
            if v_name in DROP_VERDICTS or (v_name == "supported" and verdict.get("agrees_with_key") is False):
                drop(f"referee {v_name}")
                continue
            if v_name is None and source is None:   # neither refereed nor textbook-grounded
                drop("unverified")
                continue
            explanation = str(q.get("explanation") or "").strip() or "No detailed explanation provided."
            if source is not None:
                explanation += f"\n\n**Source**: {source['title']}, Page {source['page'] or 'N/A'}"
            else:
                explanation += "\n\n**Source**: AI clinical knowledge (not from the ingested textbooks)"
            explanation += (f"\n\n**Twist of a past-paper question** ({TWIST_TYPES[kind][0].lower()}): "
                            f"{' '.join(seed.question_text.split())[:160]} (answer: {answer})")
            mcq = MCQ(
                question_text=q["question_text"], options=q["options"], correct_option=q["correct_option"],
                main_category=CATEGORY, sub_category=seed.sub_category, topic=seed.topic,
                tested_concept=(str(q.get("tested_concept") or "").strip()[:200] or concept[:200]),
                explanation_markdown=explanation, status="ready", access=seed.access,
                grounding="book" if source is not None else "ai", stem_embedding=v.tolist(),
                source_chunk_ids=source["chunk_ids"] if source else [], book_id=source["book_id"] if source else None,
                concept_id=card.id if card else None, twist_of=seed.id,
            )
            db.add(mcq)
            db.flush()
            db.add(MCQTag(mcq_id=mcq.id, axis="twist", label=kind))
            db.add(MCQTag(mcq_id=mcq.id, axis="referee", label=v_name or "unchecked"))
            db.commit()
            created.append(mcq)
    logger.info("Twists for %s: %d kept of %d written (dropped %s)", seed.id, len(created), len(items), dropped or "none")
    if not created:
        raise TwistError("No twist passed the checks for this question" + (f" ({dropped})" if dropped else "") + ".")
    return created


def serialize(db: Session, twists: list[MCQ]) -> list[dict[str, Any]]:
    tags: dict[int, dict[str, str]] = {}
    for mid, axis, label in db.query(MCQTag.mcq_id, MCQTag.axis, MCQTag.label).filter(
            MCQTag.mcq_id.in_([t.id for t in twists] or [-1]), MCQTag.axis.in_(("twist", "referee"))):
        tags.setdefault(mid, {})[axis] = label
    return [{
        "id": t.id, "question_text": t.question_text, "options": t.options, "correct_option": t.correct_option,
        "explanation_markdown": t.explanation_markdown, "grounding": t.grounding,
        "twist_type": tags.get(t.id, {}).get("twist"),
        "twist_label": TWIST_TYPES.get(tags.get(t.id, {}).get("twist") or "", ("Twist",))[0],
        "referee": tags.get(t.id, {}).get("referee"),
        "main_category": t.main_category, "sub_category": t.sub_category, "figure_id": t.figure_id,
    } for t in twists]


def status(db: Session, seed_id: int) -> dict[str, Any]:
    twists = db.query(MCQ).filter(MCQ.twist_of == seed_id).order_by(MCQ.id).all()
    with _jobs_lock:
        job = dict(_jobs.get(seed_id) or {})
    if job.get("status") == "running":   # twists saved so far are shown while the rest are checked
        return {"status": "running", "elapsed_s": round(time.time() - job["started_at"], 1),
                "twists": serialize(db, twists)}
    if twists:
        return {"status": "done", "twists": serialize(db, twists)}
    if job.get("status") == "failed":
        return {"status": "failed", "detail": job.get("detail")}
    return {"status": "none"}


def start(seed_id: int) -> dict[str, Any]:
    """Generate twists for a seed in a background thread (one job per seed at a time)."""
    from app.database import SessionLocal

    with _jobs_lock:
        job = _jobs.get(seed_id)
        if job and job["status"] == "running":
            return {"status": "running"}
        _jobs[seed_id] = {"status": "running", "started_at": time.time()}

    def run() -> None:
        db = SessionLocal()
        try:
            seed = db.get(MCQ, seed_id)
            generate_twists(db, seed)
            update = {"status": "done"}
        except TwistError as e:
            update = {"status": "failed", "detail": str(e)}
        except Exception as e:
            logger.exception("Twist job for %s failed", seed_id)
            db.rollback()
            update = {"status": "failed", "detail": f"{type(e).__name__}: {e}"}
        finally:
            db.close()
        with _jobs_lock:
            _jobs[seed_id].update(update, finished_at=time.time())

    threading.Thread(target=run, daemon=True, name=f"twists-{seed_id}").start()
    return {"status": "running"}


def most_asked_seeds(db: Session, min_years: int = 2, exam_category: str = "Past papers · FCPS Part 1") -> list[int]:
    """Seeds for pre-generation: one version per recalled question asked in at least min_years years."""
    rows = (db.query(MCQ.id, MCQ.recall_group)
            .filter(MCQ.main_category == exam_category, MCQ.asked_years.isnot(None),
                    func.jsonb_array_length(MCQ.asked_years) >= min_years)
            .order_by(func.jsonb_array_length(MCQ.asked_years).desc(), MCQ.id).all())
    out, taken = [], set()
    for i, g in rows:
        key = g if g is not None else -i
        if key not in taken:
            taken.add(key)
            out.append(i)
    return out
