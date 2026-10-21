"""Station Studio: author TOACS / viva stations and calibrate their AI scoring, on this PC.

  draft      AI-written station + examiner questions + rubric for a TOS topic, grounded in the specialty's textbooks
             (every criterion cites a passage quote; unverified ones are flagged). Saved as a draft.
  review     edit (each edit is a new version), approve, then publish to learners.
  calibrate  AI-written sample answers -> the expert marks them (gold) -> the AI scorer runs over the gold answers
             -> agreement metrics. A published station is "assessment-ready" only after calibration passes;
             before that learners see it as practice-only.

The engine is app/stations.py, shared with the app. Drafting and calibration call the AI from here, never the NAS.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func

from . import settings  # noqa: F401  (backend import path)
from .common import session

from app import stations as S  # noqa: E402
from app.station_models import Station, StationCalibration, StationCriterion, StationGold  # noqa: E402
from app.tos import tos_registry  # noqa: E402

logger = logging.getLogger("seed_studio.stations")
router = APIRouter()
RUN: dict[str, Any] = {"running": False}
_LOCK = threading.Lock()

# TOS paper -> the textbook subject its stations are grounded in. Only specialties with their own textbooks in the
# library are listed (Pathology, Radiology, Psychiatry, Anaesthesia and the dental papers have none yet).
PAPERS = {
    ("pget", "p2-paediatric-medicine"): "Paediatrics",                     # Nelson Vol 1 + 2 (the pilot)
    ("pget", "p2-medicine-allied"): "Medicine",                            # Davidson
    ("pget", "p2-obstetrics-and-gynaecology"): "Gynaecology & Obstetrics",  # Ten Teachers O + G
    ("pget", "p2-otorhinolaryngology-ent"): "ENT",                         # Dhingra
    ("pget", "p2-ophthalmology"): "Ophthalmology",                         # Jatoi
    ("pget", "p2-surgery-allied-group-1-general-surgery-urology-plastic-surgery-pediatric-surgery"): "Surgery",
    ("pget", "p2-surgery-allied-group-2-general-surgery-orthopedics-cardiac-surgery-thoracic-surgery-neurosurgery"): "Surgery",
}


def operator(request: Request) -> str:
    from .server import operator as check
    return check(request)


def _station_row(db, st: Station) -> dict[str, Any]:
    latest = S.latest_version(db, st)
    flagged = db.query(func.count()).select_from(StationCriterion).filter(
        StationCriterion.version_id == latest.id, StationCriterion.flagged).scalar() if latest else 0
    gold = db.query(StationGold).filter(StationGold.version_id == latest.id).all() if latest else []
    cal = (db.query(StationCalibration).filter(StationCalibration.version_id == latest.id)
           .order_by(StationCalibration.at.desc()).first()) if latest else None
    return {"id": st.id, "title": st.title, "status": st.status, "readiness": st.readiness, "topic_id": st.tos_topic_id,
            "version": latest.version if latest else None, "version_id": latest.id if latest else None,
            "approved": bool(latest and latest.approved_at), "flagged": flagged,
            "gold": len(gold), "gold_marked": sum(1 for g in gold if g.marked_at),
            "calibration": {"passed": cal.passed, "metrics": cal.metrics, "current": cal.scorer_hash == S.SCORER_HASH}
            if cal else None, "updated_at": st.updated_at.isoformat()}


@router.get("/api/stations-studio/papers")
def papers(who: str = Depends(operator)):
    out = []
    for (exam_id, paper_id), specialty in PAPERS.items():
        p = tos_registry.get_paper(exam_id, paper_id)
        if p:
            out.append({"exam_id": exam_id, "paper_id": paper_id, "title": p["title"], "specialty": specialty})
    return out


@router.get("/api/stations-studio/overview")
def overview(exam_id: str, paper_id: str, who: str = Depends(operator)):
    p = tos_registry.get_paper(exam_id, paper_id)
    if not p:
        raise HTTPException(404, "Paper not found")
    with session() as db:
        stations = db.query(Station).filter(Station.tos_exam_id == exam_id, Station.tos_paper_id == paper_id).all()
        rows = [_station_row(db, st) for st in stations]
    by_topic: dict[str, int] = {}
    for r in rows:
        by_topic[r["topic_id"]] = by_topic.get(r["topic_id"], 0) + 1
    topics = [{"topic_id": t["id"], "topic": t["topic_name"], "subject": s["subject_name"],
               "stations": by_topic.get(t["id"], 0)} for s in p["subjects"] for t in s["topics"]]
    return {"topics": topics, "stations": sorted(rows, key=lambda r: r["updated_at"], reverse=True), "run": RUN,
            "thresholds": S.CALIBRATION_THRESHOLDS}


class DraftReq(BaseModel):
    exam_id: str
    paper_id: str
    topic_ids: list[str]


@router.post("/api/stations-studio/draft")
def draft(req: DraftReq, who: str = Depends(operator)):
    specialty = PAPERS.get((req.exam_id, req.paper_id))
    if not specialty:
        raise HTTPException(400, "Station drafting is set up for the pilot paper only")
    if not req.topic_ids:
        raise HTTPException(400, "Choose at least one topic")
    with _LOCK:
        if RUN.get("running"):
            raise HTTPException(409, "A drafting run is already going")
        RUN.clear()
        RUN.update({"running": True, "done": 0, "total": len(req.topic_ids), "current": "", "made": [], "errors": [],
                    "started_at": datetime.utcnow().isoformat(), "by": who})

    def work():
        try:
            for i, tid in enumerate(req.topic_ids):
                RUN["current"] = tid
                try:
                    with session() as db:
                        st = S.draft_station(db, who, req.exam_id, req.paper_id, tid, specialty)
                        RUN["made"].append({"id": st.id, "title": st.title})
                except Exception as e:
                    logger.warning("draft %s failed: %s", tid, e)
                    RUN["errors"].append({"topic_id": tid, "error": str(e)[:300]})
                RUN["done"] = i + 1
        finally:
            RUN.update({"running": False, "finished_at": datetime.utcnow().isoformat(), "current": ""})

    threading.Thread(target=work, daemon=True, name="station-draft").start()
    return RUN


@router.get("/api/stations-studio/run")
def run_status(who: str = Depends(operator)):
    return RUN


@router.get("/api/stations-studio/stations/{station_id}")
def detail(station_id: int, who: str = Depends(operator)):
    with session() as db:
        st = db.get(Station, station_id)
        if st is None:
            raise HTTPException(404)
        v = S.latest_version(db, st)
        golds = db.query(StationGold).filter(StationGold.version_id == v.id).order_by(StationGold.id).all()
        cals = (db.query(StationCalibration).filter(StationCalibration.version_id == v.id)
                .order_by(StationCalibration.at.desc()).limit(5).all())
        return {**_station_row(db, st), "specialty": st.specialty, "provenance": st.provenance,
                "latest": S.version_payload(db, v, with_answers=True),
                "gold": [{"id": g.id, "label": g.label, "origin": g.origin, "transcript": g.transcript,
                          "expert_marks": g.expert_marks, "marked_by": g.marked_by,
                          "marked_at": g.marked_at.isoformat() if g.marked_at else None} for g in golds],
                "calibrations": [{"at": c.at.isoformat(), "passed": c.passed, "metrics": c.metrics, "n_gold": c.n_gold,
                                  "current": c.scorer_hash == S.SCORER_HASH} for c in cals]}


class ContentReq(BaseModel):
    content: dict


@router.post("/api/stations-studio/stations/{station_id}/versions")
def revise(station_id: int, req: ContentReq, who: str = Depends(operator)):
    with session() as db:
        st = db.get(Station, station_id)
        if st is None:
            raise HTTPException(404)
        try:
            v = S.revise_station(db, st, req.content, who)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(422, f"Invalid station content: {e}")
        return {"version": v.version, "version_id": v.id}


class NoteReq(BaseModel):
    note: str | None = None


@router.post("/api/stations-studio/stations/{station_id}/approve")
def approve(station_id: int, req: NoteReq, who: str = Depends(operator)):
    with session() as db:
        st = db.get(Station, station_id)
        v = S.latest_version(db, st) if st else None
        if v is None:
            raise HTTPException(404)
        if db.query(StationCriterion).filter(StationCriterion.version_id == v.id, StationCriterion.flagged).first():
            raise HTTPException(409, "Some criteria cite text not found in their passage. Fix or clear them (save a "
                                     "new version) before approving.")
        try:
            S.approve_version(db, st, v, who, req.note)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return _station_row(db, st)


@router.post("/api/stations-studio/stations/{station_id}/publish")
def publish(station_id: int, who: str = Depends(operator)):
    with session() as db:
        st = db.get(Station, station_id)
        if st is None:
            raise HTTPException(404)
        try:
            S.publish(db, st, who)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return _station_row(db, st)


@router.post("/api/stations-studio/stations/{station_id}/samples")
def samples(station_id: int, who: str = Depends(operator)):
    with session() as db:
        st = db.get(Station, station_id)
        v = S.latest_version(db, st) if st else None
        if v is None:
            raise HTTPException(404)
        try:
            made = S.generate_samples(db, v, who)
        except Exception as e:
            raise HTTPException(502, f"The AI could not write sample answers: {e}")
        return {"made": len(made)}


class MarksReq(BaseModel):
    marks: dict


@router.post("/api/stations-studio/gold/{gold_id}/marks")
def mark(gold_id: int, req: MarksReq, who: str = Depends(operator)):
    with session() as db:
        g = db.get(StationGold, gold_id)
        if g is None:
            raise HTTPException(404)
        try:
            S.mark_gold(db, g, req.marks, who)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True}


@router.post("/api/stations-studio/stations/{station_id}/calibrate")
def calibrate(station_id: int, who: str = Depends(operator)):
    with session() as db:
        st = db.get(Station, station_id)
        v = S.latest_version(db, st) if st else None
        if v is None:
            raise HTTPException(404)
        n = db.query(StationGold).filter(StationGold.version_id == v.id, StationGold.marked_at.isnot(None)).count()
        if n < 1:
            raise HTTPException(409, "Mark at least one gold answer first")
        try:
            run = S.run_calibration(db, v, who)
        except Exception as e:
            raise HTTPException(502, f"Calibration failed: {e}")
        return {"passed": run.passed, "metrics": run.metrics, "n_gold": run.n_gold, "thresholds": run.thresholds,
                "readiness": st.readiness}
