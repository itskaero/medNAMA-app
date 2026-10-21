"""Jobs: the preview manifest, creating and controlling jobs, and the workers that run their batches.

Persistence is the database: a job's plan is its seed_batches rows, and a worker claims one with
SELECT ... FOR UPDATE SKIP LOCKED, so two workers (or two Studio processes) never take the same batch.

  retry      a TransientError puts the batch back to pending with a growing delay, up to max_retries; a
             PermanentError (no key, no evidence) fails it at once. Every attempt's error is kept on the batch.
  pause      the job stops handing out batches; batches already running finish and save.
  cancel     pending batches are cancelled at once; a running batch checks before saving and, if its job was
             cancelled meanwhile, discards its result instead of inserting it.
  restart    on start-up, batches left 'running' by a stopped process go back to pending (they had not saved:
             the save is one transaction), recorded as an 'interrupted' error.
  overlap    a job cannot start while another active job still has work on any of the same units, so two jobs
             never write questions for the same target at the same time.
  completed batches are never re-run; regenerating means a new job, which only plans what is still missing.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import literal_column, text

from . import settings
from . import targets as T
from .common import PermanentError, TransientError, audit, cost_usd, session
from .progress import node_dicts, unit_counts

from app.seed_models import SeedBatch, SeedItem, SeedJob, SeedNote, SeedTosDocument, SeedTosNode  # noqa: E402

logger = logging.getLogger("seed_studio.engine")

ACTIVE = ("queued", "running", "paused", "cancelling")
DEFAULT_CONFIG = {
    "multiplier": 5.0,            # 5x the official count; 10 = the expanded target
    "notes": True,                # one revision note per leaf in scope
    "regenerate_notes": False,
    "difficulty": dict(T.DEFAULT_DIFFICULTY),
    "batch_size": 5,
    "max_retries": 3,
    "options": 5,
    "dup_stem_cosine": 0.90,
    "require_grounding": True,     # reject a question whose keyed answer the cited passage does not support
    "require_rationales": True,    # reject a question without a rationale for every distractor
    "publish_on_insert": False,    # False: inserted as 'pending' (not served) until approved
    "test": False,                 # automated tests only: ordinary workers never claim these jobs' batches
}
BACKOFF_S = (20, 60, 180, 600)


def normalise_config(cfg: dict | None) -> dict:
    out = {**DEFAULT_CONFIG, **{k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG}}
    out["multiplier"] = max(0.5, min(20.0, float(out["multiplier"])))
    out["batch_size"] = max(1, min(10, int(out["batch_size"])))
    out["max_retries"] = max(0, min(8, int(out["max_retries"])))
    out["options"] = 5 if int(out["options"]) == 5 else 4
    out["dup_stem_cosine"] = max(0.80, min(0.98, float(out["dup_stem_cosine"])))
    diff = {int(k): max(0.0, float(v)) for k, v in (out["difficulty"] or {}).items() if str(k) in "12345"}
    out["difficulty"] = {str(k): diff.get(k, 0.0) for k in range(1, 6)} if sum(diff.values()) > 0 else \
        {str(k): v for k, v in T.DEFAULT_DIFFICULTY.items()}
    return out


# ─── manifest (preview) ───────────────────────────────────────────────────────────────────────────

def manifest(db, doc: SeedTosDocument, scope_id: int, cfg: dict, with_evidence: bool = True) -> dict[str, Any]:
    """What a job on this scope would do, given what already exists. Writes nothing."""
    from .generate import evidence_count

    cfg = normalise_config(cfg)
    nodes = node_dicts(db, doc.id)
    by_id = {n["id"]: n for n in nodes}
    if scope_id not in by_id:
        raise KeyError("scope node not in this document")
    plans = T.compute(nodes, cfg["multiplier"], doc.base_per_topic)
    counts = unit_counts(db, doc.id)
    dist = {int(k): v for k, v in cfg["difficulty"].items()}
    units, totals = [], {"target": 0, "have": 0, "need": 0, "batches": 0, "by_difficulty": {d: 0 for d in range(1, 6)}}
    missing_evidence, ambiguous = [], []
    for u in T.units_under(nodes, scope_id):
        p = plans[u["id"]]
        c = counts.get(u["id"])
        have = dict(c["valid_by_difficulty"]) if c else {d: 0 for d in range(1, 6)}
        # validated-but-deleted questions do not count as present
        want = T.allocate_difficulty(p.planned, dist)
        need = {d: max(0, want[d] - have.get(d, 0)) for d in range(1, 6)}
        batches = T.batches_for(need, cfg["batch_size"])
        ev = evidence_count(db, u["name"]) if with_evidence else None
        row = {"id": u["id"], "path": u["path"], "planned": p.planned, "planned_source": p.source,
               "planned_note": p.note, "have": sum(have.values()), "need": sum(need.values()),
               "need_by_difficulty": need, "batches": len(batches), "subtopics": len(T.subtopics_of(nodes, u["id"])),
               "evidence": ev, "ambiguous": u["ambiguous"], "page_no": u["page_no"]}
        units.append(row)
        totals["target"] += p.planned
        totals["have"] += row["have"]
        totals["need"] += row["need"]
        totals["batches"] += len(batches)
        for d in range(1, 6):
            totals["by_difficulty"][d] += need[d]
        if ev == 0:
            missing_evidence.append(u["path"])
        if u["ambiguous"]:
            ambiguous.append({"path": u["path"], "note": u["ambiguity_note"]})
    for n in nodes:   # ambiguity anywhere on the way down to the units counts too
        if n["ambiguous"] and n["id"] != scope_id and _under(by_id, n["id"], scope_id) and n["level"] != "topic":
            ambiguous.append({"path": n["path"], "note": n["ambiguity_note"]})
    leaves = T.note_leaves_under(nodes, scope_id) if cfg["notes"] else []
    notes_need = [lf for lf in leaves if not (counts.get(lf["id"]) or {}).get("note") or cfg["regenerate_notes"]]
    est_in = totals["batches"] * settings.EST_TOKENS_IN_PER_BATCH + len(notes_need) * settings.EST_TOKENS_IN_PER_NOTE
    est_out = totals["need"] * settings.EST_TOKENS_OUT_PER_QUESTION + len(notes_need) * settings.EST_TOKENS_OUT_PER_NOTE
    return {
        "document": {"id": doc.id, "title": doc.title, "family": doc.family, "version": doc.version},
        "scope": {"id": scope_id, "path": by_id[scope_id]["path"], "level": by_id[scope_id]["level"]},
        "config": cfg, "units": units, "topics": len(units),
        "target_questions": totals["target"], "have_questions": totals["have"], "need_questions": totals["need"],
        "allocation_by_difficulty": totals["by_difficulty"], "batches": totals["batches"],
        "notes_planned": len(leaves), "notes_need": len(notes_need),
        "estimated_tokens": {"input": est_in, "output": est_out},
        "estimated_cost_usd": cost_usd(est_in, est_out),
        "missing_evidence": missing_evidence, "ambiguous": ambiguous,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def _under(by_id: dict, node_id: int, scope_id: int) -> bool:
    n = by_id.get(node_id)
    while n is not None:
        if n["id"] == scope_id:
            return True
        n = by_id.get(n["parent_id"])
    return False


# ─── creating and controlling jobs ────────────────────────────────────────────────────────────────

def new_job_id() -> str:
    return "J" + datetime.utcnow().strftime("%y%m%d%H%M") + secrets.token_hex(3)


def create_job(db, actor: str, doc: SeedTosDocument, scope_id: int, cfg: dict, dry_run: bool,
               expected_batches: int | None) -> SeedJob:
    """Persist a job and its batches from a fresh manifest. A real job must confirm the batch count it previewed,
    so a stale preview (the plan changed since) cannot start more work than the operator saw."""
    plan = manifest(db, doc, scope_id, cfg, with_evidence=False)
    if not dry_run and expected_batches is not None and expected_batches != plan["batches"] + plan["notes_need"]:
        raise ValueError(f"The plan changed since the preview ({plan['batches'] + plan['notes_need']} batches now, "
                         f"{expected_batches} previewed). Preview again.")
    job = SeedJob(id=new_job_id(), created_by=actor, document_id=doc.id, scope_node_id=scope_id,
                  status="completed" if dry_run else "queued", dry_run=dry_run, config=plan["config"], manifest=plan,
                  finished_at=datetime.utcnow() if dry_run else None)
    db.add(job)
    db.flush()
    if not dry_run:
        for u in plan["units"]:
            for d, seq, n in T.batches_for(u["need_by_difficulty"], plan["config"]["batch_size"]):
                db.add(SeedBatch(job_id=job.id, node_id=u["id"], kind="mcq", difficulty=d, seq=seq, target=n))
        if plan["config"]["notes"]:
            nodes = node_dicts(db, doc.id)
            counts = unit_counts(db, doc.id)
            for lf in T.note_leaves_under(nodes, scope_id):
                if plan["config"]["regenerate_notes"] or not (counts.get(lf["id"]) or {}).get("note"):
                    db.add(SeedBatch(job_id=job.id, node_id=lf["id"], kind="note", target=1))
        # The overlap check is raw SQL, which does not autoflush: write this job's batches first, or it never sees
        # them and two jobs could seed the same topics at once.
        db.flush()
        clash = overlapping_job(db, job)
        if clash:
            db.rollback()
            raise ValueError(f"Job {clash} is still working on some of the same topics. Wait for it, or cancel it.")
    audit(db, actor, "job_create", "job", job.id, dry_run=dry_run, scope=plan["scope"]["path"],
          need=plan["need_questions"], batches=plan["batches"], notes=plan["notes_need"], config=plan["config"])
    db.commit()
    return job


def overlapping_job(db, job: SeedJob) -> str | None:
    row = db.execute(text("""
        SELECT DISTINCT o.job_id FROM seed_batches o JOIN seed_jobs oj ON oj.id = o.job_id
        WHERE o.job_id <> :j AND oj.status IN ('queued','running','paused','cancelling')
          AND o.status IN ('pending','running')
          AND o.node_id IN (SELECT node_id FROM seed_batches WHERE job_id = :j)
        LIMIT 1"""), {"j": job.id}).first()
    return row[0] if row else None


def control(db, actor: str, job: SeedJob, action: str) -> SeedJob:
    if action == "pause" and job.status in ("queued", "running"):
        job.status = "paused"
    elif action == "resume" and job.status == "paused":
        clash = overlapping_job(db, job)
        if clash:
            raise ValueError(f"Job {clash} is now working on some of the same topics.")
        job.status = "queued"
    elif action == "cancel" and job.status in ("queued", "running", "paused"):
        db.query(SeedBatch).filter(SeedBatch.job_id == job.id, SeedBatch.status == "pending") \
            .update({"status": "cancelled", "stage": "cancelled"})
        running = db.query(SeedBatch).filter(SeedBatch.job_id == job.id, SeedBatch.status == "running").count()
        job.status = "cancelling" if running else "cancelled"
        if not running:
            job.finished_at = datetime.utcnow()
    elif action == "retry_failed":
        n = db.query(SeedBatch).filter(SeedBatch.job_id == job.id, SeedBatch.status == "failed") \
            .update({"status": "pending", "attempts": 0, "stage": None,
                     "progress": literal_column("progress - 'not_before'")}, synchronize_session=False)
        if not n:
            raise ValueError("This job has no failed batches.")
        if job.status in ("completed", "partial", "failed"):
            clash = overlapping_job(db, job)
            if clash:
                db.rollback()
                raise ValueError(f"Job {clash} is now working on some of the same topics.")
            job.status, job.finished_at = "queued", None
    else:
        raise ValueError(f"Cannot {action.replace('_', ' ')} a job that is {job.status}.")
    audit(db, actor, action, "job", job.id, status=job.status)
    db.commit()
    return job


def finish_if_done(db, job_id: str) -> None:
    """Settle a job's final state once nothing is pending or running."""
    job = db.get(SeedJob, job_id)
    if job is None or job.status not in ("queued", "running", "cancelling"):
        return
    rows = dict(db.execute(text("SELECT status, count(*) FROM seed_batches WHERE job_id = :j GROUP BY 1"),
                           {"j": job_id}).all())
    if rows.get("pending", 0) or rows.get("running", 0):
        return
    if job.status == "cancelling":
        job.status = "cancelled"
    elif rows.get("failed", 0):
        job.status = "partial" if rows.get("done", 0) else "failed"
    else:
        # all batches done; a shortfall (questions rejected) still makes it partial, never silently complete
        short = db.execute(text("""
            SELECT coalesce(sum(b.target), 0) - coalesce(sum((b.progress->>'inserted')::int), 0)
            FROM seed_batches b WHERE b.job_id = :j AND b.kind = 'mcq'"""), {"j": job_id}).scalar() or 0
        failed_notes = db.query(SeedNote).filter(SeedNote.job_id == job_id, SeedNote.status == "failed").count()
        job.status = "partial" if (short > 0 or failed_notes) else "completed"
        if short > 0 or failed_notes:
            job.error = "; ".join(x for x in (
                f"{short} question(s) short of the batch targets (rejected or duplicate)" if short > 0 else "",
                f"{failed_notes} note(s) could not be written" if failed_notes else "") if x)
    job.finished_at = datetime.utcnow()
    audit(db, "system", "job_finish", "job", job.id, status=job.status)
    db.commit()


def recover() -> int:
    """After a restart: put interrupted batches back in the queue (nothing of theirs was saved)."""
    with session() as db:
        # Only batches whose heartbeat has gone quiet: another Studio process may be working on fresh ones.
        stale_before = datetime.utcnow() - timedelta(seconds=settings.STALE_S)
        rows = db.query(SeedBatch).filter(SeedBatch.status == "running",
                                          (SeedBatch.heartbeat_at.is_(None)) | (SeedBatch.heartbeat_at < stale_before)).all()
        for b in rows:
            b.status, b.stage = "pending", None
            b.attempts = max(0, (b.attempts or 0) - 1)   # an interruption is not a failed attempt
            b.errors = [*(b.errors or []), {"at": datetime.utcnow().isoformat(), "kind": "interrupted",
                                             "message": "The Studio stopped while this batch was running; nothing was saved."}]
        n = len(rows)
        db.commit()
        for job in db.query(SeedJob).filter(SeedJob.status.in_(("running", "cancelling"))):
            finish_if_done(db, job.id)
        return n


# ─── workers ──────────────────────────────────────────────────────────────────────────────────────

_workers: list[threading.Thread] = []
_stop = threading.Event()

CLAIM_SQL = """
UPDATE seed_batches SET status = 'running', worker = :w, started_at = now(), heartbeat_at = now(),
       attempts = attempts + 1, stage = 'retrieving'
WHERE id = (
  SELECT b.id FROM seed_batches b JOIN seed_jobs j ON j.id = b.job_id
  WHERE b.status = 'pending' AND j.status IN ('queued', 'running')
    AND (CAST(:job AS text) IS NULL AND NOT coalesce((j.config->>'test')::boolean, false) OR j.id = CAST(:job AS text))
    AND (b.progress->>'not_before' IS NULL OR (b.progress->>'not_before')::timestamp < now() AT TIME ZONE 'utc')
  ORDER BY j.created_at, b.kind, b.id
  FOR UPDATE OF b SKIP LOCKED LIMIT 1)
RETURNING id, job_id
"""


def start_workers(n: int | None = None) -> None:
    if _workers:
        return
    try:
        recovered = recover()
        if recovered:
            logger.info("Recovered %d interrupted batch(es)", recovered)
    except Exception as e:   # database not up yet: the workers keep trying, recovery runs on the next start
        logger.warning("Could not check for interrupted batches (database unreachable?): %s", e)
    for i in range(n or settings.WORKERS):
        t = threading.Thread(target=_worker_loop, args=(f"w{i + 1}",), name=f"seed-worker-{i + 1}", daemon=True)
        _workers.append(t)
        t.start()


def stop_workers() -> None:
    _stop.set()


def _worker_loop(name: str) -> None:
    while not _stop.is_set():
        try:
            if not run_one(name):
                _stop.wait(2.0)
        except Exception:   # a worker must survive anything one batch does
            logger.exception("worker %s", name)
            _stop.wait(5.0)


def run_one(worker: str, runner=None, job_id: str | None = None) -> bool:
    """Claim and run one batch. Returns False when there was nothing to do. `runner` replaces the real generation
    in tests: runner(db, batch, job, stage) -> result dict, saved like a real one."""
    from . import generate as G

    with session() as db:
        row = db.execute(text(CLAIM_SQL), {"w": worker, "job": job_id}).first()
        db.commit()
        if row is None:
            return False
        batch_id, job_id = row  # noqa: PLW2901
        job = db.get(SeedJob, job_id)
        started = db.execute(text("UPDATE seed_jobs SET status = 'running', started_at = coalesce(started_at, now() AT TIME ZONE 'utc') "
                                  "WHERE id = :j AND status = 'queued'"), {"j": job_id}).rowcount
        if started:   # only the worker that flipped it records the start
            audit(db, "system", "job_start", "job", job_id)
        db.commit()
        db.refresh(job)
        batch = db.get(SeedBatch, batch_id)

        def stage(name: str, progress: dict | None = None) -> None:
            with session() as s2:   # separate session: the monitor sees it while the batch is still working
                s2.execute(text("UPDATE seed_batches SET stage = :s, heartbeat_at = now(), "
                                "progress = progress || CAST(:p AS jsonb) WHERE id = :id"),
                           {"s": name, "p": _json(progress or {}), "id": batch_id})
                s2.commit()

        try:
            if runner is not None:
                result = runner(db, batch, job, stage)
            elif batch.kind == "note":
                result = G.run_note_batch(db, batch, job, stage)
            else:
                result = G.run_mcq_batch(db, batch, job, stage)
            db.refresh(job)
            if job.status in ("cancelling", "cancelled"):
                db.rollback()
                b = db.get(SeedBatch, batch_id)
                b.status, b.stage, b.finished_at = "cancelled", "cancelled", datetime.utcnow()
                b.progress = {**(b.progress or {}), "discarded": "the job was cancelled before this batch saved"}
                db.commit()
                _usage(db, batch_id, result)
            else:
                if batch.kind == "note":
                    G.save_note(db, batch, job, result)
                else:
                    G.save_mcq_batch(db, batch, job, result)
                _usage(db, batch_id, result)
        except (TransientError, PermanentError, Exception) as e:
            db.rollback()
            permanent = isinstance(e, PermanentError)
            b = db.get(SeedBatch, batch_id)
            j = db.get(SeedJob, job_id)
            kind = "permanent" if permanent else "transient" if isinstance(e, TransientError) else "error"
            b.errors = [*(b.errors or []), {"at": datetime.utcnow().isoformat(), "kind": kind,
                                             "stage": b.stage, "message": str(e)[:500]}]
            retries = int((j.config or {}).get("max_retries", 3))
            if permanent or b.attempts > retries or j.status in ("cancelling", "cancelled"):
                b.status, b.finished_at = ("cancelled" if j.status in ("cancelling", "cancelled") else "failed"), datetime.utcnow()
            else:
                delay = BACKOFF_S[min(b.attempts - 1, len(BACKOFF_S) - 1)]
                b.status = "pending"
                b.progress = {**(b.progress or {}),
                              "not_before": (datetime.utcnow() + timedelta(seconds=delay)).isoformat()}
            if not isinstance(e, (TransientError, PermanentError)):
                logger.exception("batch %s", batch_id)
            db.commit()
        finish_if_done(db, job_id)
        return True


def _usage(db, batch_id: int, result: Any) -> None:
    """Add the call's token usage to the batch (after its save, which reloads the row)."""
    usage = result.get("usage") if isinstance(result, dict) else None
    if usage is not None:
        db.execute(text("UPDATE seed_batches SET tokens_in = tokens_in + :i, tokens_out = tokens_out + :o WHERE id = :b"),
                   {"i": usage.tokens_in, "o": usage.tokens_out, "b": batch_id})
        db.commit()


def _json(d: dict) -> str:
    import json
    return json.dumps(d, default=str)


def running_info() -> dict[str, Any]:
    return {"workers": len(_workers), "alive": sum(t.is_alive() for t in _workers)}


def wait_idle(job_id: str, timeout: float = 60) -> str:
    """Tests: run until the job settles."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with session() as db:
            st = db.get(SeedJob, job_id).status
        if st not in ACTIVE:
            return st
        time.sleep(0.2)
    return "timeout"
