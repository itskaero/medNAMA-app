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
from sqlalchemy import Integer, case, cast, func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.llm import AI_JOB_SLOTS, chat_completion, llm_configured
from app.models import MCQ, AnswerEvent, MCQTag, QuizAttempt, User
from app.past_papers import answer_norm, answers_agree

logger = logging.getLogger(__name__)

CATEGORY = "Hardened MCQs"
CONTEXT_CHAR_BUDGET = 9000
MAX_COUNT = 50                 # Mock Builder offers hardening for sessions of up to 50 questions
MAX_BUCKETS = 20               # subjects/topics in one request (a whole category ticks all its subtopics)
SIMILAR_EXISTING_LIMIT = 40
DROP_VERDICTS = {"contradicted", "books_conflict"}
MINUTES_PER_SEED = (1.0, 2.0)  # per question per AI slot on the NAS since one fast search per question
                               # (was 3-4 min with three searches, AI query rewrites and the MedCPT reranker)

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
    """(axis, label) per bucket of the selection: a Practice scope's subject|topic groups, else topics, else
    sub-categories, else categories."""
    if req.get("scope"):
        from app.practice_scope import buckets
        return [("scope", f"{s}|{t}" if t else s) for s, t in buckets(req["scope"])]
    for axis in ("topics", "sub_categories", "categories"):
        if req.get(axis):
            return [(axis, str(v)) for v in dict.fromkeys(req[axis])]
    return []


def validate(req: dict) -> None:
    """Raise HardenError on a malformed harden request (shared by preview, sync and job paths)."""
    count = int(req.get("num_questions") or 0)
    if not 1 <= count <= MAX_COUNT:
        raise HardenError(f"Hardening works on 1 to {MAX_COUNT} questions (got {count}).")
    difficulty = int(req.get("difficulty") or 0)
    if difficulty not in DIFFICULTY_LINES:
        raise HardenError(f"difficulty must be 4 or 5 (got {difficulty}).")
    if not req.get("seed_ids"):
        buckets = _buckets(req)
        if not buckets:
            raise HardenError("Hardening needs a focus: pick a category, subjects or topics (not Mixed Practice / All).")
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
    if axis == "scope":
        from app.practice_scope import restrict
        s, _, t = label.partition("|")
        return restrict(db, q, req["scope"], (s, t or None))
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


def _pick_seeds(db: Session, user: User, req: dict, limit: int | None = None) -> list[MCQ]:
    """The questions to harden: round-robin across the selection's buckets; inside a bucket, questions this
    student answered correctly first, then unseen ones, then the rest. One version per recalled question.
    limit > num_questions returns replacements too, in the same order (harden_set's reserve)."""
    n = int(limit or req["num_questions"])
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


def _ready_made(db: Session, user: User, req: dict, n: int) -> list[tuple[int, int]]:
    """Harder versions already written (by an earlier request or the PC stock batch, scripts/prewarm_harder.py)
    for questions in this selection, at this difficulty, that this student has neither answered nor been shown.
    Round-robin across the selection's buckets, one per original question. Returns [(hardened id, seed id)]."""
    seen = db.query(AnswerEvent.mcq_id).filter(AnswerEvent.user_id == user.id, AnswerEvent.mcq_id.isnot(None))
    served = db.query(cast(func.jsonb_array_elements_text(QuizAttempt.mcq_ids), Integer)).filter(
        QuizAttempt.user_id == user.id, QuizAttempt.mcq_ids.isnot(None))
    queues = []
    for axis, label in _buckets(req):
        seeds = _bucket_query(db, user, req, axis, label).with_entities(MCQ.id).subquery()
        rows = (db.query(MCQ.id, cast(MCQTag.label, Integer))
                .join(MCQTag, MCQTag.mcq_id == MCQ.id)
                .filter(MCQTag.axis == "hardened", MCQ.main_category == CATEGORY, MCQ.status == "ready",
                        MCQ.difficulty == req["difficulty"], cast(MCQTag.label, Integer).in_(db.query(seeds.c.id)),
                        MCQ.id.notin_(seen), MCQ.id.notin_(served))
                .order_by(func.random()).limit(n * 2).all())
        if rows:
            queues.append(list(rows))
    out, used = [], set()
    while len(out) < n and any(queues):
        for queue in queues:
            while queue and len(out) < n:
                hid, sid = queue.pop(0)
                if sid not in used:
                    used.add(sid)
                    out.append((hid, sid))
                    break
    return out


def _stocked_seeds(db: Session, difficulty: int) -> set[int]:
    """Originals that already have a harder version at this difficulty (the stock batch skips them)."""
    return {int(sid) for (sid,) in db.query(MCQTag.label).join(MCQ, MCQ.id == MCQTag.mcq_id).filter(
        MCQTag.axis == "hardened", MCQ.difficulty == difficulty, MCQ.status == "ready")}


def preview(db: Session, user: User, req: dict) -> dict[str, Any]:
    """How a request would be split across its buckets, and roughly how long it takes. No AI call."""
    validate(req)
    n = int(req["num_questions"])
    buckets = []
    for axis, label in _buckets(req):
        available = _bucket_query(db, user, req, axis, label).count()
        buckets.append({"label": label.partition("|")[2] or label if axis == "scope" else label,
                        "available": int(available), "picked": 0})
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
    """Textbook passages for the seed, as plain dicts. One search (no AI query rewrite, fast reranker only):
    the same passages serve the rewrite and the Referee, which used to search twice more per question."""
    from app.retrieval import retrieval_service

    q = f"{seed['concept']} {seed['answer']} {' '.join(seed['question_text'].split())[:300]}"
    blocks, size = [], 0
    for c in retrieval_service.search(db, q[:600], limit=8, rewrite=False, deep_rerank=False).context:
        if size + len(c.content or "") > CONTEXT_CHAR_BUDGET:
            continue
        size += len(c.content or "")
        blocks.append({"id": c.id, "title": c.book.title if c.book else "Textbook", "page": c.page_number,
                       "content": c.content or "", "chunk_ids": c.chunk_ids, "book_id": c.book_id})
    return blocks


def _is_harder_duplicate(q: dict, v: np.ndarray, ctx: dict) -> bool:
    """A harder version keeps its original's answer on purpose, so against the original, its other archive's
    version and the bank only a near-copy counts (statement cosine or text); "similar statement + same answer"
    still applies between the rewrites of one run."""
    from app.quiz_generation import STEM_DUP_COSINE, _is_duplicate, _is_text_duplicate

    if len(ctx["pool_vecs"]):
        if (np.asarray(ctx["pool_vecs"]) @ v > STEM_DUP_COSINE).any():
            return True
    if _is_text_duplicate(q["question_text"], ctx["pool_stems"]):
        return True
    return bool(ctx["kept"]) and _is_duplicate(q, v, np.zeros((0, len(v)), dtype=np.float32), [], [], ctx["kept"])


RETRY_HINT = {
    "shape": "Your last answer was not valid JSON with five options A-E and one correct_option. Follow the format exactly.",
    "answer changed": "Your last version changed the correct answer. The correct option text must be exactly: \"{answer}\".",
    "duplicate": ("Your last version was too close to the original question. Change the scenario, the presentation "
                  "and the wording much more (keep the fact and the exact answer text \"{answer}\")."),
}


def _flag_disputed_key(s: Session, seed: dict, verdict: dict, source: str = "Harder versions") -> None:
    """The textbooks contradict a rewrite whose key is the original's answer: the original's key is suspect.
    Tag it and put it in the admin's Reports queue (once)."""
    from app.models import AnswerReport

    if not s.query(MCQTag).filter_by(mcq_id=seed["id"], axis="flag", label="key-conflict").first():
        s.add(MCQTag(mcq_id=seed["id"], axis="flag", label="key-conflict"))
    if not s.query(AnswerReport).filter(AnswerReport.mcq_id == seed["id"], AnswerReport.status == "open",
                                        AnswerReport.reason.like(f"{source}:%")).first():
        admin = s.query(User).filter(User.role == "admin").order_by(User.id).first()
        if admin is not None:
            why = (verdict.get("explanation") or "").strip()[:600]
            s.add(AnswerReport(user_id=admin.id, kind="mcq", mcq_id=seed["id"],
                               question=seed["question_text"][:2000], answer_excerpt=seed["answer"][:500],
                               reason=f"{source}: the textbooks {verdict.get('verdict')} this key "
                                      f"({seed['answer']}). {why}"))
    s.commit()


def _runner(seed: dict, req: dict, set_id: str, title: str, ctx: dict, stage: Callable[[str, str | None], None]):
    """One question: search once, write (one retry with feedback on a fixable failure), check, Referee on the same
    passages, save. Holds an AI slot throughout and a DB connection only while reading or writing.
    Returns (mcq_id, None) or (None, reason)."""
    from app.database import SessionLocal
    from app.quiz_generation import _answer_supported, _embed, _normalise_question
    from app.referee import judge

    sid = seed["id"]
    with AI_JOB_SLOTS:
        s = SessionLocal()
        try:
            stage("searching", None)
            blocks = _passages(s, seed)
            s.commit()   # end the transaction: the connection goes back to the pool while the AI writes
            context = "\n\n".join(f"Chunk {i}: [{b['title']}, Page {b['page']}]\n{b['content']}"
                                  for i, b in enumerate(blocks, 1))

            q = v = None
            why = None
            for attempt in (1, 2):
                stage("writing" if attempt == 1 else "retrying", why)
                messages = _prompt(seed, req["difficulty"], context)
                if why in RETRY_HINT:
                    messages.append({"role": "user", "content": RETRY_HINT[why].format(answer=seed["answer"])})
                raw = chat_completion(messages, json_mode=True, temperature=0.5 if attempt == 1 else 0.7,
                                      max_tokens=2400, label=f"harden seed {sid}", role="chat")
                try:
                    q = _normalise_question(json.loads(raw) or {}, list("ABCDE"))
                except json.JSONDecodeError:
                    q = None
                if q is None or sorted(q["options"]) != list("ABCDE"):
                    why = "shape"
                    continue
                # The rewrite must keep the tested answer (that is the whole point).
                if not answers_agree(answer_norm(q["options"][q["correct_option"]]), answer_norm(seed["answer"])):
                    why = "answer changed"
                    continue
                v = _embed([q["question_text"]])[0]
                with ctx["lock"]:
                    if _is_harder_duplicate(q, v, ctx):
                        why = "duplicate"
                        continue
                    ctx["kept"].append((q, v))
                why = None
                break
            if why:
                return None, why

            # Grounding: the cited passage must state the answer, else the question is AI knowledge.
            source = None
            try:
                if q.get("source_chunk") is not None and 1 <= int(q["source_chunk"]) <= len(blocks):
                    source = blocks[int(q["source_chunk"]) - 1]
            except (TypeError, ValueError):
                source = None
            if source is not None and not _answer_supported(q["options"][q["correct_option"]], source["content"]):
                source = None

            stage("refereeing", None)
            try:
                verdict = judge(s, q["question_text"], options=q["options"], key=q["correct_option"],
                                passages=[{"id": b["id"], "title": b["title"], "page": b["page"],
                                           "content": b["content"]} for b in blocks])
            except Exception as e:
                logger.warning("Referee failed for hardened rewrite of %s: %s", sid, e)
                verdict = {"verdict": None}
            s.commit()
            v_name = verdict.get("verdict")
            if v_name in DROP_VERDICTS or (v_name == "supported" and verdict.get("agrees_with_key") is False):
                # Same answer as the original: the textbooks dispute the original's key too.
                try:
                    _flag_disputed_key(s, seed, verdict)
                except Exception:
                    logger.exception("Could not flag disputed key of %s", sid)
                    s.rollback()
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
    progress(state) is called whenever a question changes stage.

    Fail-safe: the job fills N slots. A question that fails its checks is replaced by the next one from the
    selection (at most 2N+5 tried in all); a slot still empty at the end gets one of the original questions,
    as written, so the session always has N questions. Result: the set plus fill_ids (the originals)."""
    from app.quiz_generation import _similar_existing, serialize_quiz_set

    validate(req)
    req = {**req, "num_questions": int(req["num_questions"]), "difficulty": int(req["difficulty"])}
    n = req["num_questions"]
    set_id = harden_set_id(req.get("request_id"))

    existing = db.query(MCQ).filter(MCQ.quiz_set_id == set_id).order_by(MCQ.id).all()
    if existing:
        logger.info("Harden request %s already completed; returning stored set", req.get("request_id"))
        return serialize_quiz_set(existing, set_id, 0, 0, req["difficulty"])

    # Instant part: harder versions already written for this selection. Only the rest is written now.
    # req["stock"] (the PC batch) skips this and writes versions for originals that have none yet.
    ready = [] if req.get("stock") else _ready_made(db, user, req, n)
    skip = _stocked_seeds(db, req["difficulty"]) if req.get("stock") else {sid for _, sid in ready}
    n_new = n - len(ready)
    seeds = [s for s in _pick_seeds(db, user, req, limit=2 * n + 5 + len(skip)) if s.id not in skip] if n_new else []
    seeds = seeds[: 2 * n_new + 5]
    if not seeds and not ready:
        raise HardenError("No questions match these filters to harden.")
    ready_ids = [hid for hid, _ in ready]
    if not seeds:   # the whole set was ready-made
        state = {"total": n, "done": n, "kept": 0, "ready": len(ready), "as_written": 0, "tried": 0, "dropped": {},
                 "items": [{"n": i, "label": "ready-made", "stage": "ready", "reason": None, "attempt": 1}
                           for i in range(1, len(ready) + 1)]}
        if progress:
            progress(state)
        out = serialize_quiz_set([], set_id, 0, 0, req["difficulty"])
        out.update(fill_ids=ready_ids, harder=len(ready), ready=len(ready), as_written=0)
        logger.info("Harden %s: all %d from ready-made harder versions", set_id, len(ready))
        return out

    # One shared duplication pool: the nearest bank questions to the first seeds plus every seed.
    ctx: dict = {"lock": threading.Lock(), "kept": [], "pool_vecs": [], "pool_answers": [], "pool_stems": []}
    for seed in seeds[:n]:
        vec = np.asarray(seed.stem_embedding, dtype=np.float32) if seed.stem_embedding is not None else None
        if vec is not None and len(ctx["pool_vecs"]) < SIMILAR_EXISTING_LIMIT * 4:
            try:
                near = _similar_existing(db, vec, limit=SIMILAR_EXISTING_LIMIT)
                ctx["pool_vecs"].extend(n_["vec"] for n_ in near)
                ctx["pool_stems"].extend(n_["stem"] for n_ in near)
            except Exception:
                logger.warning("Similarity lookup failed for seed %s; dedup pool kept small", seed.id)
    for seed in seeds:
        ctx["pool_stems"].append(seed.question_text or "")
    values = [_seed_values(s) for s in seeds]
    db.commit()   # nothing more to read here: release this connection for the length of the job

    label = (req.get("label") or ", ".join(req.get("sub_categories") or req.get("categories") or []) or "practice")
    title = f"Hardened · {label[:40].title()}"

    def tag(v: dict) -> str:
        return " · ".join(dict.fromkeys(x for x in (v["sub_category"], v["topic"]) if x))

    # Progress names each slot by its number, subject and topic only: showing the original statement would
    # spoil the harder version, which keeps the same correct answer.
    slots = min(n_new, len(values))
    state = {"total": slots + len(ready), "done": len(ready), "kept": 0, "ready": len(ready), "as_written": 0,
             "tried": 0, "dropped": {}, "items": [
        {"n": i, "label": tag(values[i - 1]), "stage": "queued", "reason": None, "attempt": 1}
        for i in range(1, slots + 1)]}
    lock = threading.Lock()
    reserve = list(values[slots:])      # replacements, in the selection's round-robin order
    failed: list[dict] = []             # originals whose rewrite failed (not for disputed keys)
    fill_ids: list[int] = []

    def publish() -> None:
        if progress:
            progress(json.loads(json.dumps(state)))

    def work(slot: int) -> None:
        item = state["items"][slot - 1]
        seed = values[slot - 1]
        while True:
            def stage(name: str, reason: str | None, _it=item) -> None:
                with lock:
                    _it["stage"], _it["reason"] = name, reason
                    publish()
            try:
                mcq_id, why = _runner(seed, req, set_id, title, ctx, stage)
            except Exception as e:
                logger.warning("Harden worker crashed: %s", e)
                mcq_id, why = None, "error"
            with lock:
                state["tried"] += 1
                if mcq_id is not None:
                    state["kept"] += 1
                    state["done"] += 1
                    item["stage"], item["reason"] = "kept", None
                    publish()
                    return
                state["dropped"][why or "error"] = state["dropped"].get(why or "error", 0) + 1
                if not (why or "").startswith("referee"):
                    failed.append(seed)
                nxt = reserve.pop(0) if reserve else None
                if nxt is not None:
                    seed = nxt
                    item["attempt"] += 1
                    item["label"] = tag(seed)
                    item["stage"], item["reason"] = "replacing", why
                    publish()
                    continue
                # Out of replacements: the original question, as written.
                spare = next((f for f in failed if f["id"] not in fill_ids), None)
                if spare is not None:
                    fill_ids.append(spare["id"])
                    state["as_written"] += 1
                    item["stage"], item["reason"] = "as written", why
                else:
                    item["stage"], item["reason"] = "dropped", why
                state["done"] += 1
                publish()
                return

    publish()
    with ThreadPoolExecutor(max_workers=max(1, min(settings.ai_job_workers, slots))) as pool:
        list(pool.map(work, range(1, slots + 1)))

    created = db.query(MCQ).filter(MCQ.quiz_set_id == set_id).order_by(MCQ.id).all()
    logger.info("Harden %s (%s): %d ready-made + %d harder + %d as written of %d slots; %d tried (dropped %s)",
                set_id, title, len(ready), state["kept"], state["as_written"], slots + len(ready), state["tried"],
                state["dropped"] or "none")
    if not created and not fill_ids and not ready_ids:
        raise HardenError("None of the rewrites passed the checks" + (
            f" ({', '.join(f'{k}: {v}' for k, v in state['dropped'].items())})" if state["dropped"] else "") + ".")
    out = serialize_quiz_set(created, set_id, sum(state["dropped"].values()), 0, req["difficulty"])
    out["fill_ids"] = ready_ids + fill_ids     # the session adds these to the set (include_ids)
    out["harder"] = state["kept"] + len(ready)
    out["ready"] = len(ready)
    out["as_written"] = len(fill_ids)
    return out


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

        wanted = [int(i) for i in req["seed_ids"]][:MAX_COUNT]
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
