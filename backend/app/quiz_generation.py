"""Database-aware AI MCQ generation.

Differences from the original single-function generator in main.py:
  * Retrieval uses the shared search() (query rewrite, neighbour expansion) and
    rotates away from passages already used by similar existing questions.
  * The "do not repeat" list is the ~40 existing MCQs most similar in meaning to
    the prompt (all books), not just the last 30 of one book.
  * New questions are rejected when their stem is too close in meaning
    (embedding cosine) to an existing or same-set question.
  * Batches of 5 run in parallel with one retry each; successful batches are
    kept when another fails.
  * Each question names the context chunk it came from; the server maps that to
    a real book/page ('book' grounding) or labels it AI knowledge ('ai'). The
    LLM never supplies a page number directly.
  * A client request_id makes the call idempotent: a retry of the same request
    returns (or waits for) the set the first attempt produced.
"""

import difflib
import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.llm import chat_completion
from app.models import MCQ
from app.retrieval import retrieval_service

logger = logging.getLogger(__name__)

BATCH_SIZE = 5
MAX_PARALLEL_BATCHES = 4
STEM_DUP_COSINE = 0.90          # same question reworded
SAME_ANSWER_DUP_COSINE = 0.80   # similar stem AND same correct-answer text
SIMILAR_EXISTING_LIMIT = 40
CONTEXT_CHAR_BUDGET = 9000
CONCEPT_DUP_COSINE = 0.88       # two questions testing the same fact, worded differently
# Parallel batches share one context; giving each a different angle stops them
# converging on the same few facts.
BATCH_ANGLES = (
    "pathophysiology, mechanisms and basic science",
    "clinical features, diagnosis and investigations",
    "management: drugs, fluids, doses and procedures",
    "complications, special situations and classic exam traps",
)

PROFILES = {
    "fcps": {
        "keys": ["A", "B", "C", "D", "E"],
        "style": (
            "Write CPSP FCPS-style single-best-answer questions: a short clinical or applied scenario, "
            "five options (A-E) of the same category, exactly one best answer. Include current terms and "
            "concepts that FCPS papers test even where the textbook wording is older."
        ),
    },
    # USMLE Step items are single best answer with 3-11 options, typically five.
    "usmle": {
        "keys": ["A", "B", "C", "D", "E"],
        "style": (
            "Write USMLE-style single-best-answer questions: a longer clinical vignette (age, presentation, "
            "findings, investigations) and five options (A-E) with exactly one best answer."
        ),
    },
    # Short rapid-recall drill (not an exam format).
    "quick": {
        "keys": ["A", "B", "C", "D"],
        "style": "Write short rapid-recall questions (one or two lines) with four options (A-D) and one best answer.",
    },
}
DEFAULT_PROFILE = "fcps"

_inflight: dict[str, threading.Event] = {}
_inflight_lock = threading.Lock()


class QuizGenerationError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _embed(texts: list[str]) -> np.ndarray:
    from app.ingestion import get_embedding_model

    if not texts:
        return np.zeros((0, 1024), dtype=np.float32)
    return np.asarray(get_embedding_model().encode(texts, normalize_embeddings=True), dtype=np.float32)


def quiz_set_id_for(request_id: str | None) -> str:
    if request_id:
        safe = re.sub(r"[^A-Za-z0-9_-]", "", request_id)[:40]
        if safe:
            return f"quiz_set_{safe}"
    return f"quiz_set_{uuid.uuid4().hex[:12]}"


def serialize_quiz_set(mcqs: list[MCQ], quiz_set_id: str, duplicates_skipped: int = 0,
                       failed_batches: int = 0, difficulty: int | None = None) -> dict[str, Any]:
    counts = {"book": sum(1 for m in mcqs if m.grounding == "book"),
              "ai": sum(1 for m in mcqs if m.grounding == "ai")}
    return {
        "quiz_set_id": quiz_set_id,
        "quiz_set_title": mcqs[0].quiz_set_title if mcqs else "",
        "total_questions": len(mcqs),
        "duplicates_skipped": duplicates_skipped,
        "failed_batches": failed_batches,
        "difficulty": difficulty if difficulty is not None else (mcqs[0].difficulty if mcqs else None),
        "grounding_counts": counts,
        "mcqs": [
            {
                "id": m.id,
                "question_text": m.question_text,
                "options": m.options,
                "correct_option": m.correct_option,
                "topic": m.topic,
                "difficulty": m.difficulty,
                "explanation_markdown": m.explanation_markdown,
                "tested_concept": m.tested_concept,
                "grounding": m.grounding,
            }
            for m in mcqs
        ],
    }


def load_quiz_set(db: Session, quiz_set_id: str) -> list[MCQ]:
    return db.query(MCQ).filter(MCQ.quiz_set_id == quiz_set_id).order_by(MCQ.id).all()


def _backfill_missing_embeddings(db: Session, limit: int = 50) -> None:
    """Embed up to `limit` existing MCQs that predate stem embeddings (cheap, bounded)."""
    missing = db.query(MCQ).filter(MCQ.stem_embedding.is_(None)).order_by(MCQ.id.desc()).limit(limit).all()
    if not missing:
        return
    vecs = _embed([m.question_text or "" for m in missing])
    for m, v in zip(missing, vecs):
        m.stem_embedding = v.tolist()
    db.commit()
    logger.info("Backfilled stem embeddings for %d MCQs", len(missing))


def _similar_existing(db: Session, prompt_vec: np.ndarray, limit: int = SIMILAR_EXISTING_LIMIT) -> list[dict]:
    rows = db.execute(
        text(
            "SELECT id, question_text, options, correct_option, tested_concept, source_chunk_ids, "
            "stem_embedding::text AS emb, 1 - (stem_embedding <=> CAST(:q AS vector)) AS sim "
            "FROM mcqs WHERE stem_embedding IS NOT NULL "
            "ORDER BY stem_embedding <=> CAST(:q AS vector) LIMIT :limit"
        ),
        {"q": str(prompt_vec.tolist()), "limit": limit},
    ).mappings().all()
    out = []
    for r in rows:
        opts = r["options"] or {}
        out.append({
            "id": r["id"],
            "stem": r["question_text"] or "",
            "answer": str(opts.get(r["correct_option"], "")),
            "concept": r["tested_concept"],
            "chunks": r["source_chunk_ids"] or [],
            "vec": np.asarray(json.loads(r["emb"]), dtype=np.float32),
            "sim": float(r["sim"]),
        })
    return out


def _is_text_duplicate(stem: str, others: list[str]) -> bool:
    norm = " ".join(stem.lower().split())
    for o in others:
        on = " ".join(o.lower().split())
        if norm == on or (len(norm) >= 60 and (norm in on or on in norm)):
            return True
        if difflib.SequenceMatcher(None, norm, on).ratio() > 0.8:
            return True
    return False


_UK_TO_US = (("aemi", "emi"), ("haem", "hem"), ("oedem", "edem"), ("oesoph", "esoph"), ("paed", "ped"),
             ("oestr", "estr"), ("ischaem", "ischem"), ("tumour", "tumor"), ("diarrhoea", "diarrhea"),
             ("colour", "color"), ("isation", "ization"))
_ANSWER_STOP = {"with", "and", "the", "of", "in", "to", "for", "or", "a", "an", "by", "on", "at", "from",
                "due", "only", "both", "none", "all", "above", "than", "into", "without", "after", "before"}
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[a-z]{3,}")


def _norm(text_: str) -> str:
    t = (text_ or "").lower()
    for uk, us in _UK_TO_US:
        t = t.replace(uk, us)
    return t


def _answer_supported(answer: str, passage: str) -> bool:
    """True if most key words of the correct answer occur in the cited passage.

    Guards against the model attributing a fact to a chunk that doesn't state it.
    Words are compared on a 6-letter prefix after UK->US normalisation; Bailey's
    lost fi/fl/ff ligatures are tolerated by also trying the damaged form.
    """
    tokens = [t for t in _TOKEN.findall(_norm(answer)) if t not in _ANSWER_STOP]
    if not tokens:
        return True
    body = _norm(passage)
    found = 0
    for t in tokens:
        stem = t if t[0].isdigit() else t[:6]
        variants = {stem} | {stem.replace(lig, "f") for lig in ("ffi", "ffl", "fi", "fl", "ff") if lig in stem}
        if any(v in body for v in variants):
            found += 1
    return found / len(tokens) >= 0.5


def _is_duplicate(q: dict, v: np.ndarray, existing_vecs: np.ndarray, existing_answers: list[str],
                  existing_stems: list[str], kept: list[tuple[dict, np.ndarray]]) -> bool:
    """Same question reworded (stem cosine), or a similar stem with the same correct answer."""
    answer = q["options"][q["correct_option"]].strip().lower()
    pools = [(existing_vecs, existing_answers)]
    if kept:
        pools.append((np.stack([kv for _, kv in kept]),
                      [kq["options"][kq["correct_option"]].strip().lower() for kq, _ in kept]))
    for vecs, answers in pools:
        if not len(vecs):
            continue
        sims = vecs @ v
        if (sims > STEM_DUP_COSINE).any():
            return True
        if any(sims[i] > SAME_ANSWER_DUP_COSINE and answers[i] == answer for i in range(len(sims))):
            return True
    return _is_text_duplicate(q["question_text"], existing_stems + [kq["question_text"] for kq, _ in kept])


def _concept_text(q: dict) -> str:
    """What a question tests: its concept label plus the correct answer."""
    return f"{q.get('tested_concept') or ''} {q['options'][q['correct_option']]}".strip()


def _build_prompts(prompt_text: str, profile: dict, difficulty: int | None, context_str: str,
                   avoid_lines: str, batch_no: int, n_batches: int, batch_target: int,
                   focus_chunks: list[int]) -> tuple[str, str]:
    keys = profile["keys"]
    options_example = ", ".join(f'"{k}": "..."' for k in keys)
    difficulty_line = ""
    if difficulty is not None:
        difficulty_line = (
            f"TARGET DIFFICULTY: {difficulty}/5 (1 = simple recall; 2 = straightforward application; "
            "3 = standard board-style with plausible distractors; 4 = multi-step clinical reasoning; "
            "5 = subtle distractors needing deep integration). Write every question at this level.\n"
        )
    system_prompt = (
        "You are an expert medical educator writing exam MCQs for MBBS/FCPS candidates.\n"
        f"{profile['style']}\n{difficulty_line}"
        "GROUNDING: Base questions on the TEXTBOOK CONTEXT whenever it covers the topic, and set "
        '"source_chunk" to the number of the chunk the tested fact comes from. If a question tests '
        "knowledge the context does not state (current terms, guidelines), set \"source_chunk\" to null; "
        "never invent book names or page numbers.\n"
        "Every question must test a DIFFERENT fact from the others and from the ALREADY ASKED list.\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "quiz_title": "Short topic title",\n'
        '  "questions": [\n'
        "    {\n"
        '      "question_text": "Stem...",\n'
        f'      "options": {{{options_example}}},\n'
        f'      "correct_option": "{keys[0]}",\n'
        '      "explanation": "Why the answer is right and each distractor wrong.",\n'
        '      "tested_concept": "Topic - specific fact tested (max 8 words)",\n'
        '      "buzzword": "One-line exam association, e.g. Olive-shaped mass + projectile non-bilious vomiting -> IHPS",\n'
        '      "source_chunk": 1\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    focus = ", ".join(f"Chunk {i}" for i in focus_chunks) if focus_chunks else "any chunk"
    angle = BATCH_ANGLES[(batch_no - 1) % len(BATCH_ANGLES)] if n_batches > 1 else "any aspect of the topic"
    user_prompt = (
        f"USER PROMPT: {prompt_text}\n\n"
        f"Generate exactly {batch_target} MCQs. This is batch {batch_no} of {n_batches} running in parallel; "
        f"other batches cover other angles. Write ONLY about: {angle}. "
        f"Prefer facts from {focus}.\n\n"
        f"TEXTBOOK CONTEXT:\n{context_str}\n\n"
        "ALREADY ASKED (do NOT test these facts again; write about different facts or angles):\n"
        f"{avoid_lines}"
    )
    return system_prompt, user_prompt


def _run_batch(system_prompt: str, user_prompt: str, label: str) -> list[dict]:
    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            raw = chat_completion(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                json_mode=True, temperature=0.4, max_tokens=6000, label=f"{label} try{attempt}", role="fast",
            )
            questions = json.loads(raw).get("questions") or []
            if questions:
                return questions
            last_err = ValueError("LLM returned no questions")
        except Exception as e:
            last_err = e
            logger.warning("MCQ %s attempt %d failed: %s: %s", label, attempt, type(e).__name__, e)
    raise last_err or RuntimeError("batch failed")


def _normalise_question(item: dict, keys: list[str]) -> dict | None:
    stem = str(item.get("question_text") or "").strip()
    options = item.get("options")
    if not stem or not isinstance(options, dict):
        return None
    options = {str(k).strip().upper(): str(v).strip() for k, v in options.items() if str(v).strip()}
    if len(options) < 4:
        return None
    correct = str(item.get("correct_option") or "").strip().upper()[:1]
    if correct not in options:
        return None
    return {**item, "question_text": stem, "options": options, "correct_option": correct}


def generate_quiz_set(db: Session, *, prompt_text: str, book_id: int | None, page_num: int | None,
                      mcq_count: int, difficulty: int | None, exam_profile: str | None,
                      request_id: str | None, chapter: str | None = None) -> dict[str, Any]:
    quiz_set_id = quiz_set_id_for(request_id)

    # Idempotency: a retry of the same request gets the first attempt's result.
    if request_id:
        existing = load_quiz_set(db, quiz_set_id)
        if existing:
            logger.info("Quiz request %s already completed; returning saved set", request_id)
            return serialize_quiz_set(existing, quiz_set_id)
        with _inflight_lock:
            event = _inflight.get(quiz_set_id)
            owner = event is None
            if owner:
                event = _inflight[quiz_set_id] = threading.Event()
        if not owner:
            logger.info("Quiz request %s is already running; waiting for it", request_id)
            event.wait(timeout=240)
            db.expire_all()
            existing = load_quiz_set(db, quiz_set_id)
            if existing:
                return serialize_quiz_set(existing, quiz_set_id)
            raise QuizGenerationError(409, "The original request for this quiz did not finish. Please try again.")
    try:
        return _generate(db, quiz_set_id, prompt_text, book_id, page_num, mcq_count, difficulty, exam_profile, chapter)
    finally:
        if request_id:
            with _inflight_lock:
                ev = _inflight.pop(quiz_set_id, None)
            if ev:
                ev.set()


def _generate(db: Session, quiz_set_id: str, prompt_text: str, book_id: int | None, page_num: int | None,
              mcq_count: int, difficulty: int | None, exam_profile: str | None,
              chapter: str | None = None) -> dict[str, Any]:
    from app.models import Chunk
    from app.retrieval import ContextChunk

    profile = PROFILES.get((exam_profile or DEFAULT_PROFILE).lower(), PROFILES[DEFAULT_PROFILE])

    # 1. What already exists on this topic (meaning-based, across all books).
    _backfill_missing_embeddings(db)
    prompt_vec = _embed([prompt_text])[0]
    similar = _similar_existing(db, prompt_vec)
    used_chunks = {int(c) for s in similar if s["sim"] >= 0.55 for c in s["chunks"]}

    # 2. Textbook context: explicit page, else search that rotates away from used passages.
    context: list = []
    if page_num is not None:
        q = db.query(Chunk).filter(Chunk.parent_id.is_(None), Chunk.page_number == page_num)
        if book_id:
            q = q.filter(Chunk.book_id == book_id)
        context = [ContextChunk(c.id, c.book_id, c.book, c.chapter, c.page_number, c.content, parts={c.id: c.content})
                   for c in q.order_by(Chunk.id).all()]
    if not context:
        context = retrieval_service.search(db, prompt_text, limit=6, book_id=book_id, chapter=chapter,
                                           exclude_chunk_ids=used_chunks).context

    blocks, total = [], 0
    for idx, c in enumerate(context, 1):
        title = c.book.title if c.book else "Textbook"
        block = f"Chunk {idx} [Book: {title} | Page: {c.page_number or 'N/A'}]\n{c.content}"
        if total + len(block) > CONTEXT_CHAR_BUDGET and blocks:
            break
        blocks.append(block)
        total += len(block)
    context = context[:len(blocks)]
    context_str = "\n\n".join(blocks) if blocks else (
        f"No textbook passages were found for this topic. Topic: {prompt_text}. "
        "Write questions from reliable medical knowledge and set every source_chunk to null."
    )

    avoid = [s for s in similar if s["sim"] >= 0.45][:SIMILAR_EXISTING_LIMIT]
    avoid_lines = "\n".join(
        f"- {s['stem'][:160]} -> {s['answer'][:60]}" + (f" [{s['concept']}]" if s["concept"] else "")
        for s in avoid
    ) or "None"

    # 3-4. Parallel batches, then de-duplication; one top-up round fills any
    # shortfall left by duplicates or a failed batch.
    existing_vecs = np.stack([s["vec"] for s in similar]) if similar else np.zeros((0, 1024), dtype=np.float32)
    existing_answers = [s["answer"].strip().lower() for s in similar]
    existing_stems = [s["stem"] for s in similar]
    kept: list[tuple[dict, np.ndarray]] = []
    # Seed with the concepts of similar existing MCQs so a reworded repeat of an
    # already-banked fact is caught even when the stems differ.
    kept_concepts: list[np.ndarray] = list(_embed([
        f"{s['concept'] or ''} {s['answer']}".strip() for s in similar if s["sim"] >= 0.55
    ]))
    duplicates = 0
    failed: list[Exception] = []
    total_batches = 0

    for round_no in (1, 2):
        needed = mcq_count - len(kept)
        if needed <= 0:
            break
        n_batches = (needed + BATCH_SIZE - 1) // BATCH_SIZE
        total_batches += n_batches
        kept_lines = [f"- {kq['question_text'][:160]} -> {kq['options'][kq['correct_option']][:60]}" for kq, _ in kept]
        prior = [] if avoid_lines == "None" else [avoid_lines]
        round_avoid = "\n".join(prior + kept_lines) or "None"
        jobs = []
        for b in range(n_batches):
            target = min(BATCH_SIZE, needed - b * BATCH_SIZE)
            focus = [i for i in range(1, len(context) + 1) if (i - 1 + round_no - 1) % n_batches == b]
            jobs.append(_build_prompts(prompt_text, profile, difficulty, context_str, round_avoid,
                                       b + 1, n_batches, target, focus))

        results: list = []
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_BATCHES, n_batches)) as pool:
            futures = [pool.submit(_run_batch, sp, up, f"mcq r{round_no} batch {i + 1}/{n_batches}")
                       for i, (sp, up) in enumerate(jobs)]
            for f in futures:
                try:
                    results.append(f.result())
                except Exception as e:
                    results.append(e)
        failed += [r for r in results if isinstance(r, Exception)]
        raw_questions = [q for r in results if not isinstance(r, Exception) for q in r]
        if not raw_questions:
            continue

        candidates = [q for q in (_normalise_question(x, profile["keys"]) for x in raw_questions) if q]
        vecs = _embed([q["question_text"] for q in candidates])
        concept_vecs = _embed([_concept_text(q) for q in candidates])
        for q, v, cv in zip(candidates, vecs, concept_vecs):
            if len(kept) >= mcq_count:
                break
            same_concept = bool(kept_concepts) and bool((np.stack(kept_concepts) @ cv > CONCEPT_DUP_COSINE).any())
            if same_concept or _is_duplicate(q, v, existing_vecs, existing_answers, existing_stems, kept):
                duplicates += 1
                continue
            kept.append((q, v))
            kept_concepts.append(cv)

    if not kept:
        if failed and len(failed) == total_batches:
            err = failed[-1]
            timeout = "timeout" in type(err).__name__.lower() or "timed out" in str(err).lower()
            raise QuizGenerationError(
                504 if timeout else 502,
                ("The AI service timed out while generating questions. Please try again."
                 if timeout else f"The AI service failed to generate questions: {err}"),
            )
        raise QuizGenerationError(
            409, "All generated questions repeated existing MCQs on this topic. Try a narrower or different topic."
        )

    # 5. Resolve grounding from the chunk index and save.
    quiz_title = prompt_text[:40].title()
    created: list[MCQ] = []
    for q, v in kept:
        src = q.get("source_chunk")
        chunk = None
        try:
            if src is not None and 1 <= int(src) <= len(context):
                chunk = context[int(src) - 1]
        except (TypeError, ValueError):
            chunk = None
        if chunk is not None and not _answer_supported(q["options"][q["correct_option"]], chunk.content):
            logger.info("Dropping unsupported source for %r (Chunk %s)", q["question_text"][:60], src)
            chunk = None

        explanation = str(q.get("explanation") or "No detailed explanation provided.").strip()
        buzzword = " ".join(str(q.get("buzzword") or "").split())
        if buzzword:
            explanation += f"\n\n**Buzzword:** {buzzword[:300]}"
        if chunk is not None:
            title = chunk.book.title if chunk.book else "Textbook"
            explanation += f"\n\n**Source**: {title}, Page {chunk.page_number or 'N/A'}"
            grounding, source_name, mcq_book = "book", title, chunk.book_id
        else:
            explanation += "\n\n**Source**: AI clinical knowledge (not from the ingested textbooks)"
            grounding, source_name, mcq_book = "ai", "AI clinical knowledge", book_id

        mcq = MCQ(
            book_id=mcq_book,
            quiz_set_id=quiz_set_id,
            quiz_set_title=quiz_title,
            question_text=q["question_text"],
            options=q["options"],
            correct_option=q["correct_option"],
            topic=quiz_title,
            main_category="AI MCQs",
            sub_category=source_name,
            difficulty=difficulty,
            explanation_markdown=explanation,
            status="ready",
            stem_embedding=v.tolist(),
            source_chunk_ids=chunk.chunk_ids if chunk is not None else [],
            tested_concept=(str(q.get("tested_concept") or "").strip()[:200] or None),
            grounding=grounding,
        )
        db.add(mcq)
        created.append(mcq)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Database error committing generated quiz: {e}")
        raise QuizGenerationError(500, f"Database commit error: {e}")

    logger.info(
        "Quiz %s: %d saved (%d book, %d ai), %d duplicates skipped, %d/%d batches failed",
        quiz_set_id, len(created), sum(m.grounding == "book" for m in created),
        sum(m.grounding == "ai" for m in created), duplicates, len(failed), total_batches,
    )
    return serialize_quiz_set(created, quiz_set_id, duplicates, len(failed), difficulty)


# ─── Background jobs ────────────────────────────────────────────────────────
# In-process registry (single uvicorn worker). A finished set is also found in
# the database, so polling keeps working after a restart once it has saved.

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
JOB_RETENTION_S = 3600


def _prune_jobs() -> None:
    import time

    cutoff = time.time() - JOB_RETENTION_S
    for key in [k for k, j in _jobs.items() if j.get("finished_at") and j["finished_at"] < cutoff]:
        _jobs.pop(key, None)


def start_quiz_job(params: dict[str, Any]) -> dict[str, Any]:
    """Start generate_quiz_set in a thread; returns {job_id, status}."""
    import time

    from app.database import SessionLocal

    request_id = params.get("request_id") or uuid.uuid4().hex
    params = {**params, "request_id": request_id}
    job_id = quiz_set_id_for(request_id)

    with _jobs_lock:
        _prune_jobs()
        existing = _jobs.get(job_id)
        if existing:
            return {"job_id": job_id, "status": existing["status"]}
        _jobs[job_id] = {"status": "running", "started_at": time.time()}

    def run() -> None:
        db = SessionLocal()
        try:
            result = generate_quiz_set(db, **params)
            update = {"status": "done", "result": result}
        except QuizGenerationError as e:
            update = {"status": "failed", "detail": e.detail, "status_code": e.status_code}
        except Exception as e:
            logger.exception("Quiz job %s failed", job_id)
            update = {"status": "failed", "detail": f"Internal error: {e}", "status_code": 500}
        finally:
            db.close()
        with _jobs_lock:
            _jobs[job_id].update(update, finished_at=time.time())

    threading.Thread(target=run, daemon=True, name=f"quiz-job-{job_id}").start()
    return {"job_id": job_id, "status": "running"}


def get_quiz_job(db: Session, job_id: str) -> dict[str, Any] | None:
    import time

    with _jobs_lock:
        job = dict(_jobs[job_id]) if job_id in _jobs else None
    if job is not None:
        out: dict[str, Any] = {"job_id": job_id, "status": job["status"]}
        if job["status"] == "running":
            out["elapsed_s"] = round(time.time() - job["started_at"], 1)
        elif job["status"] == "done":
            out["result"] = job["result"]
        else:
            out["detail"] = job.get("detail")
            out["status_code"] = job.get("status_code")
        return out
    mcqs = load_quiz_set(db, job_id)
    if mcqs:
        return {"job_id": job_id, "status": "done", "result": serialize_quiz_set(mcqs, job_id)}
    return None
