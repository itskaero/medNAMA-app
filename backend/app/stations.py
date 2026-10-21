"""TOACS / viva station engine (revision.md Parts III–IV): drafting, the examiner, scoring, calibration, debrief.

Rules this module keeps, because they decide whether a simulated assessment can be trusted:

* The examiner follows the reviewed question framework. Its questions and follow-ups are the reviewed text,
  verbatim; the AI only chooses whether to ask an allowed follow-up or move on, and (guided mode only) writes
  teaching and hints. Timed mode never sends anything that could reveal correctness: its lead-ins come from a
  fixed list, and the examiner keeps going even when the AI is unavailable.
* Every mark is traceable to a rubric criterion and to the candidate's own words: the scorer must quote the
  transcript, and the server checks the quote is really there. An unverified quote leaves the mark flagged and
  uncertain; an "unsafe action" finding without a verified quote is not applied, only flagged for review.
* Credit earned after a hint is capped by the criterion's prompted_credit (§17).
* Results are labelled simulated; pass/fail is stated only when the version defines a pass mark (§20).
* Drafts are grounded in the specialty's textbooks: each criterion cites a numbered passage with a quote that
  must appear in it, or it is flagged for the reviewer.

The LLM is injected (`llm=`) so tests and Seed Studio can substitute their own caller.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import AnswerEvent, User
from app.station_models import (COMPETENCIES, STATION_TYPES, Station, StationAttempt, StationAudit,
                                StationCalibration, StationCriterion, StationGold, StationScore, StationVersion)

logger = logging.getLogger("app.stations")

LLM = Callable[..., dict]
SIMULATED_NOTE = "Simulated result from AI marking against the station rubric. Not an official CPSP result."
CALIBRATION_THRESHOLDS = {"min_gold": 3, "max_mae": 0.20, "min_agreement": 0.80, "max_safety_misses": 0}
REPEAT_AFTER_DAYS = {True: 14, False: 3}   # strong attempt -> later; weak -> soon

TIMED_LEAD_INS = {
    "tutor": ["Thank you.", "Okay.", "Let's move on."],
    "examiner": ["Thank you.", "Next.", "Moving on."],
    "strict": ["Next.", "Be precise.", "Go on."],
}
PRESSURE_SUFFIX = {"calm": "", "realistic": "", "high": " You have limited time."}


def _default_llm(messages: list[dict], **kw) -> dict:
    from app.llm import llm_json
    return llm_json(messages, **kw)


def _norm(s: str) -> str:
    """Comparable text: lower case, single spaces, curly quotes and dashes folded."""
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return " ".join(s.split()).lower()


def quote_in(quote: str | None, text: str) -> bool:
    """Is this quote (at least 12 characters) really in the text? Trailing punctuation is ignored."""
    q = _norm(quote or "").strip(" .,;:'\"")
    return len(q) >= 12 and q in _norm(text)


def audit(db: Session, actor: str | None, action: str, entity: str, entity_id: Any, **detail) -> None:
    db.add(StationAudit(actor=actor, action=action, entity=entity, entity_id=str(entity_id), detail=detail))


# ─── Content access ───────────────────────────────────────────────────────────

def criteria_of(db: Session, version_id: int) -> list[StationCriterion]:
    return (db.query(StationCriterion).filter(StationCriterion.version_id == version_id)
            .order_by(StationCriterion.ordinal).all())


def latest_version(db: Session, station: Station) -> StationVersion | None:
    return (db.query(StationVersion).filter(StationVersion.station_id == station.id)
            .order_by(StationVersion.version.desc()).first())


def version_payload(db: Session, v: StationVersion, *, with_answers: bool) -> dict[str, Any]:
    """A version for display. Without answers (a learner before submitting) only what the candidate may see."""
    out: dict[str, Any] = {
        "id": v.id, "version": v.version, "scenario": v.scenario, "time_limit_s": v.time_limit_s,
        "difficulty": v.difficulty, "response_formats": v.response_formats,
        "instructions_guided": v.instructions_guided, "instructions_timed": v.instructions_timed,
        "approved_at": v.approved_at.isoformat() if v.approved_at else None, "approved_by": v.approved_by,
    }
    if with_answers:
        out.update({
            "stages": v.stages, "framework": v.framework, "model_answer": v.model_answer, "pass_mark": v.pass_mark,
            "sources": v.sources, "lineage": v.lineage, "review_notes": v.review_notes,
            "difficulty_dims": v.difficulty_dims,
            "criteria": [{"id": c.id, "ordinal": c.ordinal, "competency": c.competency, "expected": c.expected,
                          "alternatives": c.alternatives, "marks": c.marks, "safety_critical": c.safety_critical,
                          "prompted_credit": c.prompted_credit, "source_ref": c.source_ref, "flagged": c.flagged}
                         for c in criteria_of(db, v.id)],
        })
    return out


# ─── Drafting (grounded in the specialty's textbooks) ─────────────────────────

DRAFT_PROMPT = """You are writing ONE FCPS Part II style TOACS/viva station for {specialty}, for postgraduate trainees.
TOS topic: {subject} > {topic}
Subtopics in the TOS: {subtopics}

Write the station ONLY from the numbered textbook passages below. Every rubric criterion must cite the passage
number it rests on and a short quote (10–30 words) copied EXACTLY from that passage. Do not use facts the
passages do not state. If the passages cannot support a sound station, return {{"error": "insufficient evidence"}}.
Keep it to what a {specialty} examiner covers in one station of about 8 minutes: ONE focused clinical problem, 4-6
examiner questions, 6-10 rubric criteria spread across the competencies the questions test. A question may only rely on
information the candidate has already been given (the scenario, or a stage released before it).

Return JSON:
{{
 "title": "short station title",
 "station_type": "viva" | "data_interpretation" | "management",
 "scenario": "the clinical stem the candidate reads first (no answers in it)",
 "stages": [{{"after_item": <framework index after which this new information is revealed>, "text": "..."}}],
 "instructions_timed": "what the candidate is asked to do, WITHOUT any hint of the answers",
 "instructions_guided": "the same plus how hints work",
 "framework": [{{"prompt": "examiner question, one at a time", "competency": "<one of {competencies}>",
                "follow_ups": ["probing follow-up questions an examiner may ask (0-2)"]}}],   // 4-7 items, in order
 "criteria": [{{"competency": "<one of {competencies}>", "expected": "the behaviour that earns the marks",
               "alternatives": ["acceptable alternative answers"], "marks": <1-4>, "safety_critical": <true only if
               missing or contradicting it would endanger the patient>, "source": {{"n": <passage number>, "quote": "..."}}}}],
 "model_answer": "a concise strong answer to the whole station",
 "time_limit_s": <seconds, 300-720>,
 "difficulty": <1-5>
}}

Passages:
{passages}"""


def specialty_books(db: Session, specialty: str) -> list[int]:
    from app.retention import _books_for_subject
    return _books_for_subject(db, specialty)


def _passages(db: Session, query: str, book_ids: list[int], limit: int = 8) -> list[dict[str, Any]]:
    from app.retrieval import retrieval_service

    result = retrieval_service.search(db, query, limit=limit, book_ids=book_ids)
    return [{"n": i, "book_title": c.book.title if c.book else "Textbook", "page_number": c.page_number,
             "chapter": c.chapter, "content": c.content} for i, c in enumerate(result.context, 1)]


def validate_draft(data: dict, passages: list[dict]) -> tuple[dict, list[str]]:
    """Normalise an AI draft and check it. Returns (clean draft, problems). Criteria whose quote is not in the
    cited passage are kept but flagged, so the reviewer sees exactly what is unsupported."""
    problems: list[str] = []
    by_n = {p["n"]: p for p in passages}
    framework = []
    for item in data.get("framework") or []:
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        comp = item.get("competency") if item.get("competency") in COMPETENCIES else "reasoning"
        follow = [str(f).strip() for f in (item.get("follow_ups") or []) if str(f).strip()][:2]
        framework.append({"id": len(framework), "prompt": prompt, "competency": comp, "follow_ups": follow})
    if not 3 <= len(framework) <= 8:
        problems.append(f"framework has {len(framework)} questions (need 3–8)")
    criteria = []
    for c in data.get("criteria") or []:
        expected = str(c.get("expected") or "").strip()
        if not expected:
            continue
        src = c.get("source") or {}
        n = src.get("n") if isinstance(src.get("n"), int) else None
        quote = str(src.get("quote") or "")
        ok = n in by_n and quote_in(quote, by_n[n]["content"])
        criteria.append({
            "competency": c.get("competency") if c.get("competency") in COMPETENCIES else "knowledge",
            "expected": expected,
            "alternatives": [str(a).strip() for a in (c.get("alternatives") or []) if str(a).strip()],
            "marks": float(min(4, max(1, float(c.get("marks") or 1)))),
            "safety_critical": bool(c.get("safety_critical")),
            "source_ref": ({"n": n, "book_title": by_n[n]["book_title"], "page_number": by_n[n]["page_number"],
                            "quote": quote} if n in by_n else {}),
            "flagged": not ok,
        })
    verified = sum(1 for c in criteria if not c["flagged"])
    if len(criteria) < 4:
        problems.append(f"only {len(criteria)} rubric criteria (need at least 4)")
    elif verified < len(criteria) / 2:
        problems.append(f"only {verified} of {len(criteria)} criteria cite a quote found in their passage")
    stages = [{"after_item": int(s["after_item"]), "text": str(s["text"]).strip()}
              for s in (data.get("stages") or [])
              if isinstance(s, dict) and str(s.get("text") or "").strip() and str(s.get("after_item", "")).lstrip("-").isdigit()]
    clean = {
        "title": str(data.get("title") or "").strip()[:160] or "Untitled station",
        "station_type": data.get("station_type") if data.get("station_type") in STATION_TYPES else "viva",
        "scenario": str(data.get("scenario") or "").strip(),
        "stages": [s for s in stages if 0 <= s["after_item"] < len(framework)],
        "instructions_timed": str(data.get("instructions_timed") or "").strip(),
        "instructions_guided": str(data.get("instructions_guided") or "").strip(),
        "framework": framework,
        "criteria": criteria,
        "model_answer": str(data.get("model_answer") or "").strip(),
        "time_limit_s": int(min(900, max(240, int(data.get("time_limit_s") or 480)))),
        "difficulty": int(min(5, max(1, int(data.get("difficulty") or 3)))),
    }
    if not clean["scenario"]:
        problems.append("no scenario")
    return clean, problems


def draft_station(db: Session, actor: str, exam_id: str, paper_id: str, topic_id: str, specialty: str,
                  *, llm: LLM | None = None, role: str = "chat") -> Station:
    """Write one station for a TOS topic from the specialty's textbooks, saved as a draft for review."""
    from app.tos import find_topic, tos_registry

    paper = tos_registry.get_paper(exam_id, paper_id)
    found = find_topic(paper, topic_id) if paper else None
    if not found:
        raise ValueError("Topic not found in this TOS paper")
    subject, topic = found
    books = specialty_books(db, specialty)
    if not books:
        raise ValueError(f"No {specialty} textbooks in the library to ground a station in")
    subs = topic.get("subtopics", [])
    from app.tos_sheets import search_query   # focused: a long subtopic list made keyword search crawl
    passages = _passages(db, search_query(topic["topic_name"], subs), books)
    if len(passages) < 3:
        raise ValueError("Too few textbook passages on this topic to ground a station")
    prompt = DRAFT_PROMPT.format(
        specialty=specialty, subject=subject["subject_name"], topic=topic["topic_name"],
        subtopics=", ".join(subs) or "none listed", competencies=", ".join(COMPETENCIES),
        passages="\n\n".join(f"[{p['n']}] {p['book_title']}, p.{p['page_number'] or '?'}\n{p['content'][:1800]}"
                             for p in passages))
    data = (llm or _default_llm)([{"role": "user", "content": prompt}], role=role, temperature=0.3, max_tokens=6000, label="station-draft")
    if data.get("error"):
        raise ValueError(f"The AI declined: {data['error']}")
    clean, problems = validate_draft(data, passages)
    if any(p.startswith(("framework", "only", "no scenario")) for p in problems) and len(clean["criteria"]) < 4:
        raise ValueError("Draft rejected: " + "; ".join(problems))

    station = Station(specialty=specialty, tos_exam_id=exam_id, tos_paper_id=paper_id, tos_topic_id=topic_id,
                      title=clean["title"], station_type=clean["station_type"], status="draft",
                      provenance="ai_draft", created_by=actor)
    db.add(station)
    db.flush()
    version = _new_version(db, station, clean, actor, lineage={
        "prompt_hash": hashlib.sha1(DRAFT_PROMPT.encode()).hexdigest()[:12], "source": "ai_draft",
        "problems": problems, "passages": [{k: p[k] for k in ("n", "book_title", "page_number", "chapter")}
                                            for p in passages]})
    version.sources = [{k: p[k] for k in ("n", "book_title", "page_number", "chapter")} for p in passages]
    station.current_version_id = version.id
    audit(db, actor, "draft", "station", station.id, topic_id=topic_id, problems=problems)
    db.commit()
    return station


def _new_version(db: Session, station: Station, content: dict, actor: str, lineage: dict | None = None) -> StationVersion:
    prev = latest_version(db, station)
    v = StationVersion(station_id=station.id, version=(prev.version + 1) if prev else 1,
                       scenario=content["scenario"], stages=content.get("stages", []),
                       instructions_guided=content.get("instructions_guided", ""),
                       instructions_timed=content.get("instructions_timed", ""),
                       framework=content["framework"], response_formats=content.get("response_formats", ["text"]),
                       time_limit_s=content.get("time_limit_s", 480), difficulty=content.get("difficulty", 3),
                       difficulty_dims=content.get("difficulty_dims", {}), model_answer=content.get("model_answer", ""),
                       pass_mark=content.get("pass_mark"), sources=content.get("sources", []),
                       lineage={**(lineage or {}), "parent_version_id": prev.id if prev else None},
                       created_by=actor)
    db.add(v)
    db.flush()
    for i, c in enumerate(content["criteria"]):
        db.add(StationCriterion(version_id=v.id, ordinal=i, competency=c["competency"], expected=c["expected"],
                                alternatives=c.get("alternatives", []), marks=float(c["marks"]),
                                safety_critical=bool(c.get("safety_critical")),
                                prompted_credit=float(c.get("prompted_credit", 0.5)),
                                source_ref=c.get("source_ref", {}), flagged=bool(c.get("flagged"))))
    return v


def revise_station(db: Session, station: Station, content: dict, actor: str) -> StationVersion:
    """An edit is a new version; approved versions are never changed. The station returns to review."""
    for key in ("scenario", "framework", "criteria"):
        if not content.get(key):
            raise ValueError(f"{key} is required")
    v = _new_version(db, station, content, actor, lineage={"source": "edit"})
    if station.status != "published":
        station.status = "in_review"
    station.updated_at = datetime.utcnow()
    audit(db, actor, "revise", "station", station.id, version=v.version)
    db.commit()
    return v


def approve_version(db: Session, station: Station, version: StationVersion, actor: str, note: str | None = None) -> None:
    if version.station_id != station.id:
        raise ValueError("Version does not belong to this station")
    if version.approved_at:
        raise ValueError("Already approved")
    version.approved_at = datetime.utcnow()
    version.approved_by = actor
    version.review_notes = note
    if station.status in ("draft", "in_review"):
        station.status = "approved"
    audit(db, actor, "approve", "station_version", version.id, station_id=station.id, note=note)
    db.commit()


def publish(db: Session, station: Station, actor: str) -> None:
    """Learners get the latest approved version. Unapproved content can never be published."""
    v = (db.query(StationVersion).filter(StationVersion.station_id == station.id, StationVersion.approved_at.isnot(None))
         .order_by(StationVersion.version.desc()).first())
    if v is None:
        raise ValueError("Approve a version before publishing")
    station.current_version_id = v.id
    station.status = "published"
    station.published_at = datetime.utcnow()
    station.readiness = "assessment_ready" if calibration_passed(db, v.id) else "practice_only"
    audit(db, actor, "publish", "station", station.id, version=v.version, readiness=station.readiness)
    db.commit()


def set_status(db: Session, station: Station, status: str, actor: str) -> None:
    if status not in ("retired", "approved"):
        raise ValueError("Only retire or unpublish (back to approved) here")
    audit(db, actor, "status", "station", station.id, before=station.status, after=status)
    station.status = status
    db.commit()


# ─── Attempts and the examiner ────────────────────────────────────────────────

def start_attempt(db: Session, user: User, station: Station, mode: str, persona: str, pressure: str) -> StationAttempt:
    if station.status != "published" or not station.current_version_id:
        raise ValueError("This station is not available")
    v = db.get(StationVersion, station.current_version_id)
    now = datetime.utcnow()
    first = v.framework[0]["prompt"] if v.framework else "Please begin."
    opening = f"{v.scenario}\n\n{first}"
    a = StationAttempt(user_id=user.id, station_id=station.id, version_id=v.id, mode=mode, persona=persona,
                       pressure=pressure, started_at=now,
                       deadline=(now + timedelta(seconds=v.time_limit_s)) if mode == "timed" else None,
                       transcript=[{"role": "examiner", "text": opening, "item": 0, "at": now.isoformat()}],
                       framework_pos=0)
    db.add(a)
    db.commit()
    return a


def time_left(a: StationAttempt) -> float | None:
    return None if a.deadline is None else (a.deadline - datetime.utcnow()).total_seconds()


def _follow_ups_used(a: StationAttempt, item: int) -> int:
    return sum(1 for t in a.transcript if t["role"] == "examiner" and t.get("item") == item and t.get("follow_up"))


TURN_PROMPT = """You are the examiner's assistant in a {mode} {specialty} viva. Persona: {persona}.
Current examiner question: {question}
Allowed follow-up questions (reviewed; you may only choose one of these, by index): {follow_ups}
Already asked follow-ups for this question: {used}
Rubric points this question is meant to elicit (for your judgement only, never quote them in timed mode):
{targets}
Candidate's answer: {answer}
{hint_line}
The examiner's NEXT questions (never answer, hint at or mention them; the candidate must reach them unaided):
{upcoming}
Teaching and hints are about the CURRENT question only.
Return JSON: {{"action": "follow_up" | "next", "follow_up_index": <index or null>,
 "teaching": "{teaching_rule}", "hint": "{hint_rule}"}}
Choose "follow_up" only when the answer is incomplete or unclear AND an allowed follow-up probes it."""


def candidate_turn(db: Session, a: StationAttempt, text: str, confidence: str | None, want_hint: bool = False,
                   *, llm: LLM | None = None) -> dict[str, Any]:
    """Record the candidate's answer and produce the examiner's next message."""
    if a.state != "active":
        raise ValueError("This attempt has ended")
    left = time_left(a)
    if left is not None and left <= 0:
        a.events = [*a.events, {"at": datetime.utcnow().isoformat(), "event": "time_up_on_answer"}]
        submit(db, a)
        return {"ended": True, "reason": "time"}
    v = db.get(StationVersion, a.version_id)
    station = db.get(Station, a.station_id)
    item = a.framework_pos
    fw = v.framework
    guided = a.mode == "guided"
    hint_given = guided and want_hint
    now = datetime.utcnow().isoformat()
    a.transcript = [*a.transcript, {"role": "candidate", "text": (text or "").strip()[:4000], "item": item,
                                    "confidence": confidence or "sure", "at": now}]

    decision: dict[str, Any] = {"action": "next"}
    current = fw[item] if item < len(fw) else None
    if current is not None:
        crit = [c.expected for c in criteria_of(db, v.id) if c.competency == current.get("competency")][:4]
        try:
            decision = (llm or _default_llm)([{"role": "user", "content": TURN_PROMPT.format(
                mode=a.mode, specialty=station.specialty, persona=a.persona, question=current["prompt"],
                follow_ups=json.dumps(current.get("follow_ups", [])), used=_follow_ups_used(a, item),
                targets="\n".join(f"- {t}" for t in crit) or "- (none)", answer=text,
                upcoming="\n".join(f"- {f['prompt']}" for f in fw[item + 1:]) or "- (none: this is the last question)",
                hint_line="The candidate asked for a hint." if hint_given else "",
                teaching_rule=("leave empty" if hint_given else "one or two sentences on what was right, missed or wrong in THIS answer, kind and specific; nothing about the next steps"
                               if guided else "leave empty"),
                hint_rule=("a nudge toward the missing point without giving it away" if hint_given else "leave empty"))}],
                role="fast", temperature=0.2, max_tokens=500, label="station-turn")
        except Exception as e:   # the examiner must keep going without the AI (timed integrity, Q23)
            logger.warning("Station turn AI unavailable for attempt %s: %s", a.id, e)
            decision = {"action": "next"}

    parts: list[str] = []
    follow = None
    if current is not None and decision.get("action") == "follow_up":
        idx = decision.get("follow_up_index")
        options = current.get("follow_ups", [])
        if isinstance(idx, int) and 0 <= idx < len(options) and _follow_ups_used(a, item) < 1:
            follow = options[idx]
    if guided and not hint_given and str(decision.get("teaching") or "").strip():   # a hint replaces teaching: it must not give the answer away
        parts.append(str(decision["teaching"]).strip())
    hinted = False
    if hint_given and current is not None and str(decision.get("hint") or "").strip():
        parts.append("Hint: " + str(decision["hint"]).strip())
        a.hints_used += 1
        a.transcript[-1]["hint_after"] = True
        hinted = True
    if not guided:
        parts.append(random.choice(TIMED_LEAD_INS.get(a.persona, TIMED_LEAD_INS["examiner"])))

    done = False
    if hinted:
        # A hint is for this question: ask it again so the candidate can use the hint (credit is then capped).
        parts.append("Try again: " + current["prompt"])
        msg_item, is_follow = item, False
    elif follow:
        parts.append(follow)
        msg_item, is_follow = item, True
    else:
        nxt = item + 1
        a.framework_pos = nxt
        stage = next((s["text"] for s in v.stages if s.get("after_item") == item), None)
        if stage:
            parts.append(stage)
        if nxt < len(fw):
            parts.append(fw[nxt]["prompt"] + (PRESSURE_SUFFIX.get(a.pressure, "") if not guided else ""))
        else:
            parts.append("That concludes the station. Submit when you are ready.")
            done = True
        msg_item, is_follow = nxt, False
    examiner = {"role": "examiner", "text": "\n\n".join(p for p in parts if p), "item": msg_item,
                "follow_up": is_follow, "at": datetime.utcnow().isoformat()}
    a.transcript = [*a.transcript, examiner]
    db.commit()
    return {"ended": False, "done": done, "examiner": examiner, "time_left": time_left(a)}


def submit(db: Session, a: StationAttempt) -> StationAttempt:
    """Idempotent: a second submit returns the same attempt unchanged."""
    if a.state != "active":
        return a
    a.submitted_at = datetime.utcnow()
    if a.deadline and a.submitted_at > a.deadline + timedelta(seconds=5):
        a.events = [*a.events, {"at": a.submitted_at.isoformat(), "event": "submitted_after_deadline"}]
    a.state = "submitted"
    db.commit()
    return a


# ─── Scoring ──────────────────────────────────────────────────────────────────

SCORE_PROMPT = """You are marking a {specialty} TOACS/viva station against its reviewed rubric. Mark ONLY what the
candidate actually said. Award credit for valid alternative answers. For each criterion return the marks, the
candidate's words that earned (or lost) them copied EXACTLY from the transcript (or "" if the candidate never
addressed it), a one-sentence rationale, your uncertainty, and whether the candidate stated an UNSAFE action
(something that would harm the patient, not merely an omission).

Station scenario: {scenario}

Rubric:
{rubric}

{examples}Candidate transcript (only the candidate's turns are marked):
{transcript}

Return JSON: {{"scores": [{{"criterion_id": <id>, "marks": <number>, "evidence_quote": "...", "turn": <turn number or null>,
 "rationale": "...", "uncertainty": "none" | "low" | "high", "unsafe": <true|false>}}]}}"""

SCORER_HASH = hashlib.sha1(SCORE_PROMPT.encode()).hexdigest()[:12]


def _rubric_text(criteria: list[StationCriterion]) -> str:
    return "\n".join(
        f"[{c.id}] ({c.competency}{', SAFETY-CRITICAL' if c.safety_critical else ''}, {c.marks:g} marks) {c.expected}"
        + (f" | also acceptable: {'; '.join(c.alternatives)}" if c.alternatives else "") for c in criteria)


def _candidate_turns(transcript: list[dict]) -> list[tuple[int, dict]]:
    return [(i, t) for i, t in enumerate(transcript) if t.get("role") == "candidate"]


def _prompted_turns(turns: list[tuple[int, dict]]) -> set[int]:
    """Candidate turns given after a hint on the same framework question (the hint follows the turn that asked)."""
    hinted: set = set()
    out: set[int] = set()
    for i, t in turns:
        if t.get("item") in hinted:
            out.add(i)
        if t.get("hint_after"):
            hinted.add(t.get("item"))
    return out


def _examples_text(db: Session, version_id: int, exclude: set[int], criteria: list[StationCriterion], k: int = 2) -> tuple[str, list[int]]:
    """Up to k expert-marked gold answers of this version as worked examples (few-shot), never the one being scored."""
    golds = (db.query(StationGold).filter(StationGold.version_id == version_id, StationGold.marked_at.isnot(None),
                                          ~StationGold.id.in_(exclude or {0}))
             .order_by(StationGold.id).limit(k).all())
    if not golds:
        return "", []
    blocks = []
    for g in golds:
        said = " / ".join(t["text"] for t in g.transcript if t.get("role") == "candidate")[:1500]
        marks = ", ".join(f"[{c.id}] {g.expert_marks.get(str(c.id), g.expert_marks.get(c.id, '?'))}/{c.marks:g}"
                          for c in criteria)
        blocks.append(f"Example ({g.label} answer, marked by an expert): \"{said}\"\nExpert marks: {marks}")
    return "Worked examples of how the expert marks this station:\n" + "\n\n".join(blocks) + "\n\n", [g.id for g in golds]


def score_transcript(db: Session, version: StationVersion, transcript: list[dict], specialty: str, *,
                     llm: LLM | None = None, exclude_gold: set[int] | None = None) -> tuple[list[dict], list[int]]:
    """Mark a transcript against the version's rubric. Returns per-criterion results (server-checked) and the
    gold examples shown to the scorer. Used for attempts and for calibration (leave-one-out via exclude_gold)."""
    criteria = criteria_of(db, version.id)
    turns = _candidate_turns(transcript)
    prompted_turns = _prompted_turns(turns)
    said = "\n".join(f"Turn {i}: {t['text']}" + (" [after a hint]" if i in prompted_turns else "") for i, t in turns)
    all_text = "\n".join(t["text"] for _, t in turns)
    examples, used = _examples_text(db, version.id, exclude_gold or set(), criteria)
    data = (llm or _default_llm)([{"role": "user", "content": SCORE_PROMPT.format(
        specialty=specialty, scenario=version.scenario, rubric=_rubric_text(criteria), examples=examples,
        transcript=said or "(the candidate did not answer)")}], role="chat", temperature=0.0, max_tokens=3000,
        label="station-score")
    got = {int(s["criterion_id"]): s for s in (data.get("scores") or [])
           if str(s.get("criterion_id", "")).isdigit()}

    out = []
    for c in criteria:
        s = got.get(c.id, {})
        marks = max(0.0, min(float(c.marks), float(s.get("marks") or 0)))
        quote = str(s.get("evidence_quote") or "")
        verified = quote_in(quote, all_text)
        uncertainty = s.get("uncertainty") if s.get("uncertainty") in ("none", "low", "high") else "low"
        flagged = False
        unsafe = bool(s.get("unsafe"))
        if c.id not in got:
            uncertainty, flagged = "high", True
        if marks > 0 and not verified:
            uncertainty, flagged = "high", True          # credit the server cannot trace to the candidate's words
        if unsafe and not verified:
            unsafe, flagged = False, True                # never apply an unsafe finding without the quote
        prompted = False
        turn = s.get("turn") if isinstance(s.get("turn"), int) else None
        if marks > 0 and turn is not None and turn in prompted_turns:
            capped = round(float(c.marks) * float(c.prompted_credit), 2)
            if marks > capped:
                marks, prompted = capped, True
            else:
                prompted = True
        if unsafe:
            marks = 0.0
        weak = marks < 0.5 * float(c.marks)
        out.append({
            "criterion_id": c.id, "marks_available": float(c.marks), "marks_awarded": round(marks, 2),
            "evidence": quote if verified else (quote or None), "evidence_verified": verified,
            "rationale": str(s.get("rationale") or "").strip()[:600], "uncertainty": uncertainty,
            "flagged": flagged, "prompted": prompted, "unsafe": unsafe,
            "error_class": ("safety" if unsafe else _error_class(c.competency)) if weak else None,
        })
    return out, used


def _error_class(competency: str) -> str:
    return {"knowledge": "knowledge_gap", "reasoning": "reasoning", "prioritisation": "prioritisation",
            "communication": "communication", "practical": "practical", "safety": "safety"}.get(competency, "knowledge_gap")


def score_attempt(db: Session, a: StationAttempt, *, llm: LLM | None = None) -> StationAttempt:
    """Mark a submitted attempt. Without the AI it stays 'scoring_pending' and is retried on the next debrief."""
    if a.state == "scored":
        return a
    if a.state == "active":
        submit(db, a)
    v = db.get(StationVersion, a.version_id)
    station = db.get(Station, a.station_id)
    try:
        results, used = score_transcript(db, v, a.transcript, station.specialty, llm=llm)
    except Exception as e:
        logger.warning("Scoring pending for attempt %s: %s", a.id, e)
        a.state = "scoring_pending"
        db.commit()
        return a
    db.query(StationScore).filter(StationScore.attempt_id == a.id).delete()
    for r in results:
        db.add(StationScore(attempt_id=a.id, criterion_id=r["criterion_id"], marks_available=r["marks_available"],
                            marks_awarded=r["marks_awarded"], evidence=r["evidence"], rationale=r["rationale"],
                            uncertainty=r["uncertainty"], flagged=r["flagged"], prompted=r["prompted"],
                            unsafe=r["unsafe"], error_class=r["error_class"]))
    a.marks_available = sum(r["marks_available"] for r in results)
    a.marks_awarded = round(sum(r["marks_awarded"] for r in results), 2)
    a.state = "scored"
    a.scored_at = datetime.utcnow()
    a.scorer_meta = {"scorer_hash": SCORER_HASH, "gold_examples": used, "readiness": station.readiness}
    a.result_note = _result_note(a, v, results)
    pct = (a.marks_awarded / a.marks_available) if a.marks_available else 0.0
    db.add(AnswerEvent(user_id=a.user_id, is_correct=pct >= 0.6, confidence="sure", subject=station.specialty,
                       source="toacs", session_ref=f"station:{a.id}",
                       mistake_type=None if pct >= 0.6 else "gap"))
    db.commit()
    return a


def _result_note(a: StationAttempt, v: StationVersion, results: list[dict]) -> str:
    pct = (a.marks_awarded / a.marks_available * 100) if a.marks_available else 0
    note = f"{a.marks_awarded:g} of {a.marks_available:g} marks ({pct:.0f}%). {SIMULATED_NOTE}"
    if any(r["unsafe"] for r in results):
        note += " A critical safety concern was found; see the safety findings."
    if v.pass_mark is not None and not any(r["flagged"] for r in results):
        note += " At or above the station's pass mark." if pct >= v.pass_mark else " Below the station's pass mark."
    elif any(r["flagged"] for r in results):
        note += " Some marks are uncertain and flagged for review, so no pass/fail is given."
    return note


# ─── Debrief ──────────────────────────────────────────────────────────────────

def debrief(db: Session, a: StationAttempt) -> dict[str, Any]:
    v = db.get(StationVersion, a.version_id)
    station = db.get(Station, a.station_id)
    crit = {c.id: c for c in criteria_of(db, v.id)}
    scores = db.query(StationScore).filter(StationScore.attempt_id == a.id).all()
    rows = []
    by_comp: dict[str, list[float]] = {}
    for s in sorted(scores, key=lambda s: crit[s.criterion_id].ordinal if s.criterion_id in crit else 0):
        c = crit.get(s.criterion_id)
        if c is None:
            continue
        by_comp.setdefault(c.competency, [0.0, 0.0])
        by_comp[c.competency][0] += s.marks_awarded
        by_comp[c.competency][1] += s.marks_available
        rows.append({"score_id": s.id, "criterion_id": c.id, "competency": c.competency, "expected": c.expected,
                     "alternatives": c.alternatives, "safety_critical": c.safety_critical,
                     "marks_available": s.marks_available, "marks_awarded": s.marks_awarded,
                     "evidence": s.evidence, "rationale": s.rationale, "uncertainty": s.uncertainty,
                     "flagged": s.flagged, "prompted": s.prompted, "unsafe": s.unsafe, "error_class": s.error_class,
                     "source": c.source_ref})
    errors: dict[str, int] = {}
    for r in rows:
        if r["error_class"]:
            errors[r["error_class"]] = errors.get(r["error_class"], 0) + 1
    pct = (a.marks_awarded / a.marks_available) if a.marks_available else 0.0
    strong = pct >= 0.75 and not any(r["unsafe"] for r in rows)
    learn = []
    if errors.get("knowledge_gap") and station.tos_topic_id:
        learn.append({"kind": "notes", "label": "Revise this topic's notes", "exam_id": station.tos_exam_id,
                      "paper_id": station.tos_paper_id, "topic_id": station.tos_topic_id})
    if errors:
        learn.append({"kind": "repeat", "label": "Repeat this station", "station_id": station.id,
                      "after_days": REPEAT_AFTER_DAYS[strong]})
    return {
        "attempt_id": a.id, "station_id": station.id, "title": station.title, "mode": a.mode, "persona": a.persona,
        "state": a.state, "readiness": station.readiness,
        "marks_awarded": a.marks_awarded, "marks_available": a.marks_available, "result_note": a.result_note,
        "simulated": True, "hints_used": a.hints_used,
        "competencies": [{"competency": k, "awarded": round(x[0], 2), "available": x[1]} for k, x in by_comp.items()],
        "criteria": rows,
        "safety_findings": [r for r in rows if r["unsafe"] or (r["safety_critical"] and r["marks_awarded"] < r["marks_available"])],
        "error_classes": errors,
        "model_answer": v.model_answer, "sources": v.sources, "transcript": a.transcript,
        "learn": learn, "repeat_after_days": REPEAT_AFTER_DAYS[strong],
        "practice_only": station.readiness != "assessment_ready",
    }


# ─── Calibration (expert gold answers) ────────────────────────────────────────

def calibration_passed(db: Session, version_id: int) -> bool:
    run = (db.query(StationCalibration).filter(StationCalibration.version_id == version_id)
           .order_by(StationCalibration.at.desc()).first())
    return bool(run and run.passed and run.scorer_hash == SCORER_HASH)


def calibration_metrics(pairs: list[tuple[StationCriterion, float, float, bool]]) -> dict[str, Any]:
    """pairs: (criterion, expert marks, AI marks, AI said unsafe). Errors are fractions of each criterion's marks."""
    if not pairs:
        return {"n": 0, "mae": None, "agreement": None, "safety_misses": 0, "false_flags": 0, "per_criterion": {}}
    errs, agree, misses, false_flags = [], 0, 0, 0
    per: dict[int, list[float]] = {}
    for c, expert, ai, unsafe in pairs:
        e = abs(expert - ai) / float(c.marks)
        errs.append(e)
        per.setdefault(c.id, []).append(e)
        agree += e <= 0.25
        if c.safety_critical and expert < c.marks and ai >= c.marks:
            misses += 1                                     # the expert deducted a safety point the AI missed
        if unsafe and expert >= c.marks:
            false_flags += 1                                # the AI called it unsafe where the expert gave full marks
    return {"n": len(pairs), "mae": round(sum(errs) / len(errs), 3), "agreement": round(agree / len(pairs), 3),
            "safety_misses": misses, "false_flags": false_flags,
            "per_criterion": {str(k): round(sum(v) / len(v), 3) for k, v in per.items()}}


def run_calibration(db: Session, version: StationVersion, actor: str, *, llm: LLM | None = None,
                    thresholds: dict | None = None) -> StationCalibration:
    """Score every expert-marked gold answer of this version (leave-one-out examples) and compare with the expert."""
    th = {**CALIBRATION_THRESHOLDS, **(thresholds or {})}
    station = db.get(Station, version.station_id)
    criteria = {c.id: c for c in criteria_of(db, version.id)}
    golds = db.query(StationGold).filter(StationGold.version_id == version.id, StationGold.marked_at.isnot(None)).all()
    pairs = []
    for g in golds:
        results, _ = score_transcript(db, version, g.transcript, station.specialty, llm=llm, exclude_gold={g.id})
        for r in results:
            c = criteria[r["criterion_id"]]
            expert = g.expert_marks.get(str(c.id), g.expert_marks.get(c.id))
            if expert is None:
                continue
            pairs.append((c, float(expert), r["marks_awarded"], r["unsafe"]))
    m = calibration_metrics(pairs)
    passed = (len(golds) >= th["min_gold"] and m["mae"] is not None and m["mae"] <= th["max_mae"]
              and m["agreement"] >= th["min_agreement"] and m["safety_misses"] <= th["max_safety_misses"])
    run = StationCalibration(version_id=version.id, scorer_hash=SCORER_HASH, n_gold=len(golds), metrics=m,
                             thresholds=th, passed=passed, run_by=actor)
    db.add(run)
    if station.status == "published" and station.current_version_id == version.id:
        station.readiness = "assessment_ready" if passed else "practice_only"
    audit(db, actor, "calibrate", "station_version", version.id, passed=passed, metrics=m)
    db.commit()
    return run


SAMPLE_PROMPT = """Write three realistic candidate performances for this {specialty} viva station, to be marked by an
expert examiner: one STRONG, one BORDERLINE (some gaps, nothing dangerous) and one UNSAFE (includes at least one
clearly unsafe decision a weak trainee might make). Answer each examiner question in turn, in the candidate's own
words (1-4 sentences each). Do not label the answers or explain them.

Scenario: {scenario}
Examiner questions, in order:
{questions}

Return JSON: {{"strong": ["answer to Q1", ...], "borderline": [...], "unsafe": [...]}}"""


def generate_samples(db: Session, version: StationVersion, actor: str, *, llm: LLM | None = None) -> list[StationGold]:
    """AI-written candidate answers for the expert to mark (gold answers start unmarked: the AI never marks them)."""
    station = db.get(Station, version.station_id)
    qs = [item["prompt"] for item in version.framework]
    data = (llm or _default_llm)([{"role": "user", "content": SAMPLE_PROMPT.format(
        specialty=station.specialty, scenario=version.scenario,
        questions="\n".join(f"Q{i + 1}. {q}" for i, q in enumerate(qs)))}], role="chat", temperature=0.7,
        max_tokens=2500, label="station-samples")
    made = []
    for label in ("strong", "borderline", "unsafe"):
        answers = [str(x).strip() for x in (data.get(label) or []) if str(x).strip()]
        if not answers:
            continue
        transcript = []
        for i, q in enumerate(qs):
            transcript.append({"role": "examiner", "text": q, "item": i})
            if i < len(answers):
                transcript.append({"role": "candidate", "text": answers[i], "item": i})
        g = StationGold(version_id=version.id, label=label, transcript=transcript, origin="ai_sample")
        db.add(g)
        made.append(g)
    audit(db, actor, "samples", "station_version", version.id, n=len(made))
    db.commit()
    return made


def mark_gold(db: Session, gold: StationGold, marks: dict, actor: str) -> StationGold:
    """The expert's marks for every criterion of the gold answer's version (each within the criterion's marks)."""
    crit = {c.id: c for c in criteria_of(db, gold.version_id)}
    clean = {}
    for cid, c in crit.items():
        val = marks.get(str(cid), marks.get(cid))
        if val is None:
            raise ValueError(f"Missing a mark for criterion {c.ordinal + 1}")
        val = float(val)
        if not 0 <= val <= c.marks:
            raise ValueError(f"Criterion {c.ordinal + 1}: marks must be between 0 and {c.marks:g}")
        clean[str(cid)] = val
    gold.expert_marks, gold.marked_by, gold.marked_at = clean, actor, datetime.utcnow()
    audit(db, actor, "mark_gold", "station_gold", gold.id, marks=clean)
    db.commit()
    return gold


# ─── Learner guidance: a suggested level and next stations (shown with its reason, never forced) ─────

def learner_level(db: Session, user_id: int, specialty: str | None = None) -> dict[str, Any]:
    """A suggested station difficulty from this learner's own scored stations (Q10: progression by demonstrated
    competence). It is advice shown with its reason; it never changes a station or a timed attempt."""
    q = (db.query(StationAttempt, StationVersion.difficulty).join(StationVersion, StationVersion.id == StationAttempt.version_id)
         .join(Station, Station.id == StationAttempt.station_id)
         .filter(StationAttempt.user_id == user_id, StationAttempt.state == "scored"))
    if specialty:
        q = q.filter(Station.specialty == specialty)
    recent = q.order_by(StationAttempt.scored_at.desc()).limit(5).all()
    if not recent:
        return {"level": 2, "reason": "No stations marked yet: start at level 2 and work up.", "recent": 0}
    pcts = [(a.marks_awarded or 0) / a.marks_available for a, _ in recent if a.marks_available]
    avg_diff = sum(d for _, d in recent) / len(recent)
    avg_pct = sum(pcts) / len(pcts) if pcts else 0
    level = round(avg_diff)
    if avg_pct >= 0.75 and len(recent) >= 2:
        level, why = min(5, level + 1), f"you averaged {avg_pct:.0%} on your last {len(recent)} stations"
    elif avg_pct < 0.5:
        level, why = max(1, level - 1), f"your last {len(recent)} stations averaged {avg_pct:.0%}: consolidate first"
    else:
        why = f"your last {len(recent)} stations averaged {avg_pct:.0%}"
    return {"level": level, "reason": f"Suggested level {level}: {why}.", "recent": len(recent)}


# ─── Circuits: several timed stations in a row (stored as a study_sessions row, kind 'toacs_circuit') ──

CIRCUIT_NOTE = ("A practice circuit of stations sat back to back. Provisional: there is no official station count or "
                "circuit blueprint for this specialty here, so it is not a simulation of a specific examination.")


def pick_circuit(db: Session, user_id: int, specialty: str, n: int) -> list[Station]:
    """n published stations, one per TOS topic where possible, least recently sat by this learner first."""
    stations = db.query(Station).filter(Station.status == "published", Station.specialty == specialty).all()
    last = {sid: at for sid, at in db.query(StationAttempt.station_id, StationAttempt.started_at)
            .filter(StationAttempt.user_id == user_id).order_by(StationAttempt.started_at)}
    stations.sort(key=lambda s: (last.get(s.id) is not None, last.get(s.id) or datetime.min, random.random()))
    picked, topics = [], set()
    for s in stations:                       # first pass: distinct topics
        if s.tos_topic_id not in topics:
            picked.append(s)
            topics.add(s.tos_topic_id)
        if len(picked) == n:
            return picked
    for s in stations:                       # then fill up if there are fewer topics than stations wanted
        if s not in picked:
            picked.append(s)
        if len(picked) == n:
            break
    return picked


def circuit_summary(db: Session, items: list[dict]) -> dict[str, Any]:
    rows, awarded, available = [], 0.0, 0.0
    for i, it in enumerate(items):
        st = db.get(Station, it["station_id"])
        a = db.get(StationAttempt, it["attempt_id"]) if it.get("attempt_id") else None
        rows.append({"index": i, "station_id": it["station_id"], "title": st.title if st else "?",
                     "attempt_id": a.id if a else None,
                     "state": a.state if a else ("skipped" if it.get("skipped") else "not_started"),
                     "marks_awarded": a.marks_awarded if a else None, "marks_available": a.marks_available if a else None})
        if a and a.state == "scored" and a.marks_available:
            awarded += a.marks_awarded or 0
            available += a.marks_available
    nxt = next((r["index"] for r in rows if r["state"] == "not_started"), None)
    done = nxt is None and all(r["state"] in ("submitted", "scoring_pending", "scored", "skipped") for r in rows)
    return {"stations": rows, "next_index": nxt, "done": done, "marks_awarded": round(awarded, 2),
            "marks_available": available, "note": CIRCUIT_NOTE}


def promote_to_gold(db: Session, attempt: StationAttempt, actor: str, corrections: dict[int, float],
                    label: str = "borderline", origin: str = "challenge") -> StationGold:
    """A reviewed attempt becomes a gold answer: the expert's marks are the AI marks with the reviewer's corrections."""
    marks = {str(s.criterion_id): (corrections.get(s.criterion_id, s.marks_awarded))
             for s in db.query(StationScore).filter(StationScore.attempt_id == attempt.id)}
    g = StationGold(version_id=attempt.version_id, label=label, transcript=attempt.transcript, expert_marks=marks,
                    marked_by=actor, marked_at=datetime.utcnow(), origin=origin)
    db.add(g)
    db.flush()
    audit(db, actor, "promote_gold", "station_attempt", attempt.id, gold_id=g.id, corrections=corrections)
    db.commit()
    return g


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()
