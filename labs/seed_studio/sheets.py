"""TOS sheets: pre-write every topic's concept refresher and revision notes on this PC.

The app writes a sheet the first time a learner opens it, which costs a retrieval and an AI call (and on the NAS,
a wait). This page writes the missing ones for a whole paper ahead of time with the app's own generator
(app/tos_sheets.py: same prompts, same cache keys), then scripts/nas_sync_tos_sheets.py copies them to the NAS.

One run at a time; progress is kept in memory (a restart forgets the progress, never the sheets already written).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import settings  # noqa: F401  (backend import path)
from .common import audit, session

from app import tos_sheets  # noqa: E402
from app.tos import tos_registry  # noqa: E402

logger = logging.getLogger("seed_studio.sheets")
router = APIRouter()
RUN: dict[str, Any] = {"running": False}
_LOCK = threading.Lock()


def operator(request: Request) -> str:
    """The signed-in Studio operator (the same check as every other Studio route)."""
    from .server import operator as check
    return check(request)


@router.get("/api/sheets/exams")
def exams(who: str = Depends(operator)):
    return [{"id": e["id"], "short_name": e["short_name"],
             "papers": [{"id": p["id"], "title": p["title"],
                         "topics": sum(len(s["topics"]) for s in p["subjects"])} for p in e["papers"]]}
            for e in tos_registry.examinations.values()]


@router.get("/api/sheets/status")
def status(exam_id: str, paper_id: str, who: str = Depends(operator)):
    """Per topic: is the refresher written, and are notes available (Seed Studio's or the cache)?"""
    paper = tos_registry.get_paper(exam_id, paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")
    rows = []
    with session() as db:
        for s in paper["subjects"]:
            for t in s["topics"]:
                ref = tos_sheets.get_sheet(db, "refresher", exam_id, paper, s, t, write=False)
                notes = tos_sheets.get_sheet(db, "notes", exam_id, paper, s, t, write=False)
                rows.append({"subject": s["subject_name"], "topic": t["topic_name"], "topic_id": t["id"],
                             "subtopics": len(t.get("subtopics", [])),
                             "refresher": bool(ref.get("available")),
                             "notes": notes.get("origin") if notes.get("available") else None})
    return {"topics": rows, "refreshers": sum(r["refresher"] for r in rows),
            "notes": sum(1 for r in rows if r["notes"]), "total": len(rows), "run": RUN}


class RunReq(BaseModel):
    exam_id: str
    paper_id: str
    kinds: list[str] = ["refresher", "notes"]
    subtopics: bool = False


@router.post("/api/sheets/run")
def run(req: RunReq, who: str = Depends(operator)):
    paper = tos_registry.get_paper(req.exam_id, req.paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")
    kinds = tuple(k for k in req.kinds if k in tos_sheets.KINDS)
    if not kinds:
        raise HTTPException(400, "Choose refresher and/or notes")
    with _LOCK:
        if RUN.get("running"):
            raise HTTPException(409, "A sheet run is already going; wait for it to finish.")
        RUN.clear()
        RUN.update({"running": True, "exam_id": req.exam_id, "paper_id": req.paper_id, "paper": paper["title"],
                    "kinds": list(kinds), "subtopics": req.subtopics, "done": 0, "total": 0, "current": "",
                    "counts": {}, "started_at": datetime.utcnow().isoformat(), "by": who, "error": None})
    with session() as db:
        audit(db, who, "sheets_run", "paper", f"{req.exam_id}:{req.paper_id}", kinds=list(kinds), subtopics=req.subtopics)
        db.commit()

    def work():
        def progress(done, total, current, counts):
            RUN.update({"done": done, "total": total, "current": current, "counts": dict(counts)})
        try:
            with session() as db:
                RUN["counts"] = tos_sheets.prewrite(db, req.exam_id, paper, kinds, req.subtopics, on_progress=progress)
        except Exception as e:   # keep the error visible on the page
            logger.exception("sheet run failed")
            RUN["error"] = f"{type(e).__name__}: {e}"
        finally:
            RUN.update({"running": False, "finished_at": datetime.utcnow().isoformat()})

    threading.Thread(target=work, daemon=True, name="tos-sheets").start()
    return RUN


@router.get("/api/sheets/run")
def run_status(who: str = Depends(operator)):
    return RUN
