#!/usr/bin/env python3
"""Copy TOACS stations authored on this PC (Seed Studio, dev DB) to the NAS database.

The PC is where stations are drafted, reviewed, approved, marked (gold answers) and calibrated; the NAS is where
learners sit them. This copies, for every station on the PC (or --ids):
  * the station row (title, status, readiness, publication);
  * every version not yet on the NAS, with its rubric criteria (versions are immutable, so a version is copied
    once and never changed);
  * gold answers and the latest calibration run of each copied version (criterion ids remapped);
and records on the NAS which PC station/version each row came from (version lineage pc_station_id /
pc_version_id), so re-running only adds what is new and updates status. Learners' attempts are never touched.

Usage (repo root, .venv-gpu, dev DB up, NAS key in _local/):
    python scripts/nas_sync_stations.py --dry-run
    python scripts/nas_sync_stations.py [--ids 33 51 52]
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nas_tunnel import ROOT, nas_database_url  # noqa: E402

sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.station_models import (Station, StationAudit, StationCalibration, StationCriterion,  # noqa: E402
                                StationGold, StationVersion)

VERSION_COLS = ("version", "scenario", "stages", "instructions_guided", "instructions_timed", "framework",
                "response_formats", "time_limit_s", "difficulty", "difficulty_dims", "model_answer", "pass_mark",
                "sources", "review_notes", "created_by", "created_at", "approved_by", "approved_at")
CRIT_COLS = ("ordinal", "competency", "expected", "alternatives", "marks", "safety_critical", "prompted_credit",
             "source_ref", "flagged")


def _hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def sync_station(pc, nas, st: Station, dry: bool) -> str:
    pc_versions = pc.query(StationVersion).filter(StationVersion.station_id == st.id).order_by(StationVersion.version).all()
    # The NAS copy, found through the lineage of any of its versions.
    nas_v = [v for v in nas.query(StationVersion).all() if (v.lineage or {}).get("pc_station_id") == st.id]
    nas_st = nas.get(Station, nas_v[0].station_id) if nas_v else None
    have = {(v.lineage or {}).get("pc_version_id"): v for v in nas_v}
    todo = [v for v in pc_versions if v.id not in have]
    if dry:
        return f"#{st.id} {st.title[:50]}: {'new' if nas_st is None else 'exists'}, {len(todo)} version(s) to copy, status {st.status}"

    if nas_st is None:
        nas_st = Station(specialty=st.specialty, tos_exam_id=st.tos_exam_id, tos_paper_id=st.tos_paper_id,
                         tos_topic_id=st.tos_topic_id, title=st.title, station_type=st.station_type,
                         status="draft", readiness="practice_only", provenance=st.provenance, created_by=st.created_by,
                         created_at=st.created_at)
        nas.add(nas_st)
        nas.flush()
    crit_map: dict[int, int] = {}
    for v in todo:
        nv = StationVersion(station_id=nas_st.id, **{c: getattr(v, c) for c in VERSION_COLS},
                            lineage={**(v.lineage or {}), "pc_station_id": st.id, "pc_version_id": v.id})
        nas.add(nv)
        nas.flush()
        have[v.id] = nv
    # A version's content never changes, but its approval is recorded later: carry approval across.
    for pv in pc_versions:
        nv = have.get(pv.id)
        if nv is not None and pv.approved_at and not nv.approved_at:
            nv.approved_at, nv.approved_by, nv.review_notes = pv.approved_at, pv.approved_by, pv.review_notes
    # Criterion id map for every version on both sides (by ordinal: criteria of a version never change).
    for pv_id, nv in have.items():
        pcs = {c.ordinal: c.id for c in pc.query(StationCriterion).filter(StationCriterion.version_id == pv_id)}
        ncs = {c.ordinal: c.id for c in nas.query(StationCriterion).filter(StationCriterion.version_id == nv.id)}
        if not ncs:
            for c in pc.query(StationCriterion).filter(StationCriterion.version_id == pv_id).order_by(StationCriterion.ordinal):
                nc = StationCriterion(version_id=nv.id, **{k: getattr(c, k) for k in CRIT_COLS})
                nas.add(nc)
                nas.flush()
                ncs[c.ordinal] = nc.id
        crit_map.update({pcs[o]: ncs[o] for o in pcs if o in ncs})

    # Gold answers and the latest calibration of each version.
    for pv_id, nv in have.items():
        existing = {_hash(g.transcript) for g in nas.query(StationGold).filter(StationGold.version_id == nv.id)}
        for g in pc.query(StationGold).filter(StationGold.version_id == pv_id):
            if _hash(g.transcript) in existing:
                continue
            nas.add(StationGold(version_id=nv.id, label=g.label, transcript=g.transcript, origin=g.origin,
                                expert_marks={str(crit_map.get(int(k), k)): m for k, m in (g.expert_marks or {}).items()},
                                marked_by=g.marked_by, marked_at=g.marked_at, created_at=g.created_at))
        cal = (pc.query(StationCalibration).filter(StationCalibration.version_id == pv_id)
               .order_by(StationCalibration.at.desc()).first())
        if cal and not nas.query(StationCalibration).filter(StationCalibration.version_id == nv.id,
                                                            StationCalibration.at == cal.at).first():
            m = dict(cal.metrics or {})
            m["per_criterion"] = {str(crit_map.get(int(k), k)): x for k, x in (m.get("per_criterion") or {}).items()}
            nas.add(StationCalibration(version_id=nv.id, at=cal.at, scorer_hash=cal.scorer_hash, n_gold=cal.n_gold,
                                       metrics=m, thresholds=cal.thresholds, passed=cal.passed, run_by=cal.run_by))

    # Station state follows the PC.
    nas_st.title, nas_st.station_type, nas_st.status, nas_st.readiness = st.title, st.station_type, st.status, st.readiness
    nas_st.published_at, nas_st.updated_at = st.published_at, st.updated_at
    nas_st.current_version_id = have[st.current_version_id].id if st.current_version_id in have else nas_st.current_version_id
    nas.add(StationAudit(actor="nas_sync_stations", action="sync", entity="station", entity_id=str(nas_st.id),
                         detail={"pc_station_id": st.id, "versions_copied": [v.version for v in todo], "status": st.status}))
    nas.commit()
    return f"#{st.id} -> NAS #{nas_st.id} {st.title[:50]}: {len(todo)} version(s) copied, status {st.status}/{st.readiness}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=int, nargs="*", help="PC station ids (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pc = SessionLocal()
    try:
        q = pc.query(Station).order_by(Station.id)
        if args.ids:
            q = q.filter(Station.id.in_(args.ids))
        stations = q.all()
        print(f"On this PC: {len(stations)} station(s)", flush=True)
        if not stations:
            return 0
        with nas_database_url() as url:
            engine = create_engine(url.replace("postgresql://", "postgresql+psycopg2://", 1), pool_pre_ping=True)
            nas = sessionmaker(bind=engine)()
            try:
                for st in stations:
                    print(" ", sync_station(pc, nas, st, args.dry_run), flush=True)
            finally:
                nas.close()
                engine.dispose()
    finally:
        pc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
