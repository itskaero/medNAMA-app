"""TOACS / viva stations: the learner's library, attempts, examiner turns, debrief and score challenges, and the
admin's review, publication and challenge triage. The engine is app/stations.py; drafting and calibration run in
Seed Studio on the PC (labs/seed_studio/stations_studio.py), never on the NAS.

Learners only ever see published stations, and never a station's answers, rubric or model answer before their
own attempt is submitted. Timed attempts follow the server clock and are never extended.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import stations as S
from app.auth import require_admin, require_student_or_admin
from app.deps import get_db
from app.models import StudySession, User
from app.station_models import (MODES, PERSONAS, PRESSURES, Station, StationAttempt, StationAudit,
                                StationCalibration, StationChallenge, StationGold, StationScore, StationVersion)

router = APIRouter()


def _station_summary(db: Session, st: Station, user_id: int | None = None) -> dict[str, Any]:
    v = db.get(StationVersion, st.current_version_id) if st.current_version_id else None
    out: dict[str, Any] = {
        "id": st.id, "title": st.title, "specialty": st.specialty, "station_type": st.station_type,
        "status": st.status, "readiness": st.readiness,
        "tos": {"exam_id": st.tos_exam_id, "paper_id": st.tos_paper_id, "topic_id": st.tos_topic_id},
        "topic": _topic_name(st), "time_limit_s": v.time_limit_s if v else None, "difficulty": v.difficulty if v else None,
    }
    if user_id is not None:
        last = (db.query(StationAttempt).filter(StationAttempt.user_id == user_id, StationAttempt.station_id == st.id)
                .order_by(StationAttempt.started_at.desc()).first())
        if last:
            pct = (last.marks_awarded / last.marks_available * 100) if last.marks_available else None
            due = None
            if last.state == "scored" and last.scored_at:
                strong = (pct or 0) >= 75
                due = (last.scored_at + S.timedelta(days=S.REPEAT_AFTER_DAYS[strong])).isoformat()
            out["last_attempt"] = {"id": last.id, "state": last.state, "mode": last.mode,
                                   "percent": round(pct) if pct is not None else None,
                                   "at": last.started_at.isoformat(), "repeat_due": due}
    return out


def _topic_name(st: Station) -> str | None:
    from app.tos import find_topic, tos_registry

    paper = tos_registry.get_paper(st.tos_exam_id or "", st.tos_paper_id or "")
    found = find_topic(paper, st.tos_topic_id or "") if paper else None
    return f"{found[0]['subject_name']} · {found[1]['topic_name']}" if found else None


def _attempt_payload(db: Session, a: StationAttempt) -> dict[str, Any]:
    """What the candidate may see of an attempt: the transcript and the clock, nothing about correctness."""
    v = db.get(StationVersion, a.version_id)
    st = db.get(Station, a.station_id)
    return {
        "id": a.id, "station_id": st.id, "title": st.title, "mode": a.mode, "persona": a.persona, "pressure": a.pressure,
        "state": a.state, "transcript": a.transcript, "hints_used": a.hints_used,
        "deadline": a.deadline.isoformat() if a.deadline else None, "server_now": datetime.utcnow().isoformat(),
        "time_left": S.time_left(a), "time_limit_s": v.time_limit_s,
        "instructions": v.instructions_guided if a.mode == "guided" else v.instructions_timed,
        "items_total": len(v.framework), "item": a.framework_pos, "readiness": st.readiness,
    }


def _own_attempt(db: Session, attempt_id: int, user: User) -> StationAttempt:
    a = db.get(StationAttempt, attempt_id)
    if a is None or a.user_id != user.id:
        raise HTTPException(status_code=404, detail="Attempt not found")
    return a


# ─── Learner ──────────────────────────────────────────────────────────────────

@router.get("/api/stations")
def list_stations(specialty: str | None = Query(None), db: Session = Depends(get_db),
                  user: User = Depends(require_student_or_admin)):
    q = db.query(Station).filter(Station.status == "published")
    if specialty:
        q = q.filter(Station.specialty == specialty)
    items = [_station_summary(db, s, user.id) for s in q.order_by(Station.specialty, Station.tos_topic_id, Station.id)]
    # Suggested level per specialty (Q10), shown with its reason; stations near it and not yet sat (or due a
    # repeat) are marked "suggested". Learners still choose freely.
    levels = {sp: S.learner_level(db, user.id, sp) for sp in {s["specialty"] for s in items}}
    now = datetime.utcnow().isoformat()
    for s in items:
        lv = levels[s["specialty"]]["level"]
        last = s.get("last_attempt")
        fresh = last is None or bool(last.get("repeat_due") and last["repeat_due"] <= now)
        s["suggested"] = bool(fresh and s["difficulty"] is not None and lv - 1 <= s["difficulty"] <= lv)
        s["_fresh"] = fresh
    # Nothing at the suggested level (e.g. only harder stations published yet): suggest the easiest fresh ones.
    for sp in levels:
        mine = [s for s in items if s["specialty"] == sp]
        if not any(s["suggested"] for s in mine):
            fresh = [s for s in mine if s["_fresh"] and s["difficulty"] is not None]
            if fresh:
                low = min(s["difficulty"] for s in fresh)
                for s in fresh:
                    s["suggested"] = s["difficulty"] == low
    for s in items:
        s.pop("_fresh", None)
    return {"stations": items, "specialties": sorted({s["specialty"] for s in items}), "levels": levels,
            "note": S.SIMULATED_NOTE, "circuit_note": S.CIRCUIT_NOTE}


@router.get("/api/stations/{station_id}")
def station_detail(station_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    st = db.get(Station, station_id)
    if st is None or st.status != "published":
        raise HTTPException(status_code=404, detail="Station not found")
    v = db.get(StationVersion, st.current_version_id)
    return {**_station_summary(db, st, user.id),
            "instructions_guided": v.instructions_guided, "instructions_timed": v.instructions_timed,
            "items_total": len(v.framework)}


class StartReq(BaseModel):
    mode: str = "guided"
    persona: str = "examiner"
    pressure: str = "realistic"


@router.post("/api/stations/{station_id}/attempts")
def start(station_id: int, req: StartReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    if req.mode not in MODES or req.persona not in PERSONAS or req.pressure not in PRESSURES:
        raise HTTPException(status_code=422, detail="Unknown mode, persona or pressure")
    st = db.get(Station, station_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        a = S.start_attempt(db, db.get(User, user.id), st, req.mode, req.persona, req.pressure)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _attempt_payload(db, a)


@router.get("/api/station-attempts/{attempt_id}")
def resume(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    """Reload an attempt (after a refresh or a lost connection). A timed one past its deadline is submitted."""
    a = _own_attempt(db, attempt_id, user)
    if a.state == "active":
        a.events = [*a.events, {"at": datetime.utcnow().isoformat(), "event": "resumed"}]
        left = S.time_left(a)
        if left is not None and left <= 0:
            S.submit(db, a)
        else:
            db.commit()
    return _attempt_payload(db, a)


class TurnReq(BaseModel):
    text: str = Field("", max_length=4000)
    confidence: str = "sure"
    hint: bool = False


@router.post("/api/station-attempts/{attempt_id}/turn")
def turn(attempt_id: int, req: TurnReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    a = _own_attempt(db, attempt_id, user)
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="Write an answer first")
    try:
        out = S.candidate_turn(db, a, req.text, req.confidence, want_hint=req.hint and a.mode == "guided")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {**out, "attempt": _attempt_payload(db, a)}


class EventReq(BaseModel):
    event: str = Field(..., max_length=60)   # hidden | visible | offline | online


@router.post("/api/station-attempts/{attempt_id}/events")
def event(attempt_id: int, req: EventReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    """Interruptions and recovery are recorded (§18), never used to stop or extend the clock."""
    a = _own_attempt(db, attempt_id, user)
    a.events = [*a.events, {"at": datetime.utcnow().isoformat(), "event": req.event}][-200:]
    db.commit()
    return {"ok": True}


@router.post("/api/station-attempts/{attempt_id}/submit")
def submit(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    """Idempotent: submitting twice returns the same debrief."""
    a = _own_attempt(db, attempt_id, user)
    S.submit(db, a)
    S.score_attempt(db, a)
    return S.debrief(db, a)


@router.get("/api/station-attempts/{attempt_id}/debrief")
def get_debrief(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    a = _own_attempt(db, attempt_id, user)
    if a.state == "active":
        raise HTTPException(status_code=409, detail="Submit the station first")
    if a.state in ("submitted", "scoring_pending"):
        S.score_attempt(db, a)   # retry marking when the AI was unavailable at submit
    return S.debrief(db, a)


@router.get("/api/station-attempts")
def my_attempts(db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    rows = (db.query(StationAttempt, Station.title).join(Station, Station.id == StationAttempt.station_id)
            .filter(StationAttempt.user_id == user.id).order_by(StationAttempt.started_at.desc()).limit(50).all())
    return [{"id": a.id, "station_id": a.station_id, "title": t, "mode": a.mode, "state": a.state,
             "marks_awarded": a.marks_awarded, "marks_available": a.marks_available, "at": a.started_at.isoformat()}
            for a, t in rows]


class ChallengeReq(BaseModel):
    score_id: int
    rationale: str = Field(..., min_length=10, max_length=2000)
    source: str | None = Field(None, max_length=500)


@router.post("/api/station-attempts/{attempt_id}/challenges")
def challenge(attempt_id: int, req: ChallengeReq, db: Session = Depends(get_db),
              user: User = Depends(require_student_or_admin)):
    a = _own_attempt(db, attempt_id, user)
    sc = db.get(StationScore, req.score_id)
    if sc is None or sc.attempt_id != a.id:
        raise HTTPException(status_code=404, detail="That mark is not part of this attempt")
    if db.query(StationChallenge).filter(StationChallenge.score_id == sc.id, StationChallenge.status == "open").first():
        raise HTTPException(status_code=409, detail="This mark is already under review")
    ch = StationChallenge(attempt_id=a.id, score_id=sc.id, user_id=user.id, rationale=req.rationale.strip(),
                          source=(req.source or "").strip() or None)
    db.add(ch)
    S.audit(db, user.username, "challenge", "station_score", sc.id, attempt_id=a.id)
    db.commit()
    return {"id": ch.id, "status": ch.status}


@router.get("/api/station-attempts/{attempt_id}/challenges")
def my_challenges(attempt_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    a = _own_attempt(db, attempt_id, user)
    return [{"id": c.id, "score_id": c.score_id, "status": c.status, "rationale": c.rationale,
             "reviewer_note": c.reviewer_note, "corrected_marks": c.corrected_marks}
            for c in db.query(StationChallenge).filter(StationChallenge.attempt_id == a.id)]


# ─── Circuits: several timed stations in a row ────────────────────────────────

class CircuitReq(BaseModel):
    specialty: str
    n: int = Field(4, ge=2, le=8)
    persona: str = "examiner"
    pressure: str = "realistic"


def _own_circuit(db: Session, circuit_id: int, user: User) -> StudySession:
    c = db.get(StudySession, circuit_id)
    if c is None or c.user_id != user.id or c.kind != "toacs_circuit":
        raise HTTPException(status_code=404, detail="Circuit not found")
    return c


def _start_next(db: Session, c: StudySession, user: User) -> int | None:
    """Start the next station of the circuit (timed). The previous one must have been submitted first."""
    items = [dict(i) for i in c.items]
    for i, it in enumerate(items):
        if it.get("skipped"):
            continue
        if it.get("attempt_id"):
            prev = db.get(StationAttempt, it["attempt_id"])
            if prev and prev.state == "active":
                return prev.id                   # still sitting this one
            continue
        st = db.get(Station, it["station_id"])
        if st is None or st.status != "published":
            items[i]["skipped"] = True          # unpublished since the circuit started
            c.items = items
            continue
        a = S.start_attempt(db, db.get(User, user.id), st, "timed", it["persona"], it["pressure"])
        items[i]["attempt_id"] = a.id
        c.items = items
        db.commit()
        return a.id
    c.completed_at = c.completed_at or datetime.utcnow()
    db.commit()
    return None


@router.post("/api/station-circuits")
def start_circuit(req: CircuitReq, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    if req.persona not in PERSONAS or req.pressure not in PRESSURES:
        raise HTTPException(status_code=422, detail="Unknown persona or pressure")
    picked = S.pick_circuit(db, user.id, req.specialty, req.n)
    if len(picked) < 2:
        raise HTTPException(status_code=409, detail="Fewer than 2 published stations in this specialty")
    c = StudySession(user_id=user.id, kind="toacs_circuit", day=datetime.utcnow().date(),
                     items=[{"station_id": s.id, "attempt_id": None, "persona": req.persona, "pressure": req.pressure}
                            for s in picked])
    db.add(c)
    db.commit()
    first = _start_next(db, c, user)
    return {"circuit_id": c.id, "attempt_id": first, "stations": len(picked), "note": S.CIRCUIT_NOTE}


@router.get("/api/station-circuits/{circuit_id}")
def get_circuit(circuit_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    c = _own_circuit(db, circuit_id, user)
    return {"id": c.id, **S.circuit_summary(db, c.items)}


@router.post("/api/station-circuits/{circuit_id}/next")
def next_station(circuit_id: int, db: Session = Depends(get_db), user: User = Depends(require_student_or_admin)):
    c = _own_circuit(db, circuit_id, user)
    attempt_id = _start_next(db, c, user)
    return {"attempt_id": attempt_id, **S.circuit_summary(db, c.items)}


# ─── Admin: review, publication, challenges ──────────────────────────────────

@router.get("/api/admin/stations")
def admin_list(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = []
    for st in db.query(Station).order_by(Station.updated_at.desc()).all():
        latest = S.latest_version(db, st)
        flagged = (db.query(func.count()).select_from(S.StationCriterion)
                   .filter(S.StationCriterion.version_id == latest.id, S.StationCriterion.flagged).scalar()) if latest else 0
        open_ch = (db.query(func.count()).select_from(StationChallenge).join(StationAttempt)
                   .filter(StationAttempt.station_id == st.id, StationChallenge.status == "open").scalar())
        rows.append({**_station_summary(db, st), "latest_version": latest.version if latest else None,
                     "latest_approved": bool(latest and latest.approved_at), "flagged_criteria": flagged,
                     "open_challenges": open_ch, "provenance": st.provenance,
                     "calibrated": S.calibration_passed(db, latest.id) if latest else False})
    return rows


@router.get("/api/admin/stations/{station_id}")
def admin_detail(station_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    st = db.get(Station, station_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Station not found")
    versions = (db.query(StationVersion).filter(StationVersion.station_id == st.id)
                .order_by(StationVersion.version.desc()).all())
    cal = {r.version_id: r for r in db.query(StationCalibration).filter(
        StationCalibration.version_id.in_([v.id for v in versions] or [0])).order_by(StationCalibration.at)}
    return {**_station_summary(db, st), "current_version_id": st.current_version_id,
            "versions": [{**S.version_payload(db, v, with_answers=True),
                          "gold": db.query(func.count()).select_from(StationGold).filter(
                              StationGold.version_id == v.id, StationGold.marked_at.isnot(None)).scalar(),
                          "calibration": ({"passed": cal[v.id].passed, "metrics": cal[v.id].metrics,
                                           "at": cal[v.id].at.isoformat()} if v.id in cal else None)}
                         for v in versions],
            "audit": [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "detail": a.detail}
                      for a in db.query(StationAudit).filter(StationAudit.entity.in_(("station", "station_version")))
                      .filter(StationAudit.entity_id.in_([str(st.id), *[str(v.id) for v in versions]]))
                      .order_by(StationAudit.at.desc()).limit(50)]}


class VersionReq(BaseModel):
    content: dict


@router.post("/api/admin/stations/{station_id}/versions")
def admin_revise(station_id: int, req: VersionReq, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """An edit always creates a new version: approved and published versions are never changed."""
    st = db.get(Station, station_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        v = S.revise_station(db, st, req.content, admin.username)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid station content: {e}")
    return S.version_payload(db, v, with_answers=True)


class ApproveReq(BaseModel):
    note: str | None = None


@router.post("/api/admin/stations/{station_id}/versions/{version_id}/approve")
def admin_approve(station_id: int, version_id: int, req: ApproveReq, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin)):
    st, v = db.get(Station, station_id), db.get(StationVersion, version_id)
    if st is None or v is None:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        S.approve_version(db, st, v, admin.username, req.note)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "status": st.status}


@router.post("/api/admin/stations/{station_id}/publish")
def admin_publish(station_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    st = db.get(Station, station_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        S.publish(db, st, admin.username)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "status": st.status, "readiness": st.readiness}


class StatusReq(BaseModel):
    status: str


@router.post("/api/admin/stations/{station_id}/status")
def admin_status(station_id: int, req: StatusReq, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    st = db.get(Station, station_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        S.set_status(db, st, req.status, admin.username)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "status": st.status}


@router.get("/api/admin/station-challenges")
def admin_challenges(status: str = Query("open"), db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = (db.query(StationChallenge, StationScore, StationAttempt, Station)
            .join(StationScore, StationScore.id == StationChallenge.score_id)
            .join(StationAttempt, StationAttempt.id == StationChallenge.attempt_id)
            .join(Station, Station.id == StationAttempt.station_id)
            .filter(StationChallenge.status == status).order_by(StationChallenge.created_at).limit(100).all())
    crit = {c.id: c for c in db.query(S.StationCriterion).filter(
        S.StationCriterion.id.in_([sc.criterion_id for _, sc, _, _ in rows] or [0]))}
    return [{"id": ch.id, "station": st.title, "attempt_id": a.id, "rationale": ch.rationale, "source": ch.source,
             "criterion": crit[sc.criterion_id].expected if sc.criterion_id in crit else None,
             "marks_available": sc.marks_available, "marks_awarded": sc.marks_awarded, "evidence": sc.evidence,
             "ai_rationale": sc.rationale, "status": ch.status, "created_at": ch.created_at.isoformat()}
            for ch, sc, a, st in rows]


class ResolveReq(BaseModel):
    decision: str                          # upheld | rejected
    corrected_marks: float | None = None
    note: str = Field(..., min_length=3, max_length=2000)
    promote_to_gold: bool = False          # use the corrected attempt as an expert-marked calibration example


@router.post("/api/admin/station-challenges/{challenge_id}")
def admin_resolve(challenge_id: int, req: ResolveReq, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Record the reviewer's decision. The original score row is never edited (Q25)."""
    ch = db.get(StationChallenge, challenge_id)
    if ch is None or ch.status != "open":
        raise HTTPException(status_code=404, detail="No open challenge with that id")
    if req.decision not in ("upheld", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be upheld or rejected")
    sc = db.get(StationScore, ch.score_id)
    if req.decision == "upheld":
        if req.corrected_marks is None or not 0 <= req.corrected_marks <= sc.marks_available:
            raise HTTPException(status_code=422, detail="Give the corrected marks for an upheld challenge")
    ch.status, ch.reviewer, ch.reviewer_note = req.decision, admin.username, req.note
    ch.corrected_marks = req.corrected_marks if req.decision == "upheld" else None
    ch.resolved_at = datetime.utcnow()
    S.audit(db, admin.username, f"challenge_{req.decision}", "station_challenge", ch.id,
            corrected=ch.corrected_marks, original=sc.marks_awarded)
    db.commit()
    gold_id = None
    if req.promote_to_gold:
        a = db.get(StationAttempt, ch.attempt_id)
        corrections = {sc.criterion_id: ch.corrected_marks} if ch.corrected_marks is not None else {}
        gold_id = S.promote_to_gold(db, a, admin.username, corrections).id
    return {"ok": True, "status": ch.status, "gold_id": gold_id}
