"""Hardened MCQs: AI rewrites of existing questions that keep the tested fact and the
correct answer but make the stem and options harder.

A hardened question is written from ONE seed (any non-private bank/AI/generated
question the student can already see) at a requested difficulty:

  1. shape       five options A-E, one key (quiz_generation._normalise_question)
  2. answer kept the rewrite's correct answer agrees with the seed's (past_papers.answers_agree)
  3. new text    not a reworded copy of the seed, the nearest bank questions or other
                 hardened versions in the same run (quiz_generation._is_duplicate)
  4. grounding   the cited passage states the answer (quiz_generation._answer_supported), else 'ai'
  5. referee     the Answer-Key Referee on the finished question: contradicted, books_conflict
                 or a key the textbooks do not back is dropped; textbooks_silent is kept
                 as AI knowledge, provided something (a source or the referee) verified it

Each seed produces ONE harder rewrite that keeps the same correct-answer concept. The
rewrite changes the wording of the stem and the distractors and adds a reasoning layer
so the "heard it once" pattern does not transfer (see DIFFICULTY_LINES).

Results are stored as MCQs in main_category 'Hardened MCQs' under a quiz_set_id (the
same field the AI-quiz history uses), so a hardened set appears in Mock Builder ->
Quiz History, can be practised like any set, and only ever contains rewrites of
questions the student could already see.

Generation runs in the background, one worker thread per seed (as app/twists.py);
asking again with the same request_id returns the stored set.
"""

import json
import logging
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.llm import chat_completion, llm_configured
from app.models import MCQ, MCQTag
from app.past_papers import answer_norm, answers_agree, distinct_by_group

logger = logging.getLogger(__name__)

CATEGORY = "Hardened MCQs"
CONTEXT_CHAR_BUDGET = 9000
ALLOWED_COUNTS = (5, 10, 15, 20)
MAX_SEEDS = 20                 # one harder rewrite per seed
SIMILAR_EXISTING_LIMIT = 40
DROP_VERDICTS = {"contradicted", "books_conflict"}

# The two levels the builder offers. 1-3 are what the bank mostly already is; the whole
# point of "harder" is to climb to reasoning the student has not sat yet.
DIFFICULTY_LINES = {
    4: ("4/5 multi-step reasoning: keep the facts but make the student connect several steps "
        "(extra findings and a discriminating investigation, or the next decision after the "
        "diagnosis) before the answer is certain."),
    5: ("5/5 deep integration: nearly-identical distractors that only an exact command of the "
        "topic separates, plus a trap for the mirror-image presentation of the same condition."),
}


class HardenError(Exception):
    pass


def validate(req: dict) -> None:
    """Raise HardenError on a malformed harden request (shared by sync and job paths)."""
    count = int(req.get("num_questions") or 0)
    if count not in ALLOWED_COUNTS:
        raise HardenError(f"num_questions must be a multiple of 5 between 5 and 20 (got {count}).")
    difficulty = int(req.get("difficulty") or 0)
    if difficulty not in DIFFICULTY_LINES:
        raise HardenError(f"difficulty must be 4 or 5 (got {difficulty}).")
    if not (bool(req.get("seed_ids")) or bool(req.get("categories")) or bool(req.get("sub_categories"))
            or bool(req.get("topics"))):
        raise HardenError("Give seed_ids, or the categories/sub_categories/topics to pick questions from.")
    if not llm_configured("chat"):
        raise HardenError("The AI service is not configured.")


def harden_set_id(request_id: str | None) -> str:
    if request_id:
        safe = "".join(c for c in request_id if c.isalnum() or c in "_-")[:40]
        if safe:
            return f"hardened_{safe}"
    return f"hardened_{uuid.uuid4().hex[:12]}"


def _pick_seeds(db: Session, user, req: dict) -> list[MCQ]:
    """Select the questions to harden: the same scope practice uses, minus already-hardened ones."""
    from app.retention import access_scope

    query = access_scope(db.query(MCQ).filter(MCQ.status != "private"), user)
    if req.get("seed_ids"):
        query = query.filter(MCQ.id.in_(req["seed_ids"]))
    elif req.get("sub_categories"):
        query = query.filter(MCQ.sub_category.in_(req["sub_categories"]))
    elif req.get("categories"):
        query = query.filter(MCQ.main_category.in_(req["categories"]))
    if req.get("topics"):
        query = query.filter(MCQ.topic.in_(req["topics"]))
    # Never harden a hardened question: it would compound rewrites and blind the dedup check.
    query = query.filter(or_(MCQ.main_category.is_(None), MCQ.main_category != CATEGORY))
    candidates = query.order_by(func.random()).limit(int(req["num_questions"]) * 2 + 10).all()
    return distinct_by_group(candidates, int(req["num_questions"]))


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


def _prompt(seed: MCQ, answer: str, concept: str, difficulty: int, context: str) -> list[dict]:
    rng = random.Random(seed.id)   # a fixed letter per seed keeps keys balanced across runs
    key_letter = rng.choice("ABCDE")
    system = (
        "You are an FCPS Part 1 examiner. You are given an EXISTING question and must REWRITE it "
        "into a HARDER single-best-answer version that tests the SAME fact and keeps the SAME "
        "correct answer.\n"
        "The rewrite must:\n"
        "- change the wording and presentation of the stem (a different age/sex/setting is fine; "
        "  the underlying facts stay the same).\n"
        "- add one extra reasoning layer: more findings, a discriminating investigation, or the "
        "  next decision after the diagnosis.\n"
        "- replace the distractors with MORE plausible wrong answers of the same kind (same "
        "  category: all diseases, all drugs, all investigations...) that a student who knows "
        "  most of the topic would still pick.\n"
        "- keep the correct answer as the SAME concept (you may reword it: 'chlorpromazine' -> "
        "  'a phenothiazine antipsychotic' - the fact tested is unchanged).\n"
        "- not reuse the original stem or option text.\n"
        f"- be written at {DIFFICULTY_LINES[difficulty]}\n"
        "GROUNDING: base the rewrite on the TEXTBOOK PASSAGES; any new detail you introduce "
        "(a value, a test, a threshold) must be consistent with them. Set \"source_chunk\" to the "
        "number of the passage that supports the correct answer; if none does, set it to null. "
        "Never invent book names or page numbers.\n"
        "Return ONLY JSON: {\"question_text\": \"...\", \"options\": {\"A\": \"...\", \"B\": \"...\", "
        "\"C\": \"...\", \"D\": \"...\", \"E\": \"...\"}, \"correct_option\": \"A\", \"explanation\": \"why "
        "the (unchanged) answer is right and why each new distractor is wrong\", "
        "\"tested_concept\": \"max 8 words\", \"source_chunk\": 1}"
    )
    opts = "\n".join(f"{k}. {v}" for k, v in sorted((seed.options or {}).items()))
    user = (
        f"ORIGINAL QUESTION (keep its fact and its answer):\n{seed.question_text}\n{opts}\n"
        f"KEY: {seed.correct_option}. {answer}\nCONCEPT: {concept}\n\n"
        f"Write exactly ONE harder rewrite ({difficulty}/5) with its correct answer at option "
        f"{key_letter}.\n\nTEXTBOOK PASSAGES:\n{context or 'NO TEXTBOOK PASSAGES FOUND.'}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _runner(seed_id: int, req: dict, set_id: str, title: str, ctx: dict):
    """One worker: write + check + persist ONE harder rewrite of ONE seed (its own session)."""
    from app.database import SessionLocal
    from app.quiz_generation import _answer_supported, _embed, _is_duplicate, _normalise_question
    from app.referee import judge

    s = SessionLocal()
    try:
        seed = s.get(MCQ, seed_id)
        if seed is None:
            return None, "seed gone"
        answer = str((seed.options or {}).get(seed.correct_option, ""))
        concept = seed.tested_concept or seed.topic or answer
        blocks = _passages(s, seed, concept, answer)
        context = "\n\n".join(
            f"Chunk {i}: [{b.book.title if b.book else 'Textbook'}, Page {b.page_number}]\n{b.content}"
            for i, b in enumerate(blocks, 1))

        raw = chat_completion(_prompt(seed, answer, concept, req["difficulty"], context), json_mode=True,
                              temperature=0.5, max_tokens=2400, label=f"harden seed {seed.id}", role="chat")
        q = _normalise_question(json.loads(raw) or {}, list("ABCDE"))
        if q is None or sorted(q["options"]) != list("ABCDE"):
            return None, "shape"

        # The rewrite must keep the tested answer (that is the whole point).
        if not answers_agree(answer_norm(q["options"][q["correct_option"]]), answer_norm(answer)):
            return None, "answer changed"

        # Not a reworded copy of the seed, the nearest bank questions or this run's other rewrites.
        v = _embed([q["question_text"]])[0]
        with ctx["lock"]:
            if _is_duplicate(q, v, ctx["pool_vecs"], ctx["pool_answers"], ctx["pool_stems"], ctx["kept"]):
                return None, "duplicate"
            ctx["kept"].append((q, v))

        # Grounding: the cited passage must state the answer, else the question is AI knowledge.
        chunk, source = None, None
        try:
            if q.get("source_chunk") is not None and 1 <= int(q["source_chunk"]) <= len(blocks):
                chunk = blocks[int(q["source_chunk"]) - 1]
        except (TypeError, ValueError):
            chunk = None
        if chunk is not None and not _answer_supported(q["options"][q["correct_option"]], chunk.content or ""):
            logger.info("Dropping unsupported source on hardened rewrite of %s", seed.id)
            chunk = None
        if chunk is not None:
            source = {"title": chunk.book.title if chunk.book else "Textbook", "page": chunk.page_number,
                      "chunk_ids": chunk.chunk_ids, "book_id": chunk.book_id}

        # Referee the finished question.
        try:
            verdict = judge(s, q["question_text"], options=q["options"], key=q["correct_option"])
        except Exception as e:
            logger.warning("Referee failed for hardened rewrite of %s: %s", seed.id, e)
            verdict = {"verdict": None}
        v_name = verdict.get("verdict")
        if v_name in DROP_VERDICTS or (v_name == "supported" and verdict.get("agrees_with_key") is False):
            return None, f"referee {v_name}"
        if v_name is None and source is None:
            return None, "unverified"

        explanation = str(q.get("explanation") or "No detailed explanation provided.").strip()
        if source is not None:
            explanation += f"\n\n**Source**: {source['title']}, Page {source['page'] or 'N/A'}"
        else:
            explanation += "\n\n**Source**: AI clinical knowledge (not from the ingested textbooks)"
        explanation += (f"\n\n**Hardened rewrite** at {req['difficulty']}/5 of the question "
                        f"\"{' '.join(seed.question_text.split())[:140]}\" (answer unchanged: {answer})")

        mcq = MCQ(
            book_id=source["book_id"] if source else seed.book_id,
            quiz_set_id=set_id, quiz_set_title=title,
            question_text=q["question_text"], options=q["options"], correct_option=q["correct_option"],
            topic=seed.topic, main_category=CATEGORY, sub_category=seed.sub_category,
            tested_concept=(str(q.get("tested_concept") or "").strip()[:200] or concept[:200]),
            explanation_markdown=explanation, status="ready", access=seed.access,
            grounding="book" if source is not None else "ai",
            stem_embedding=v.tolist(), source_chunk_ids=source["chunk_ids"] if source else [],
            difficulty=req["difficulty"], concept_id=seed.concept_id,
        )
        s.add(mcq)
        s.flush()
        s.add(MCQTag(mcq_id=mcq.id, axis="hardened", label=str(seed.id)))
        s.commit()
        return mcq, None
    except Exception as e:
        logger.exception("Harden worker for seed %s failed", seed_id)
        s.rollback()
        return None, "error"
    finally:
        s.close()


def harden_set(db: Session, user, req: dict) -> dict[str, Any]:
    """Write, gate and store harder rewrites of the requested questions. Idempotent per request_id."""
    from app.quiz_generation import _similar_existing, serialize_quiz_set

    validate(req)
    req = {**req, "num_questions": int(req["num_questions"]), "difficulty": int(req["difficulty"])}
    set_id = harden_set_id(req.get("request_id"))

    existing = db.query(MCQ).filter(MCQ.quiz_set_id == set_id).order_by(MCQ.id).all()
    if existing:
        logger.info("Harden request %s already completed; returning stored set", req.get("request_id"))
        return serialize_quiz_set(existing, set_id, 0, 0, req["difficulty"])

    seeds = _pick_seeds(db, user, req)
    if not seeds:
        raise HardenError("No questions match these filters to harden.")

    # One shared duplication pool: the nearest bank questions to every seed plus the seeds
    # themselves, so rewrites never repeat each seed or the bank.
    ctx: dict = {"lock": threading.Lock(), "kept": [], "pool_vecs": [], "pool_answers": [], "pool_stems": []}
    for seed in seeds:
        vec = np.asarray(seed.stem_embedding, dtype=np.float32) if seed.stem_embedding is not None else None
        if vec is not None and len(ctx["pool_vecs"]) < SIMILAR_EXISTING_LIMIT * 4:
            try:
                near = _similar_existing(db, vec, limit=SIMILAR_EXISTING_LIMIT)
                ctx["pool_vecs"].extend(n["vec"] for n in near)
                ctx["pool_answers"].extend(n["answer"].strip().lower() for n in near)
                ctx["pool_stems"].extend(n["stem"] for n in near)
            except Exception:
                logger.warning("Similarity lookup failed for seed %s; dedup pool kept small", seed.id)
        ctx["pool_answers"].append(str((seed.options or {}).get(seed.correct_option, "")).strip().lower())
        ctx["pool_stems"].append(seed.question_text or "")
        if vec is not None:
            ctx["pool_vecs"].append(vec)
    ctx["pool_vecs"] = np.stack(ctx["pool_vecs"]) if ctx["pool_vecs"] else np.zeros((0, 1024), dtype=np.float32)

    label = (req.get("label") or ", ".join(req.get("sub_categories") or req.get("categories") or []) or "practice")
    title = f"Hardened · {label[:40].title()}"

    dropped: dict[str, int] = {}
    kept_count = 0
    seed_ids = [s.id for s in seeds]

    def on_drop(why: str):
        dropped[why] = dropped.get(why, 0) + 1

    with ThreadPoolExecutor(max_workers=min(6, len(seed_ids))) as pool:
        futures = {pool.submit(_runner, sid, req, set_id, title, ctx): sid for sid in seed_ids}
        for fut in as_completed(futures):
            try:
                mcq, why = fut.result()
            except Exception as e:
                logger.warning("Harden worker crashed: %s", e)
                on_drop("error")
                continue
            if mcq is None:
                on_drop(why or "error")
            else:
                kept_count += 1

    # Workers saved through their own sessions; re-read (all columns bound to `db`)
    # so serialization can touch every attribute of a live row.
    created = db.query(MCQ).filter(MCQ.quiz_set_id == set_id).order_by(MCQ.id).all()

    logger.info("Harden %s (%s): %d kept of %d seeds (dropped %s)", set_id, title,
                kept_count, len(seed_ids), dropped or "none")
    if not created:
        raise HardenError("None of the rewrites passed the checks" + (f" ({', '.join(f'{k}: {v}' for k, v in dropped.items())})" if dropped else "") + ".")
    return serialize_quiz_set(created, set_id, sum(dropped.values()), 0, req["difficulty"])


# ─── Background jobs (same pattern as app/quiz_generation.py) ─────────────────

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
JOB_RETENTION_S = 3600


def _prune_jobs() -> None:
    cutoff = time.time() - JOB_RETENTION_S
    for key in [k for k, j in _jobs.items() if j.get("finished_at") and j["finished_at"] < cutoff]:
        _jobs.pop(key, None)


def get_harden_job(db: Session, job_id: str) -> dict[str, Any] | None:
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
    mcqs = db.query(MCQ).filter(MCQ.quiz_set_id == job_id).order_by(MCQ.id).all()
    if mcqs:
        from app.quiz_generation import serialize_quiz_set

        return {"job_id": job_id, "status": "done",
                "result": serialize_quiz_set(mcqs, job_id, 0, 0, mcqs[0].difficulty)}
    return None


def start_harden_job(user, req: dict) -> dict[str, Any]:
    try:
        validate(req)
    except HardenError as e:
        return {"error": str(e)}

    if req.get("seed_ids"):
        from app.database import SessionLocal
        from app.retention import access_scope

        wanted = [int(i) for i in req["seed_ids"]][:20]
        s = SessionLocal()
        try:
            visible = [r[0] for r in access_scope(
                s.query(MCQ.id).filter(MCQ.status != "private", MCQ.id.in_(wanted),
                                       or_(MCQ.main_category.is_(None), MCQ.main_category != CATEGORY)),
                user).all()]
        finally:
            s.close()
        if not visible:
            return {"error": "None of those questions exist or are visible to you."}
        req = {**req, "seed_ids": visible}

    request_id = req.get("request_id") or uuid.uuid4().hex
    req = {**req, "request_id": request_id}
    if not req.get("label"):
        req["label"] = ", ".join(req.get("sub_categories") or req.get("categories") or [])
    job_id = harden_set_id(request_id)

    with _jobs_lock:
        _prune_jobs()
        existing = _jobs.get(job_id)
        if existing:
            return {"job_id": job_id, "status": existing["status"]}
        _jobs[job_id] = {"status": "running", "started_at": time.time()}

    def run() -> None:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            result = harden_set(db, user, req)
            update = {"status": "done", "result": result}
        except HardenError as e:
            update = {"status": "failed", "detail": str(e)}
        except Exception as e:
            logger.exception("Harden job %s failed", job_id)
            db.rollback()
            update = {"status": "failed", "detail": f"Internal error: {e}", "status_code": 500}
        finally:
            db.close()
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id].update(update, finished_at=time.time())

    threading.Thread(target=run, daemon=True, name=f"harden-{job_id}").start()
    return {"job_id": job_id, "status": "running"}