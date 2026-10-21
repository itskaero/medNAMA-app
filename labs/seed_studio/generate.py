"""One batch of work: N questions on one TOS unit at one difficulty, or one revision note for one leaf.

Reuses medNAMA's pipeline rather than copying it: retrieval_service.search (hybrid search, rerank, programme bias),
the embedding model, the bank-wide duplicate check (_similar_existing / _is_duplicate) and the answer-support check
(_answer_supported) from app/quiz_generation.py, and app/revision.build for notes (its reference validation strips
any citation not in the retrieved pages).

What is new here is the record: every candidate question becomes a seed_items row, kept or not, with the reason.
The save is one transaction (questions + items + batch done), so an interruption leaves either the whole batch
or nothing, and a batch that is already done is never written twice.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Callable

import numpy as np
from sqlalchemy import text

from .common import PermanentError, TransientError, llm_json

from app import exam_levels  # noqa: E402
from app.models import MCQ, Book, MCQTag, User  # noqa: E402
from app.seed_models import SeedBatch, SeedItem, SeedJob, SeedNote, SeedTosDocument, SeedTosNode  # noqa: E402

logger = logging.getLogger("seed_studio.generate")

DIFFICULTY_TEXT = {
    1: "1/5: simple recall of one fact",
    2: "2/5: straightforward application of one fact",
    3: "3/5: standard exam style with plausible distractors",
    4: "4/5: multi-step clinical or mechanistic reasoning",
    5: "5/5: subtle distractors needing integration of several facts",
}


def programme_for(doc: SeedTosDocument) -> str:
    return exam_levels.PGET if doc.family == "PGET" else exam_levels.FCPS_1


def lineage(db, node: SeedTosNode) -> dict[str, str]:
    """{'paper': ..., 'specialty': ..., 'subject': ..., 'topic': ...} for a node."""
    out: dict[str, str] = {}
    n = node
    while n is not None:
        if n.level != "family":
            out.setdefault(n.level, n.name)
        n = db.get(SeedTosNode, n.parent_id) if n.parent_id else None
    return out


# ─── which books a unit is written from ──────────────────────────────────────────────────────────

# TOS subject / specialty wording -> books.subject. Basic sciences are matched on the subject first, so PGET Surgery's
# "Anatomy" goes to the anatomy books and not to Bailey & Love; a clinical subject ("Hematologic Disorders") is not a
# basic science even though it names one, so it falls through to its specialty (Medicine).
BASIC_SUBJECTS = (
    (r"physiolog", "Physiology"),
    (r"pharmacolog", "Pharmacology"),
    (r"chemical patholog|biochem", "Biochemistry"),
    (r"anatom|histolog|embryolog", "Anatomy"),
    (r"microbio", "Microbiology"),
    (r"patholog|haematolog|hematolog|immunolog|immunopath", "Pathology"),
)
CLINICAL = (
    (r"otorhino|\bent\b|\bear\b|larynx|pharynx|\bnose\b", "ENT"),
    (r"ophthalm", "Ophthalmology"),
    (r"paediatr|pediatr|neonat", "Paediatrics"),
    (r"obstet|gynae|gyneco", "Gynaecology & Obstetrics"),
    (r"surg", "Surgery"),
    (r"medicine|cardio|gastro|endocrin|hepatolog|nephro|rheumat|neurolog|pulmon|respirat|dermatolog", "Medicine"),
)
NOT_BASIC = re.compile(r"disorder|disease|clinical|infections", re.I)


def subject_family(line: dict[str, str]) -> str | None:
    """The books.subject a unit's own textbooks carry, from its TOS lineage; None = no subject books (behavioural
    science, dentistry, radiology...): the whole library is searched, as before."""
    subject, specialty = line.get("subject") or "", line.get("specialty") or ""
    if subject and not NOT_BASIC.search(subject):
        for pattern, family in BASIC_SUBJECTS:
            if re.search(pattern, subject, re.I):
                return family
    for name in (specialty, subject):
        for pattern, family in CLINICAL + BASIC_SUBJECTS:
            if name and re.search(pattern, name, re.I):
                return family
    return None


def primary_books(db, family: str | None) -> list[int]:
    if not family:
        return []
    return [i for (i,) in db.query(Book.id).filter(Book.subject == family, Book.status == "ready").order_by(Book.id)]


def _search(db, query: str, *, limit: int, programme: str, book_ids: list[int] | None = None,
            exclude: set[int] | None = None) -> list:
    from app.retrieval import retrieval_service

    try:
        return retrieval_service.search(db, query[:500], limit=limit, level=programme, book_ids=book_ids or None,
                                        exclude_chunk_ids=exclude or None).context
    except Exception as e:
        db.rollback()
        raise TransientError(f"retrieval failed: {e}")


def subject_first(db, query: str, *, limit: int, programme: str, primary: list[int],
                  exclude: set[int] | None = None, enough: int = 2) -> list:
    """Passages from the unit's own subject books first; the rest of the library only tops up when those books
    return fewer than `enough` passages (retrieval's score floor means a short list is a real gap)."""
    found = _search(db, query, limit=limit, programme=programme, book_ids=primary, exclude=exclude) if primary else []
    if len(found) < enough:
        have = {c.id for c in found}
        for c in _search(db, query, limit=limit, programme=programme, exclude=exclude):
            if c.id not in have and len(found) < limit:
                found.append(c)
    return found


def evidence_count(db, query: str, cap: int = 20) -> int:
    """Quick full-text probe (no model): how many passages mention the unit's name. 0 = likely no source."""
    try:
        return int(db.execute(text(
            "SELECT count(*) FROM (SELECT 1 FROM chunks WHERE parent_id IS NOT NULL AND "
            "to_tsvector('english', content) @@ plainto_tsquery('english', :q) LIMIT :cap) t"),
            {"q": query, "cap": cap}).scalar() or 0)
    except Exception:
        db.rollback()
        return -1


# ─── MCQs ─────────────────────────────────────────────────────────────────────────────────────────

SYSTEM = """You write single-best-answer MCQs for {programme} candidates ({programme_label}).
{mcq_guidance}
Table of Specifications position: {lineage}.
Write ONLY about this TOS unit. Spread questions across its sub-topics; give each question's sub-topic exactly as listed.
TARGET DIFFICULTY {difficulty}. Every question at this level.
GROUNDING: test facts stated in the TEXTBOOK PASSAGES and set "source_chunk" to the number of the passage that states
the keyed answer. Never invent a book, a page or a fact the passages do not support; if the passages cannot support
a good question at this difficulty, write fewer questions rather than unsupported ones.
Each question must test a different fact from the others and from the ALREADY IN THE BANK list.
Return JSON only:
{{"questions": [{{"question_text": "...", "options": {{{options_example}}}, "correct_option": "{first_key}",
  "explanation": "why the key is right (2-4 sentences)",
  "distractor_rationales": {{{rationale_example}}},
  "learning_objective": "what a candidate should be able to do, one line",
  "subtopic": "one of the listed sub-topics, or null",
  "tested_concept": "topic - specific fact tested (max 8 words)",
  "source_chunk": 1}}]}}"""


def _context(db, query: str, programme: str, exclude: set[int], primary: list[int] | None = None) -> list:
    """Passages for one MCQ batch: the unit's own subject books first, the library only to top up."""
    return subject_first(db, query, limit=6, programme=programme, primary=primary or [], exclude=exclude, enough=4)


def _unit_history(db, unit_id: int) -> tuple[list[str], set[int]]:
    """Stems already kept for this unit (any job) and the passages they came from."""
    stems, chunks = [], set()
    for (payload,) in db.query(SeedItem.payload).filter(SeedItem.node_id == unit_id,
                                                        SeedItem.status.in_(("validated", "inserted"))).limit(400):
        stems.append(f"- {str(payload.get('question_text', ''))[:150]} -> {str(payload.get('answer', ''))[:50]}")
        for c in payload.get("citations") or []:
            if c.get("chunk_id"):
                chunks.add(int(c["chunk_id"]))
    return stems, chunks


def run_mcq_batch(db, batch: SeedBatch, job: SeedJob, stage: Callable[[str, dict | None], None]) -> dict[str, Any]:
    """Generate, validate, de-duplicate and save one batch. Returns counts and token usage.

    `stage(name, progress)` records the live stage (committed separately, for the monitor). Raises TransientError /
    PermanentError for the retry logic; raises nothing after the save has committed."""
    from app.quiz_generation import (CONCEPT_DUP_COSINE, SAME_ANSWER_DUP_COSINE, STEM_DUP_COSINE,
                                     _answer_supported, _concept_text, _embed, _is_text_duplicate,
                                     _similar_existing)

    cfg = job.config or {}
    unit = db.get(SeedTosNode, batch.node_id)
    doc = db.get(SeedTosDocument, unit.document_id)
    programme = programme_for(doc)
    line = lineage(db, unit)
    n_options = 5 if int(cfg.get("options", 5)) == 5 else 4
    keys = "ABCDE"[:n_options]
    from . import targets as T
    from .progress import node_dicts
    nodes_d = node_dicts(db, doc.id)
    subtopics = T.subtopics_of(nodes_d, unit.id)
    details = {n["name"]: n["detail"] for n in nodes_d if n.get("detail")}

    # Which sub-topics this batch leans on: rotate through them so successive batches cover the unit.
    focus = subtopics
    if len(subtopics) > 6:
        start = ((batch.difficulty or 1) * 7 + batch.seq * 5) % len(subtopics)
        focus = (subtopics[start:] + subtopics[:start])[:6]
    # a heading alone ("Sensory & Motor") is vague to search on; its listed items say what to look for
    query = (f"{unit.name}: " + "; ".join(f"{s} ({details[s]})" if s in details else s for s in focus)) if focus \
        else f"{line.get('subject', '')} {unit.name}".strip()

    stage("retrieving", {"query": query[:200]})
    history, used = _unit_history(db, unit.id)
    primary = primary_books(db, subject_family(line))
    context = _context(db, query[:500], programme, used if len(used) < 300 else set(), primary)
    if not context:
        context = _context(db, query[:500], programme, set(), primary)
    if not context:
        raise PermanentError("Insufficient relevant evidence: retrieval found no textbook passages for this unit.")
    blocks, total = [], 0
    for i, c in enumerate(context, 1):
        title = c.book.title if c.book else "Textbook"
        block = f"Chunk {i} [Book: {title} | Page: {c.page_number or 'N/A'}]\n{c.content}"
        if total + len(block) > 9000 and blocks:
            break
        blocks.append(block)
        total += len(block)
    context = context[:len(blocks)]

    qvec = _embed([query])[0]
    similar = _similar_existing(db, qvec)
    avoid = [f"- {s['stem'][:150]} -> {s['answer'][:50]}" for s in similar if s["sim"] >= 0.45][:30]
    avoid_text = "\n".join(avoid + history[-30:]) or "None"

    system = SYSTEM.format(
        programme=programme, programme_label=exam_levels.label(programme),
        mcq_guidance=exam_levels.mcq_guidance(programme),
        lineage=" > ".join(v for k, v in line.items() if k in ("paper", "specialty", "subject", "topic")) or unit.path,
        difficulty=DIFFICULTY_TEXT.get(batch.difficulty or 3, DIFFICULTY_TEXT[3]),
        options_example=", ".join(f'"{k}": "..."' for k in keys), first_key=keys[0],
        rationale_example=", ".join(f'"{k}": "why wrong"' for k in keys[1:]))
    listed = "\n".join(f"- {s}" + (f": {details[s]}" if s in details else "") for s in subtopics)
    user = (f"TOS UNIT: {unit.path}\nSUB-TOPICS (heading: the items it covers; give the heading as the sub-topic):\n"
            f"{listed or '(none listed)'}\n"
            f"FOCUS THIS BATCH ON: {', '.join(focus) if focus else unit.name}\n"
            f"Write exactly {batch.target} questions.\n\nTEXTBOOK PASSAGES:\n" + "\n\n".join(blocks)
            + f"\n\nALREADY IN THE BANK (do not test these facts again):\n{avoid_text}\n\n"
            f"Spread the correct answers across {', '.join(keys)}.")

    stage("generating", {"query": query[:200], "passages": len(context)})
    data, usage = llm_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                           role="fast", max_tokens=min(8000, 900 * batch.target + 800), temperature=0.4,
                           label=f"seed b{batch.id}")
    raw = [q for q in (data.get("questions") or []) if isinstance(q, dict)]
    if not raw:
        raise TransientError("The AI returned no questions.")

    stage("validating", {"generated": len(raw), "passages": len(context)})
    require_grounding = bool(cfg.get("require_grounding", True))
    require_rationales = bool(cfg.get("require_rationales", True))
    stem_dup = float(cfg.get("dup_stem_cosine", STEM_DUP_COSINE))
    items: list[dict] = []
    candidates: list[dict] = []
    for q in raw:
        item = _validate(q, keys, context, require_grounding, require_rationales, subtopics, _answer_supported)
        item["difficulty"] = batch.difficulty
        items.append(item)
        if item["status"] == "validated":
            candidates.append(item)

    stage("deduplicating", {"generated": len(raw), "validated": len(candidates)})
    if candidates:
        vecs = _embed([c["payload"]["question_text"] for c in candidates])
        cvecs = _embed([_concept_text({"tested_concept": c["payload"].get("tested_concept"),
                                       "options": c["payload"]["options"], "correct_option": c["payload"]["key"]})
                        for c in candidates])
        ex_vecs = np.stack([s["vec"] for s in similar]) if similar else np.zeros((0, len(vecs[0])), dtype=np.float32)
        ex_answers = [s["answer"].strip().lower() for s in similar]
        kept_vecs: list[np.ndarray] = []
        kept_concepts: list[np.ndarray] = list(_embed([f"{s['concept'] or ''} {s['answer']}".strip()
                                                       for s in similar if s["sim"] >= 0.55])) if similar else []
        kept_stems: list[str] = []
        for c, v, cv in zip(candidates, vecs, cvecs):
            answer = c["payload"]["options"][c["payload"]["key"]].strip().lower()
            reason = None
            if len(ex_vecs):
                sims = ex_vecs @ v
                i = int(np.argmax(sims))
                if sims[i] > stem_dup:
                    reason = f"same question as bank MCQ #{similar[i]['id']} (similarity {sims[i]:.2f})"
                elif any(sims[j] > SAME_ANSWER_DUP_COSINE and ex_answers[j] == answer for j in range(len(sims))):
                    j = max((j for j in range(len(sims)) if ex_answers[j] == answer), key=lambda j: sims[j])
                    reason = f"similar stem and same answer as bank MCQ #{similar[j]['id']}"
            if reason is None and kept_vecs and float(np.max(np.stack(kept_vecs) @ v)) > stem_dup:
                reason = "repeats another question in this batch"
            if reason is None and kept_concepts and bool((np.stack(kept_concepts) @ cv > CONCEPT_DUP_COSINE).any()):
                reason = "tests a fact already in the bank or this batch, worded differently"
            if reason is None and _is_text_duplicate(c["payload"]["question_text"], [s["stem"] for s in similar] + kept_stems):
                reason = "near-identical wording to an existing question"
            if reason:
                c["status"], c["reason"] = "duplicate", reason
                continue
            c["vec"] = v
            kept_vecs.append(v)
            kept_concepts.append(cv)
            kept_stems.append(c["payload"]["question_text"])

    stage("saving", {"generated": len(raw), "validated": sum(1 for i in items if i["status"] == "validated")})
    return {"items": items, "usage": usage, "context": context, "unit": unit, "doc": doc, "line": line,
            "programme": programme}


def _validate(q: dict, keys: str, context: list, require_grounding: bool, require_rationales: bool,
              subtopics: list[str], answer_supported) -> dict:
    stem = " ".join(str(q.get("question_text") or "").split())
    options = q.get("options") if isinstance(q.get("options"), dict) else {}
    options = {str(k).strip().upper()[:1]: " ".join(str(v).split()) for k, v in options.items() if str(v).strip()}
    key = str(q.get("correct_option") or "").strip().upper()[:1]
    explanation = str(q.get("explanation") or "").strip()
    rationales = q.get("distractor_rationales") if isinstance(q.get("distractor_rationales"), dict) else {}
    rationales = {str(k).strip().upper()[:1]: str(v).strip() for k, v in rationales.items() if str(v).strip()}
    subtopic = str(q.get("subtopic") or "").strip() or None
    if subtopic and subtopics:
        match = next((s for s in subtopics if s.lower() == subtopic.lower()), None)
        subtopic = match
    payload: dict[str, Any] = {"question_text": stem, "options": options, "key": key,
                               "answer": options.get(key, ""), "explanation": explanation,
                               "distractor_rationales": rationales,
                               "learning_objective": str(q.get("learning_objective") or "").strip() or None,
                               "tested_concept": str(q.get("tested_concept") or "").strip()[:200] or None,
                               "subtopic": subtopic, "citations": []}

    def reject(why: str) -> dict:
        return {"status": "rejected", "reason": why, "payload": payload, "subtopic": subtopic}

    if len(stem) < 15:
        return reject("malformed: the stem is missing or too short")
    if sorted(options) != sorted(keys):
        return reject(f"malformed: expected options {', '.join(keys)}, got {', '.join(sorted(options)) or 'none'}")
    if len({v.lower() for v in options.values()}) < len(options):
        return reject("malformed: two options are identical")
    if key not in options:
        return reject("malformed: the keyed answer is not one of the options")
    if not explanation:
        return reject("no explanation")
    if require_rationales:
        missing = [k for k in keys if k != key and k not in rationales]
        if missing:
            return reject(f"no rationale for distractor {', '.join(missing)}")

    src = q.get("source_chunk")
    chunk = None
    try:
        if src is not None and 1 <= int(src) <= len(context):
            chunk = context[int(src) - 1]
    except (TypeError, ValueError):
        chunk = None
    supported = bool(chunk is not None and answer_supported(options[key], chunk.content))
    if chunk is not None:
        payload["citations"] = [{"book_title": chunk.book.title if chunk.book else None, "page_number": chunk.page_number,
                                 "chunk_id": chunk.id, "chunk_ids": list(chunk.chunk_ids), "supported": supported,
                                 "excerpt": (chunk.content or "")[:400]}]
    payload["grounding"] = "book" if supported else "ai"
    if require_grounding and chunk is None:
        return reject("no source passage cited")
    if require_grounding and not supported:
        return reject("the cited passage does not support the keyed answer")
    return {"status": "validated", "reason": None, "payload": payload, "subtopic": subtopic}


def explanation_markdown(p: dict) -> str:
    parts = [f"**Why {p['key']} is right**: {p['explanation']}"]
    if p.get("distractor_rationales"):
        parts.append("**Why the others are wrong**:\n" + "\n".join(
            f"- **{k}.** {v}" for k, v in sorted(p["distractor_rationales"].items()) if k != p["key"]))
    if p.get("learning_objective"):
        parts.append(f"**Learning objective**: {p['learning_objective']}")
    cite = (p.get("citations") or [None])[0]
    if cite and cite.get("supported"):
        parts.append(f"**Source**: {cite['book_title']}, Page {cite['page_number']}")
    else:
        parts.append("**Source**: AI clinical knowledge (not from the ingested textbooks)")
    return "\n\n".join(parts)


def save_mcq_batch(db, batch: SeedBatch, job: SeedJob, result: dict) -> dict[str, int]:
    """Questions, items and the batch's done state in ONE transaction. Idempotent: a batch already done, or a
    question already saved under this batch's reference, is never written again."""
    db.refresh(batch)
    if batch.status == "done":
        return {"skipped": 1}
    cfg = job.config or {}
    publish = bool(cfg.get("publish_on_insert", False))
    unit, doc, line = result["unit"], result["doc"], result["line"]
    paper = line.get("paper") or ""
    category = f"Seed · {doc.family} {paper}".strip()
    counts = {"generated": 0, "validated": 0, "inserted": 0, "rejected": 0, "duplicate": 0}
    for idx, it in enumerate(result["items"]):
        counts["generated"] += 1
        p = it["payload"]
        mcq_id = None
        status = it["status"]
        if status == "validated" and not job.dry_run:
            ref = f"seed:b{batch.id}:{idx}"
            existing = db.query(MCQ.id).filter(MCQ.source == "seed-studio", MCQ.source_ref == ref).scalar()
            if existing:
                mcq_id = existing
            else:
                cite = (p.get("citations") or [None])[0]
                book_id = None
                if cite and cite.get("supported"):
                    book_id = db.query(Book.id).filter(Book.title == cite["book_title"]).scalar()
                m = MCQ(book_id=book_id, quiz_set_id=f"seed-node-{unit.id}", quiz_set_title=unit.name[:120],
                        question_text=p["question_text"], options=p["options"], correct_option=p["key"],
                        topic=unit.name, main_category=category,
                        sub_category=line.get("specialty") or line.get("subject") or unit.name,
                        difficulty=batch.difficulty, explanation_markdown=explanation_markdown(p),
                        status="ready" if publish else "pending",
                        stem_embedding=it["vec"].tolist() if it.get("vec") is not None else None,
                        source_chunk_ids=(cite or {}).get("chunk_ids") if cite and cite.get("supported") else [],
                        tested_concept=p.get("tested_concept"), grounding=p.get("grounding"),
                        source="seed-studio", source_ref=ref)
                db.add(m)
                db.flush()
                mcq_id = m.id
                db.add(MCQTag(mcq_id=m.id, axis="programme", label=result["programme"]))
                db.add(MCQTag(mcq_id=m.id, axis="seed_node", label=str(unit.id)))
            status = "inserted"
        counts["validated"] += status in ("validated", "inserted")
        counts["inserted"] += status == "inserted"
        counts["rejected"] += status == "rejected"
        counts["duplicate"] += status == "duplicate"
        db.add(SeedItem(batch_id=batch.id, job_id=job.id, node_id=unit.id, status=status, reason=it.get("reason"),
                        difficulty=batch.difficulty, subtopic=it.get("subtopic"), payload=p, mcq_id=mcq_id,
                        review_state="approved" if (publish and mcq_id) else "none"))
    batch.status, batch.stage, batch.finished_at = "done", "done", datetime.utcnow()
    batch.progress = {**(batch.progress or {}), **counts}
    db.commit()
    return counts


# ─── revision notes ───────────────────────────────────────────────────────────────────────────────

def run_note_batch(db, batch: SeedBatch, job: SeedJob, stage: Callable[[str, dict | None], None]) -> dict[str, Any]:
    """Write (or confirm) the current revision note for one leaf. A leaf with a current note is skipped unless the
    job asked to regenerate notes; an approved note is never replaced."""
    from app import revision

    leaf = db.get(SeedTosNode, batch.node_id)
    doc = db.get(SeedTosDocument, leaf.document_id)
    programme = programme_for(doc)
    current = db.query(SeedNote).filter(SeedNote.node_id == leaf.id, SeedNote.status == "done") \
        .order_by(SeedNote.id.desc()).first()
    if current and (current.review_state == "approved" or not (job.config or {}).get("regenerate_notes")):
        return {"skipped": True}
    line = lineage(db, leaf)
    topic = line.get("topic")
    query = f"{topic}: {leaf.name}" if topic and topic != leaf.name else leaf.name
    if line.get("subject") and line["subject"] not in query:
        query = f"{line['subject']} {query}"
    family = subject_family(line)
    primary = primary_books(db, family)
    titles = {b.id: b.title for b in db.query(Book).filter(Book.status == "ready")}

    stage("retrieving", {"query": query[:200], "books": family or "whole library"})
    probe = f"{query} ({leaf.detail})" if leaf.detail else query
    found = subject_first(db, probe, limit=8, programme=programme, primary=primary, enough=3)
    if not found:
        return {"failed": "Insufficient relevant evidence: no textbook passages found."}

    # The AI plans the note: which items the syllabus lists here, what examiners test on them, and the headings.
    stage("planning", {"passages": len(found)})
    outline, usage = plan_outline(leaf, line, programme, found, titles)

    # Every heading is looked for in the subject's books; another book is added only for a heading they miss.
    book_ids = list(dict.fromkeys([c.book_id for c in found if c.book_id in primary] or primary))[:4] \
        if primary else list(dict.fromkeys(c.book_id for c in found))[:4]
    supplements: dict[int, list[str]] = {}
    if primary:
        for sec in outline["sections"]:
            q = f"{query}: {sec['heading']}" + (f" ({sec['focus']})" if sec["focus"] else "")
            hits = subject_first(db, q, limit=4, programme=programme, primary=primary, enough=2)
            for c in hits:
                if c.book_id not in primary and (c.book_id in supplements or len(book_ids) < 4):
                    if c.book_id not in book_ids:
                        book_ids.append(c.book_id)
                    supplements.setdefault(c.book_id, [])
                    if sec["heading"] not in supplements[c.book_id]:
                        supplements[c.book_id].append(sec["heading"])
                    break
    if not book_ids:
        return {"failed": "Insufficient relevant evidence: no textbook passages found."}

    plan = {"intent": outline["intent"], "items": outline["items"],
            "sections": [s["heading"] + ("" if s["item"] else " (added)") for s in outline["sections"]],
            "planned_by": outline["by"],
            "books": [titles.get(b, f"Book {b}") + (" (for: " + "; ".join(supplements[b]) + ")" if b in supplements
                                                    else " (primary)" if b in primary else "") for b in book_ids]}
    user = db.query(User).filter(User.username == job.created_by).first() or db.query(User).filter(User.role == "admin").first()
    stage("writing_note", {"books": len(book_ids), "sections": len(outline["sections"])})
    try:
        row = revision.build(db, user, {"book_ids": book_ids, "topic": query[:300], "level": programme, "length": "full",
                                        **({"sections": outline["sections"]} if outline["sections"] else {})})
    except RuntimeError as e:   # revision.build's own "could not be written / no text" outcomes
        if "Try again" in str(e):   # an empty or failed model reply: worth a retry, not a failed note
            raise TransientError(str(e))
        return {"failed": str(e), "usage": usage, "plan": plan}
    bullets = [ln for ln in (row.markdown or "").splitlines() if ln.lstrip().startswith(("-", "*"))]
    cited = [b for b in bullets if "[" in b and "Page" in b]
    if len(cited) < 3:
        return {"failed": f"Too little supported content: {len(cited)} cited bullet(s) after reference checking.",
                "usage": usage, "plan": plan}
    coverage = item_coverage(row.markdown or "", [s["heading"] for s in outline["sections"] if s["item"]])
    return {"markdown": row.markdown, "citations": row.citations or [], "summary_key": row.scope_key,
            "coverage": coverage, "usage": usage, "plan": plan}


OUTLINE_SYSTEM = """You plan the outline of one revision note for {programme} candidates ({programme_label}).
The note covers one entry of the official Table of Specifications (TOS). Think like the examiner who sets this paper.

Work out, in this order:
1. ITEMS: the separate things this TOS entry lists, read with understanding. "Insulin, Oral Anti-Diabetics,
   Anti-thyroids and steroids" lists four drug groups; "Edema (types, stages)" is one item; "Fluid Distribution and
   Edema" may be one topic or two, as the subject makes sense of it; a single concept is one item. Word each item as
   the TOS wrote it.
2. INTENT: one sentence on what examiners test on this entry at this exam, for this subject (a pharmacology entry is
   tested on mechanism, uses, adverse effects and interactions; physiology on mechanisms, regulation and values;
   anatomy on relations, supply and applied anatomy; behavioural science on concepts, models and application;
   clinical entries on features, diagnosis and management). Not a fixed template: judge this entry.
3. SECTIONS: 3 to 8 headings, in teaching order. EVERY item is a heading of its own ("item": true), in TOS order.
   You may add headings that are not items ("item": false) when examiners ask about them AND the passage excerpts
   suggest the books cover them. Never use generic headings like "Core concepts", "Overview", "Clinical features" for
   a topic examiners do not test clinically. Each heading gets a "focus": the specific facets to cover, one line.

Return JSON only:
{{"items": ["..."], "intent": "...", "sections": [{{"heading": "...", "focus": "...", "item": true}}]}}"""


def plan_outline(leaf: SeedTosNode, line: dict[str, str], programme: str, found: list,
                 titles: dict[int, str]) -> tuple[dict[str, Any], Any]:
    """The AI-planned outline for one leaf's note: {"items", "intent", "sections": [{heading, focus, item}], "by"}.
    Falls back to the TOS's own list (or, with none, the intent template in app/revision.py) if the call fails."""
    excerpts = "\n".join(
        f"- [{titles.get(c.book_id, 'Textbook')} | {(getattr(c, 'chapter', None) or '').strip()[:80]}] "
        + " ".join((c.content or "").split())[:180] for c in found[:8])
    path = " > ".join(v for k, v in line.items() if k in ("paper", "specialty", "subject", "topic") and v != leaf.name)
    user = (f"TOS POSITION: {path}\nTOS ENTRY: {leaf.name}\n"
            + (f"ITEMS LISTED UNDER IT: {leaf.detail}\n" if leaf.detail else "")
            + f"\nWHAT THE BOOKS HAVE (passage excerpts found for this entry):\n{excerpts}")
    try:
        data, usage = llm_json([{"role": "system", "content": OUTLINE_SYSTEM.format(
                                    programme=programme, programme_label=exam_levels.label(programme))},
                                {"role": "user", "content": user}],
                               max_tokens=1200, temperature=0.2, label="seed-note-outline")
        return validate_outline(data, leaf), usage
    except Exception as e:
        logger.warning("Outline planning failed for node %s, using the TOS list: %s", leaf.id, e)
        items = split_items(leaf.detail)
        return {"items": items, "intent": "", "by": "fallback",
                "sections": [{"heading": i, "focus": "", "item": True} for i in items]}, None


def validate_outline(data: Any, leaf: SeedTosNode) -> dict[str, Any]:
    """Clean the planner's JSON. Items listed in the TOS detail can never be dropped: one the planner left out is put
    back. At most 8 headings, never cutting an item."""
    data = data if isinstance(data, dict) else {}
    clean = lambda v, n: " ".join(str(v or "").split())[:n]  # noqa: E731
    items = [clean(i, 160) for i in (data.get("items") or []) if isinstance(i, str) and clean(i, 160)]
    for listed in split_items(leaf.detail):   # the curated TOS list is authoritative where it exists
        if not any(listed.lower() == i.lower() for i in items):
            items.append(listed)
    sections, seen = [], set()
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        heading = clean(sec.get("heading"), 160)
        if not heading or heading.lower() in seen:
            continue
        seen.add(heading.lower())
        is_item = any(heading.lower() == i.lower() or i.lower() in heading.lower() for i in items)
        sections.append({"heading": heading, "focus": clean(sec.get("focus"), 240), "item": is_item})
    for i in items:
        if not any(i.lower() == s["heading"].lower() or i.lower() in s["heading"].lower() for s in sections):
            sections.append({"heading": i, "focus": "", "item": True})
    extras = 8 - sum(s["item"] for s in sections)
    kept = []
    for sec in sections:
        if sec["item"]:
            kept.append(sec)
        elif extras > 0:
            kept.append(sec)
            extras -= 1
    return {"items": items, "intent": clean(data.get("intent"), 300), "sections": kept[:12], "by": "ai"}


def split_items(detail: str | None) -> list[str]:
    """"Distribution within body compartments, Forces of filtration and absorption, Edema (types, stages)" ->
    three items. Commas inside brackets do not split ("Edema (types, stages)" stays one item)."""
    if not detail:
        return []
    items, depth, cur = [], 0, ""
    for ch in detail:
        depth += ch in "([" and 1 or 0
        depth -= ch in ")]" and 1 or 0
        if ch in ",;" and depth <= 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    return [" ".join(i.split()).strip(" .") for i in items if i.strip(" .")][:12]


def item_coverage(markdown: str, items: list[str]) -> dict | None:
    """Which listed items the note actually covers: a section with that heading and at least one bullet."""
    if not items:
        return None
    covered, current, has_bullet = set(), None, False
    for ln in markdown.splitlines() + ["## "]:
        if ln.startswith("#"):
            if current is not None and has_bullet:
                covered.add(current)
            head = ln.lstrip("#").strip().lower()
            current = next((i for i in items if i.lower() == head or i.lower() in head or head in i.lower()), None) \
                if head else None
            has_bullet = False
        elif ln.lstrip().startswith(("-", "*")):
            has_bullet = True
    missing = [i for i in items if i not in covered]
    return {"items": len(items), "covered": len(items) - len(missing), "missing": missing}


def _coverage_note(cov: dict | None) -> str | None:
    """Recorded on a written note whose TOS items were not all covered (the note is kept; the gap is named)."""
    if not cov or not cov["missing"]:
        return None
    return (f"Covered {cov['covered']} of {cov['items']} listed items; the retrieved passages did not cover: "
            + "; ".join(cov["missing"]))


def save_note(db, batch: SeedBatch, job: SeedJob, result: dict) -> None:
    db.refresh(batch)
    if batch.status == "done":
        return
    if result.get("skipped"):
        batch.progress = {**(batch.progress or {}), "skipped": "a current note already exists"}
    else:
        failed = result.get("failed")
        db.add(SeedNote(node_id=batch.node_id, job_id=job.id, batch_id=batch.id, status="failed" if failed else "done",
                        markdown=result.get("markdown"), citations=result.get("citations") or [],
                        summary_key=result.get("summary_key"), reason=failed or _coverage_note(result.get("coverage"))))
        batch.progress = {**(batch.progress or {}), "note": "failed" if failed else "done", "reason": failed,
                          **({"plan": result["plan"]} if result.get("plan") else {})}
    batch.status, batch.stage, batch.finished_at = "done", "done", datetime.utcnow()
    db.commit()
