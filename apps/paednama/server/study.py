"""Phase C: topic summaries (all books, or one book's own account) and the reading loop.

Everything is written from retrieved passages of the Paediatrics books only, cited by number, and kept once written.
The reading loop's answers are graded against the passage they came from; a missed answer becomes a medNAMA concept
card with a spaced review, so it comes back in Daily Dose like any other card.

Merge back: this reuses medNAMA's private helpers in app.retention (_embed, _find_existing_card, _schedule,
_norm_ws); when PaedNAMA joins medNAMA these become normal imports.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from . import registry, settings  # noqa: F401  (backend/ on the path)
from .models import PaedsReadPack, PaedsReading, PaedsSheet

logger = logging.getLogger("paednama.study")

# The LLM, swappable in tests: (messages, json_mode, role, label) -> text
LLM: Callable[..., str] | None = None


def _llm(messages: list[dict], *, json_mode: bool = False, role: str = "chat", label: str = "paeds",
         temperature: float = 0.2, max_tokens: int | None = None) -> str:
    if LLM is not None:
        return LLM(messages, json_mode=json_mode, role=role, label=label)
    from app.llm import chat_completion

    return chat_completion(messages, json_mode=json_mode, role=role, label=label, temperature=temperature,
                           max_tokens=max_tokens)


def _json(text: str) -> dict:
    import json

    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    data = json.loads(t)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


class StudyUnavailable(Exception):
    """No passages, or the AI did not answer: the caller reports it."""


# ─── retrieval scoped to Paediatrics books ────────────────────────────────────────────────────────

def topic_query(system: dict, topic: dict) -> str:
    """A focused search: the topic, then a few words of its first two sub-topics (long OR-queries are slow)."""
    words = topic["name"].replace("/", " ").split()
    for st in topic.get("subtopics", [])[:2]:
        words += st.replace(",", " ").split()[:5]
    seen, out = set(), []
    for w in words:
        k = w.lower().strip("():;.,-")
        if k and k not in seen:
            seen.add(k)
            out.append(w.strip("():;,"))
    return " ".join(out[:16])


def retrieve(db: Session, query: str, book_ids: list[int], limit: int = 8) -> list[Any]:
    """Passages from these books only (medNAMA's search, which already spreads results across books)."""
    from app.retrieval import retrieval_service

    if not book_ids:
        return []
    try:
        return retrieval_service.search(db, query, limit=limit, rewrite=False, deep_rerank=False, book_ids=book_ids).context
    except Exception as e:
        logger.warning("Paeds retrieval failed for %r: %s", query, e)
        db.rollback()
        return []


def numbered(chunks: list[Any], cap: int = 1500) -> tuple[str, list[dict]]:
    passages, cites = [], []
    for i, c in enumerate(chunks, 1):
        title = c.book.title if c.book else "Textbook"
        passages.append(f"[{i}] {title}, {c.chapter or 'chapter n/a'}, p.{c.page_number or '?'}\n{c.content[:cap]}")
        cites.append({"n": i, "book_id": c.book_id, "book_title": title, "chapter": c.chapter, "page": c.page_number})
    return "\n\n".join(passages), cites


def keep_valid_citations(md: str, cites: list[dict]) -> tuple[str, list[dict]]:
    """Drop [n] markers that point at no passage, and keep only the passages actually cited."""
    valid = {c["n"] for c in cites}
    used: set[int] = set()

    def fix(m: re.Match) -> str:
        nums = [int(x) for x in re.findall(r"\d+", m.group(0))]
        ok = [n for n in nums if n in valid]
        used.update(ok)
        return "".join(f"[{n}]" for n in ok)

    md = re.sub(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]", fix, md)
    return md, [c for c in cites if c["n"] in used]


# ─── topic summaries ─────────────────────────────────────────────────────────────────────────────

SHEET_VERSION = 2
SHEET_PROMPT = """You write a topic summary for a Paediatrics trainee preparing for CPSP {exam}: the theory MCQs and the
clinical exam (TOACS stations with examiner questions{long_case}).
Topic: {topic}  (system: {system})
{subtopics}
Use ONLY the numbered passages below{lens_note}. Cite every point inline as [n]. Where the passages do not cover a
section, write "Not covered in these books." under it: never fill gaps from memory.

Length follows the topic: a narrow topic gets a short summary, a big one a longer one. Dense bullets, no padding.
Sections, in Markdown:
### Overview
two lines: what it is and why it matters.
### High-yield points
the facts examiners ask: numbers, cut-offs, ages, classic associations, first-line choices.
### Clinical features and examination
what to look for and how to elicit it at the bedside; the signs that clinch the diagnosis.
### Diagnosis and investigations
in the order you would do them, with how to read the results.
### Differential diagnosis
a table: | Condition | Points for it | How to tell it apart |, for the conditions it is most confused with.
### Management
step by step, with doses and thresholds where the passages give them.
### Red flags and complications
### Exam angle
MCQ traps, and the questions an examiner asks at a station or viva on this topic.
{differ}
Passages:
{passages}"""

DIFFER = "### Where the books differ\nOnly if two passages from different books disagree on a fact; else omit this section.\n"


def get_sheet(db: Session, stage: str, topic_id: str, lens: str, paeds_book_ids: list[int], titles: dict[int, str],
              *, write: bool = True) -> dict[str, Any]:
    found = registry.find_topic(stage, topic_id)
    if not found:
        raise KeyError(topic_id)
    system, topic = found
    hit = db.query(PaedsSheet).filter_by(stage=stage, topic_id=topic_id, lens=lens).first()
    if hit and (hit.version or 1) >= SHEET_VERSION:
        return {"markdown": hit.markdown, "citations": hit.citations, "lens": lens, "cached": True}
    if not write:
        return {"markdown": "", "citations": [], "lens": lens, "missing": True}
    ids = paeds_book_ids if lens == "all" else [int(lens)]
    chunks = retrieve(db, topic_query(system, topic), ids, limit=10 if lens == "all" else 7)
    if not chunks:      # generic titles ("Principles of dermatologic therapy") fall under search's relevance floor:
        plain = re.sub(r"^\s*(principles|principals)\s+of\s+|\bpaediatric\b", "", topic["name"], flags=re.I).strip()
        chunks = retrieve(db, f"{plain} in children", ids, limit=10 if lens == "all" else 7)
    if not chunks:
        raise StudyUnavailable("These books have no passages on this topic." if lens == "all" else
                               f"{titles.get(int(lens), 'This book')} has no passages on this topic.")
    passages, cites = numbered(chunks)
    lens_note = "" if lens == "all" else f" (all from {titles.get(int(lens), 'one book')})"
    prompt = SHEET_PROMPT.format(
        exam="IMM" if stage == "imm" else "FCPS-II", topic=topic["name"], system=system["system"].title(),
        long_case="" if stage == "imm" else "; then a long case and short cases",
        subtopics=f"Sub-topics in the curriculum: {'; '.join(topic['subtopics'])}" if topic.get("subtopics") else "",
        lens_note=lens_note, differ=DIFFER if lens == "all" else "", passages=passages)
    try:
        md = _llm([{"role": "user", "content": prompt}], label="paeds-sheet", max_tokens=3500)
    except Exception as e:
        logger.warning("Sheet generation failed for %s/%s: %s", topic_id, lens, e)
        raise StudyUnavailable("The AI model did not answer. Try again in a moment.")
    md, cites = keep_valid_citations(md.strip(), cites)
    try:
        if hit:                                   # written with an older prompt: replace it
            hit.markdown, hit.citations, hit.version = md, cites, SHEET_VERSION
        else:
            db.add(PaedsSheet(stage=stage, topic_id=topic_id, lens=lens, markdown=md, citations=cites,
                              version=SHEET_VERSION))
        db.commit()
    except Exception:          # written by a parallel request first
        db.rollback()
    return {"markdown": md, "citations": cites, "lens": lens, "cached": False}


# ─── the reading loop ─────────────────────────────────────────────────────────────────────────────

PACK_PROMPT = """You prepare an active-reading session for a Paediatrics trainee (CPSP {exam}) on: {topic}.
Below are {n} SECTIONS from their textbooks. Return JSON:
{{"case": {{"stem": "a 2-3 sentence clinical vignette a trainee would meet, built only from facts in the sections",
           "question": "one question about the vignette that the sections answer",
           "answer": "the answer in 1-2 sentences", "section": <number of the section that answers it>}},
  "questions": [{{"section": <section number>, "q": "a short-answer recall question",
                 "answer": "the expected answer, a few words to one sentence",
                 "quote": "ONE sentence copied EXACTLY, character for character, from that section, that proves the answer"}}]}}
Write 3 questions per section: facts worth remembering (thresholds, doses, first-line tests, red flags, classic
features), not trivia. Never use facts that are not in the sections.

{sections}"""


def _sections(db: Session, stage: str, system: dict, topic: dict, spine_ids: list[int], all_ids: list[int]) -> list[dict]:
    """Up to three sections to read: from the spine book when it covers the topic, else from all the books."""
    q = topic_query(system, topic)
    chunks = retrieve(db, q, spine_ids, limit=3) if spine_ids else []
    if len(chunks) < 2:
        chunks = retrieve(db, q, all_ids, limit=3)
    out = []
    for c in chunks[:3]:
        out.append({"chunk_id": c.id, "book_id": c.book_id, "book_title": c.book.title if c.book else "Textbook",
                    "chapter": c.chapter, "page": c.page_number, "text": c.content[:2600]})
    return out


def get_pack(db: Session, stage: str, topic_id: str, spine_ids: list[int], all_ids: list[int]) -> PaedsReadPack:
    found = registry.find_topic(stage, topic_id)
    if not found:
        raise KeyError(topic_id)
    hit = db.query(PaedsReadPack).filter_by(stage=stage, topic_id=topic_id).first()
    if hit:
        return hit
    system, topic = found
    sections = _sections(db, stage, system, topic, spine_ids, all_ids)
    if not sections:
        raise StudyUnavailable("These books have no passages on this topic.")
    text = "\n\n".join(f"SECTION {i} ({s['book_title']}, p.{s['page'] or '?'}):\n{s['text']}" for i, s in enumerate(sections, 1))
    try:
        data = _json(_llm([{"role": "user", "content": PACK_PROMPT.format(
            exam="IMM" if stage == "imm" else "FCPS-II", topic=topic["name"], n=len(sections), sections=text)}],
            json_mode=True, label="paeds-pack", max_tokens=2500))
    except Exception as e:
        logger.warning("Reading pack failed for %s: %s", topic_id, e)
        raise StudyUnavailable("The AI model did not answer. Try again in a moment.")
    # Keep only questions whose quote really is in their section (an answer the book doesn't back is not asked).
    for s in sections:
        s["questions"] = []
    for q in data.get("questions") or []:
        try:
            i = int(q.get("section")) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= i < len(sections)):
            continue
        quote, ans, qq = str(q.get("quote") or "").strip(), str(q.get("answer") or "").strip(), str(q.get("q") or "").strip()
        if qq and ans and quote and _norm(quote) in _norm(sections[i]["text"]) and len(sections[i]["questions"]) < 3:
            sections[i]["questions"].append({"q": qq, "answer": ans, "quote": quote})
    case = data.get("case") or {}
    case = {"stem": str(case.get("stem") or "").strip(), "question": str(case.get("question") or "").strip(),
            "answer": str(case.get("answer") or "").strip()}
    pack = PaedsReadPack(stage=stage, topic_id=topic_id, case=case, sections=sections)
    try:
        db.add(pack)
        db.commit()
    except Exception:
        db.rollback()
        pack = db.query(PaedsReadPack).filter_by(stage=stage, topic_id=topic_id).first() or pack
    return pack


def public_pack(pack: PaedsReadPack) -> dict[str, Any]:
    """What the learner sees before answering: no answers, no quotes."""
    return {"case": {"stem": pack.case.get("stem"), "question": pack.case.get("question")},
            "sections": [{"book_title": s["book_title"], "chapter": s["chapter"], "page": s["page"], "text": s["text"],
                          "questions": [q["q"] for q in s.get("questions", [])]} for s in pack.sections]}


def reading(db: Session, user_id: int, stage: str, topic_id: str) -> PaedsReading:
    r = db.get(PaedsReading, (user_id, stage, topic_id))
    if r is None:
        r = PaedsReading(user_id=user_id, stage=stage, topic_id=topic_id, sections_done=[], explain_scores={})
        db.add(r)
        db.flush()
    return r


GRADE_PROMPT = (
    "You mark a Paediatrics trainee's short answer against the expected answer and the textbook sentence. Judge meaning, "
    "not wording; accept synonyms and correct extra detail; a confident wrong fact is wrong. Return JSON: "
    '{"correct": true|false, "feedback": "one sentence: what was right or what was missing"}')


def grade_answer(db: Session, user_id: int, pack: PaedsReadPack, section: int, index: int, answer: str,
                 confidence: str) -> dict[str, Any]:
    s = pack.sections[section]
    q = s["questions"][index]
    answer = (answer or "").strip()
    if not answer:
        correct, feedback = False, "No answer given."
    else:
        try:
            out = _json(_llm([{"role": "system", "content": GRADE_PROMPT}, {"role": "user", "content": (
                f"QUESTION: {q['q']}\nEXPECTED: {q['answer']}\nTEXTBOOK: \"{q['quote']}\"\n\nTRAINEE'S ANSWER: {answer[:600]}")}],
                json_mode=True, role="fast", label="paeds-grade", max_tokens=200))
            correct, feedback = bool(out.get("correct")), str(out.get("feedback") or "").strip()
        except Exception as e:
            logger.warning("Grading failed: %s", e)
            raise StudyUnavailable("The AI model did not answer. Try again in a moment.")
    r = reading(db, user_id, pack.stage, pack.topic_id)
    r.recall_total += 1
    r.recall_correct += 1 if correct else 0
    card_id = None
    if not correct or confidence == "guess":
        card_id = card_for_miss(db, user_id, s, q)
        r.cards_added += 1 if card_id else 0
    db.commit()
    return {"correct": correct, "feedback": feedback, "expected": q["answer"], "quote": q["quote"],
            "book_title": s["book_title"], "page": s["page"], "card_added": bool(card_id)}


def card_for_miss(db: Session, user_id: int, section: dict, q: dict) -> int | None:
    """A missed recall answer becomes a medNAMA concept card (reused if one for the same idea exists) with a review
    due soon, so it returns in Daily Dose and medNAMA's re-tests."""
    from app.models import ConceptCard, ConceptReview
    from app.retention import _embed, _find_existing_card, _schedule

    try:
        vec = _embed([f"{q['q']} {q['answer']}"])[0]
        card = _find_existing_card(db, vec)
        if card is None:
            card = ConceptCard(title=q["q"][:200], summary=q["answer"][:1500], quote=q["quote"][:1000],
                               chunk_id=section.get("chunk_id"), book_title=section.get("book_title"),
                               page_number=section.get("page"), subject="Paediatrics", source="textbook",
                               grounding="textbook", embedding=vec.tolist())
            db.add(card)
            db.flush()
        review = db.query(ConceptReview).filter_by(user_id=user_id, concept_id=card.id).first()
        if review is None:
            review = ConceptReview(user_id=user_id, concept_id=card.id, box=0, lapses=0, reviews=0)
            db.add(review)
        _schedule(review, is_correct=False, confidence="unsure")
        db.flush()
        return card.id
    except Exception as e:          # the answer is still marked; only the card is skipped
        logger.warning("Could not add a review card: %s", e)
        return None


EXPLAIN_PROMPT = (
    "You mark a Paediatrics trainee's explanation, in their own words, of a textbook section they just read, like a "
    "friendly CPSP examiner. Identify the 3-5 points that matter most in the section. Return JSON: "
    '{"score": 0-100, "points_hit": ["..."], "points_missed": ["..."], "errors": ["anything they stated that is '
    'wrong"], "feedback": "2 sentences, specific"}. Judge meaning, not wording. Never reward confident wrong statements.')


def grade_explain(db: Session, user_id: int, pack: PaedsReadPack, section: int, text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if len(text) < 20:
        raise ValueError("Write a few sentences in your own words.")
    s = pack.sections[section]
    try:
        out = _json(_llm([{"role": "system", "content": EXPLAIN_PROMPT}, {"role": "user", "content": (
            f"SECTION ({s['book_title']}, p.{s['page']}):\n{s['text']}\n\nTRAINEE'S EXPLANATION:\n{text[:1500]}")}],
            json_mode=True, role="fast", label="paeds-explain", max_tokens=600))
    except Exception as e:
        logger.warning("Explain grading failed: %s", e)
        raise StudyUnavailable("The AI model did not answer. Try again in a moment.")
    try:
        score = max(0, min(100, int(out.get("score") or 0)))
    except (TypeError, ValueError):
        score = 0
    r = reading(db, user_id, pack.stage, pack.topic_id)
    r.explain_scores = {**(r.explain_scores or {}), str(section): score}
    done = set(r.sections_done or [])
    done.add(section)
    r.sections_done = sorted(done)
    if len(done) >= len(pack.sections):
        r.finished_at = r.finished_at or datetime.utcnow()
    db.commit()
    return {"score": score, "points_hit": [str(x) for x in out.get("points_hit") or []][:6],
            "points_missed": [str(x) for x in out.get("points_missed") or []][:6],
            "errors": [str(x) for x in out.get("errors") or []][:4], "feedback": str(out.get("feedback") or "").strip(),
            "finished": r.finished_at is not None}


def progress(db: Session, user_id: int, stage: str) -> dict[str, Any]:
    rows = db.query(PaedsReading).filter_by(user_id=user_id, stage=stage).all()
    return {"topics": {r.topic_id: {"done": r.finished_at is not None, "sections_done": len(r.sections_done or []),
                                    "recall": [r.recall_correct, r.recall_total], "cards_added": r.cards_added}
                       for r in rows},
            "finished": sum(1 for r in rows if r.finished_at), "started": len(rows)}
