"""Hardened MCQs: AI rewrites of existing questions that keep the tested fact and the correct answer
but make the stem and options harder.

A hardened question is written from ONE seed (any non-private bank/AI/generated question the student
can already see) at a requested difficulty, and has to pass, in order:

  1. shape       five options A-E, one key (quiz_generation._normalise_question)
  2. answer kept the key's text is the seed's answer (the prompt asks for it word for word;
                 past_papers.answers_agree tolerates spelling)
  3. new text    not a reworded copy of the seed, the nearest bank questions or other hardened
                 versions in the same run (quiz_generation._is_duplicate)
  4. grounding   the cited passage states the answer (quiz_generation._answer_supported), else 'ai'
  5. referee     the Answer-Key Referee on the finished question: contradicted, books_conflict or a
                 key the textbooks do not back is dropped; textbooks_silent is kept as AI knowledge,
                 provided something (a source or the referee) verified it

Which questions: a selection is split into buckets (each picked topic, else each sub-category, else
each category) and seeds are taken round-robin across them, so one big bank cannot crowd out the
rest. Inside a bucket, questions the student answered correctly come first (a harder version tests
what they think they know), then unseen ones, then the rest. "All" (nothing picked) is refused: a
harder set needs a focus.

Results are stored as MCQs in main_category 'Hardened MCQs' under a quiz_set_id, so a set appears in
Mock Builder -> Quiz History and can be practised while the rest is still being written.

Runs in the background. Each seed takes a slot of llm.AI_JOB_SLOTS (retrieval and the reranker are
CPU-bound) and holds a DB connection only while it reads or writes; the AI calls run with none. The
job reports every seed's stage, so the page can show progress.
"""

import json
import logging
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import numpy as np
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.llm import AI_JOB_SLOTS, chat_completion, llm_configured
from app.models import MCQ, AnswerEvent, MCQTag, User
from app.past_papers import answer_norm, answers_agree

logger = logging.getLogger(__name__)

CATEGORY = "Hardened MCQs"
CONTEXT_CHAR_BUDGET = 9000
ALLOWED_COUNTS = (5, 10, 15, 20)
MAX_BUCKETS = 6                # subjects/topics in one request: more is not a focused set
SIMILAR_EXISTING_LIMIT = 40
DROP_VERDICTS = {"contradicted", "books_conflict"}
MINUTES_PER_SEED = (1.0, 1.5)  # observed on the NAS with 2 slots; shown as the estimate

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


def _buckets(req: dict) -> list[tuple[str, str]]:
    """(axis, label) per bucket of the selection: topics, else sub-categories, else categories."""
    for axis in ("topics", "sub_categories", "categories"):
        if req.get(axis):
            return [(axis, str(v)) for v in dict.fromkeys(req[axis])]
    return []


def validate(req: dict) -> None:
    """Raise HardenError on a malformed harden request (shared by preview, sync and job paths)."""
    count = int(req.get("num_questions") or 0)
    if count not in ALLOWED_COUNTS:
        raise HardenError(f"num_questions must be a multiple of 5 between 5 and 20 (got {count}).")
    difficulty = int(req.get("difficulty") or 0)
    if difficulty not in DIFFICULTY_LINES:
        raise HardenError(f"difficulty must be 4 or 5 (got {difficulty}).")
    if not req.get("seed_ids"):
        buckets = _buckets(req)
        if not buckets:
            raise HardenError("Hardening needs a focus: pick 1-6 subjects or topics (not All).")
        if len(buckets) > MAX_BUCKETS:
            raise HardenError(f"Pick at most {MAX_BUCKETS} subjects or topics to harden (got {len(buckets)}).")
    if not llm_configured("chat"):
        raise HardenError("The AI service is not configured.")


def harden_set_id(request_id: str | None) -> str:
    if request_id:
        safe = "".join(c for c in request_id if c.isalnum() or c in "_-")[:40]
        if safe:
            return f"hardened_{safe}"
    return f"hardened_{uuid.uuid4().hex[:12]}"


def _base_query(db: Session, user: User):
    from app.retention import access_scope

    # Never harden a hardened question: it would compound rewrites and blind the dedup check.
    return access_scope(db.query(MCQ).filter(MCQ.status != "private"), user).filter(
        or_(MCQ.main_category.is_(None), MCQ.main_category != CATEGORY))


def _bucket_query(db: Session, user: User, req: dict, axis: str, label: str):
    """Questions of one bucket, inside the rest of the selection (a topic stays within the picked subjects)."""
    q = _base_query(db, user)
    if axis == "topics":
        q = q.filter(MCQ.topic == label)
        if req.get("sub_categories"):
            q = q.filter(MCQ.sub_category.in_(req["sub_categories"]))
        elif req.get("categories"):
            q = q.filter(MCQ.main_category.in_(req["categories"]))
    elif axis == "sub_categories":
        q = q.filter(MCQ.sub_category == label)
        if req.get("categories"):
            q = q.filter(MCQ.main_category.in_(req["categories"]))
    else:
        q = q.filter(MCQ.main_category == label)
    return q


def _pick_seeds(db: Session, user: User, req: dict) -> list[MCQ]:
    """The questions to harden: round-robin across the selection's buckets; inside a bucket, questions this
    student answered correctly first, then unseen ones, then the rest. One version per recalled question."""
    n = int(req["num_questions"])
    if req.get("seed_ids"):
        return _base_query(db, user).filter(MCQ.id.in_(req["seed_ids"])).limit(n).all()

    answered = (db.query(AnswerEvent.mcq_id, func.bool_or(AnswerEvent.is_correct).label("ok"))
                .filter(AnswerEvent.user_id == user.id, AnswerEvent.mcq_id.isnot(None))
                .group_by(AnswerEvent.mcq_id).subquery())
    rank = case((answered.c.ok.is_(True), 0), (answered.c.mcq_id.is_(None), 1), else_=2)
    queues = []
    for axis, label in _buckets(req):
        rows = (_bucket_query(db, user, req, axis, label).outerjoin(answered, answered.c.mcq_id == MCQ.id)
                .order_by(rank, func.random()).limit(n * 2 + 10).all())
        if rows:
            queues.append(rows)
    picked, taken = [], set()
    while len(picked) < n and any(queues):
        for queue in queues:
            while queue and len(picked) < n:
                m = queue.pop(0)
                key = m.recall_group if m.recall_group is not None else -m.id
                if key not in taken:
                    taken.add(key)
                    picked.append(m)
                    break
    return picked


def preview(db: Session, user: User, req: dict) -> dict[str, Any]:
    """How a request would be split across its buckets, and roughly how long it takes. No AI call."""
    validate(req)
    n = int(req["num_questions"])
    buckets = []
    for axis, label in _buckets(req):
        available = _bucket_query(db, user, req, axis, label).count()
        buckets.append({"label": label, "available": int(available), "picked": 0})
    left = n
    while left > 0 and any(b["picked"] < b["available"] for b in buckets):
        for b in buckets:
            if left > 0 and b["picked"] < b["available"]:
                b["picked"] += 1
                left -= 1
    total = sum(b["picked"] for b in buckets)
    per = max(1, settings.ai_job_workers)
    lo, hi = (round(total * m / per) for m in MINUTES_PER_SEED)
    return {"buckets": buckets, "total": total, "estimate_min": [max(1, lo), max(2, hi)]}


def _prompt(seed: dict, difficulty: int, context: str) -> list[dict]:
    rng = random.Random(seed["id"])   # a fixed letter per seed keeps keys balanced across runs
    key_letter = rng.choice("ABCDE")
    answer = seed["answer"]
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
        "- use the ORIGINAL correct answer text, word for word, as the correct option (only the stem "
        "  and the distractors change).\n"
        "- not reuse the original stem or distractor text.\n"
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
    opts = "\n".join(f"{k}. {v}" for k, v in sorted((seed["options"] or {}).items()))
    user = (
        f"ORIGINAL QUESTION (keep its fact and its answer):\n{seed['question_text']}\n{opts}\n"
        f"KEY: {seed['correct_option']}. {answer}\nCONCEPT: {seed['concept']}\n\n"
        f"Write exactly ONE harder rewrite ({difficulty}/5). Its correct option is option {key_letter} and its "
        f"text is exactly: \"{answer}\".\n\nTEXTBOOK PASSAGES:\n{context or 'NO TEXTBOOK PASSAGES FOUND.'}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _seed_values(seed: MCQ) -> dict[str, Any]:
    """Plain values of a seed, so a worker can let go of its DB connection while the AI works."""
    answer = str((seed.options or {}).get(seed.correct_option, ""))
    return {"id": seed.id, "question_text": seed.question_text, "options": dict(seed.options or {}),
            "correct_option": seed.correct_option, "answer": answer,
            "concept": seed.tested_concept or seed.topic or answer, "topic": seed.topic,
            "sub_category": seed.sub_category, "access": seed.access, "concept_id": seed.concept_id,
            "book_id": seed.book_id}


def _passages(db: Session, seed: dict) -> list[dict]:
    """Textbook passages for the seed, as plain dicts (title, page, content, chunk ids, book id)."""
    from app.retrieval import retrieval_service

    blocks, seen, size = [], set(), 0
    for q in (f"{seed['concept']} {seed['answer']}", f"{' '.join(seed['question_text'].split())[:300]} {seed['answer']}"):
        for c in retrieval_service.search(db, q[:600], limit=6).context:
            if c.id in seen or size + len(c.content or "") > CONTEXT_CHAR_BUDGET:
                continue
            seen.add(c.id)
            size += len(c.content or "")
            blocks.append({"title": c.book.title if c.book else "Textbook", "page": c.page_number,
                           "content": c.content or "", "chunk_ids": c.chunk_ids, "book_id": c.book_id})
    return blocks


def _runner(seed: dict, req: dict, set_id: str, title: str, ctx: dict, stage: Callable[[int, str, str | None], None]):
    """One worker: write + check + persist ONE harder rewrite of ONE seed. Holds an AI slot throughout and a DB
    connection only while reading or writing."""
    from app.database import SessionLocal
    from app.quiz_generation import _answer_supported, _embed, _is_duplicate, _normalise_question
    from app.referee import judge

    sid = seed["id"]
    with AI_JOB_SLOTS:
        s = SessionLocal()
        try:
            stage(sid, "searching", None)
            blocks = _passages(s, seed)
            s.commit()   # end the transaction: the connection goes back to the pool while the AI writes
            context = "\n\n".join(f"Chunk {i}: [{b['title']}, Page {b['page']}]\n{b['content']}"
                                  for i, b in enumerate(blocks, 1))

            stage(sid, "writing", None)
            raw = chat_completion(_prompt(seed, req["difficulty"], context), json_mode=True,
                                  temperature=0.5, max_tokens=2400, label=f"harden seed {sid}", role="chat")
            q = _normalise_question(json.loads(raw) or {}, list("ABCDE"))
            if q is None or sorted(q["options"]) != list("ABCDE"):
                return None, "shape"
            # The rewrite must keep the tested answer (that is the whole point).
            if not answers_agree(answer_norm(q["options"][q["correct_option"]]), answer_norm(seed["answer"])):
                return None, "answer changed"

            # Not a reworded copy of the seed, the nearest bank questions or this run's other rewrites.
            v = _embed([q["question_text"]])[0]
            with ctx["lock"]:
                if _is_duplicate(q, v, ctx["pool_vecs"], ctx["pool_answers"], ctx["pool_stems"], ctx["kept"]):
                    return None, "duplicate"
                ctx["kept"].append((q, v))

            # Grounding: the cited passage must state the answer, else the question is AI knowledge.
            source = None
            try:
                if q.get("source_chunk") is not None and 1 <= int(q["source_chunk"]) <= len(blocks):
                    source = blocks[int(q["source_chunk"]) - 1]
            except (TypeError, ValueError):
                source = None
            if source is not None and not _answer_supported(q["options"][q["correct_option"]], source["content"]):
                logger.info("Dropping unsupported source on hardened rewrite of %s", sid)
                source = None

            stage(sid, "refereeing", None)
            try:
                verdict = judge(s, q["question_text"], options=q["options"], key=q["correct_option"])
            except Exception as e:
                logger.warning("Referee failed for hardened rewrite of %s: %s", sid, e)
                verdict = {"verdict": None}
            s.commit()
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
                            f"\"{' '.join(seed['question_text'].split())[:140]}\" (answer unchanged: {seed['answer']})")
            mcq = MCQ(
                book_id=source["book_id"] if source else seed["book_id"],
                quiz_set_id=set_id, quiz_set_title=title,
                question_text=q["question_text"], options=q["options"], correct_option=q["correct_option"],
                topic=seed["topic"], main_category=CATEGORY, sub_category=seed["sub_category"],
                tested_concept=(str(q.get("tested_concept") or "").strip()[:200] or seed["concept"][:200]),
                explanation_markdown=explanation, status="ready", access=seed["access"],
                grounding="book" if source is not None else "ai",
                stem_embedding=v.tolist(), source_chunk_ids=source["chunk_ids"] if source else [],
                difficulty=req["difficulty"], concept_id=seed["concept_id"],
            )
            s.add(mcq)
            s.flush()
            s.add(MCQTag(mcq_id=mcq.id, axis="hardened", label=str(sid)))
            s.commit()
            return mcq.id, None
        except Exception:
            logger.exception("Harden worker for seed %s failed", sid)
            s.rollback()
            return None, "error"
        finally:
            s.close()


def harden_set(db: Session, user: User, req: dict,
               progress: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """Write, gate and store harder rewrites of the requested questions. Idempotent per request_id.
    progress(state) is called whenever a seed changes stage."""
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

    # One shared duplication pool: the nearest bank questions to the first seeds plus every seed,
    # so rewrites never repeat a seed or the bank.
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
    values = [_seed_values(s) for s in seeds]
    db.commit()   # nothing more to read here: release this connection for the length of the job

    label = (req.get("label") or ", ".join(req.get("sub_categories") or req.get("categories") or []) or "practice")
    title = f"Hardened · {label[:40].title()}"

    state = {"total": len(values), "done": 0, "kept": 0, "dropped": {}, "items": [
        {"seed_id": v["id"], "label": v["sub_category"] or v["topic"] or "",
         "stem": " ".join((v["question_text"] or "").split())[:110], "stage": "queued", "reason": None}
        for v in values]}
    lock = threading.Lock()
    by_id = {it["seed_id"]: it for it in state["items"]}

    def publish() -> None:
        if progress:
            progress(json.loads(json.dumps(state)))

    def stage(seed_id: int, name: str, reason: str | None) -> None:
        with lock:
            by_id[seed_id]["stage"], by_id[seed_id]["reason"] = name, reason
            publish()

    publish()
    with ThreadPoolExecutor(max_workers=max(1, min(settings.ai_job_workers, len(values)))) as pool:
        futures = {pool.submit(_runner, v, req, set_id, title, ctx, stage): v["id"] for v in values}
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                mcq_id, why = fut.result()
            except Exception as e:
                logger.warning("Harden worker crashed: %s", e)
                mcq_id, why = None, "error"
            with lock:
                state["done"] += 1
                if mcq_id is None:
                    state["dropped"][why or "error"] = state["dropped"].get(why or "error", 0) + 1
                    by_id[sid]["stage"], by_id[sid]["reason"] = "dropped", why or "error"
                else:
                    state["kept"] += 1
                    by_id[sid]["stage"], by_id[sid]["reason"] = "kept", None
                publish()

    created = db.query(MCQ).filter(MCQ.quiz_set_id == set_id).order_by(MCQ.id).all()
    logger.info("Harden %s (%s): %d kept of %d seeds (dropped %s)", set_id, title,
                state["kept"], len(values), state["dropped"] or "none")
    if not created:
        raise HardenError("None of the rewrites passed the checks" + (
            f" ({', '.join(f'{k}: {v}' for k, v in state['dropped'].items())})" if state["dropped"] else "") + ".")
    return serialize_quiz_set(created, set_id, sum(state["dropped"].values()), 0, req["difficulty"])


# ─── Background jobs (same pattern as app/quiz_generation.py) ─────────────────

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
JOB_RETENTION_S = 3600


def _prune_jobs() -> None:
    cutoff = time.time() - JOB_RETENTION_S
    for key in [k for k, j in _jobs.items() if j.get("finished_at") and j["finished_at"] < cutoff]:
        _jobs.pop(key, None)


def get_harden_job(db: Session, job_id: str, user: User) -> dict[str, Any] | None:
    """Job state for its owner: status, per-seed progress, and the result when done. None for anyone else.
    A job the server no longer remembers (a restart) is answered from the set saved so far, marked partial."""
    with _jobs_lock:
        job = dict(_jobs[job_id]) if job_id in _jobs else None
    if job is not None:
        if job.get("user_id") != user.id:
            return None
        out: dict[str, Any] = {"job_id": job_id, "status": job["status"], "progress": job.get("progress")}
        if job["status"] == "running":
            out["elapsed_s"] = round(time.time() - job["started_at"], 1)
        elif job["status"] == "done":
            out["result"] = job["result"]
        else:
            out["detail"] = job.get("detail")
            out["status_code"] = job.get("status_code")
        return out
    from app.retention import access_scope

    mcqs = access_scope(db.query(MCQ).filter(MCQ.quiz_set_id == job_id, MCQ.main_category == CATEGORY),
                        user).order_by(MCQ.id).all()
    if mcqs:
        from app.quiz_generation import serialize_quiz_set

        return {"job_id": job_id, "status": "done", "partial": True,
                "result": serialize_quiz_set(mcqs, job_id, 0, 0, mcqs[0].difficulty)}
    return None


def start_harden_job(user: User, req: dict) -> dict[str, Any]:
    try:
        validate(req)
    except HardenError as e:
        return {"error": str(e)}

    if req.get("seed_ids"):
        from app.database import SessionLocal

        wanted = [int(i) for i in req["seed_ids"]][:20]
        s = SessionLocal()
        try:
            visible = [r[0] for r in _base_query(s, user).with_entities(MCQ.id).filter(MCQ.id.in_(wanted)).all()]
        finally:
            s.close()
        if not visible:
            return {"error": "None of those questions exist or are visible to you."}
        req = {**req, "seed_ids": visible}

    request_id = req.get("request_id") or uuid.uuid4().hex
    req = {**req, "request_id": request_id}
    if not req.get("label"):
        req["label"] = ", ".join(req.get("topics") or req.get("sub_categories") or req.get("categories") or [])
    job_id = harden_set_id(request_id)
    user_id = user.id

    with _jobs_lock:
        _prune_jobs()
        existing = _jobs.get(job_id)
        if existing:
            return {"job_id": job_id, "status": existing["status"]}
        _jobs[job_id] = {"status": "running", "started_at": time.time(), "user_id": user_id, "progress": None}

    def progress(state: dict) -> None:
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["progress"] = state

    def run() -> None:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            owner = db.get(User, user_id)   # this thread's own copy, not the request's (closed) session object
            result = harden_set(db, owner, req, progress)
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
