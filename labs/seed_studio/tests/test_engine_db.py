"""Jobs end to end against the real database, with a stand-in generator (no AI calls, no cost).

Checks: batches persist and run to completion; counts come from rows; a finished batch is never saved twice; a second
job only plans what is missing; overlapping jobs are refused; pause holds work; cancel discards an in-flight batch;
restart recovery re-queues interrupted batches; failed batches can be retried. Everything created is deleted after.
Skipped when the database is not reachable.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import pytest

from labs.seed_studio import engine as E
from labs.seed_studio import generate as G
from labs.seed_studio.common import PermanentError, session

from app.models import MCQ  # noqa: E402
from app.seed_models import SeedBatch, SeedItem, SeedJob, SeedTosDocument, SeedTosNode  # noqa: E402

_stem = itertools.count()


def _db_ok() -> bool:
    try:
        from sqlalchemy import text
        with session() as db:
            db.execute(text("SELECT 1 FROM seed_jobs LIMIT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_ok(), reason="dev database not reachable or not migrated")


@pytest.fixture()
def doc():
    with session() as db:
        d = SeedTosDocument(family="PGET", title="TEST seed doc", source_path="data/none.pdf", page_count=0,
                            parse_status="parsed", multiplier=1.0, has_counts=True)
        db.add(d)
        db.flush()
        mk = lambda parent, level, name, count=None: SeedTosNode(  # noqa: E731
            document_id=d.id, parent_id=parent, level=level, name=name, path=name, official_count=count)
        fam = mk(None, "family", "PGET"); db.add(fam); db.flush()
        paper = mk(fam.id, "paper", "Paper T", 4); db.add(paper); db.flush()
        subj = mk(paper.id, "subject", "Subject T", 4); db.add(subj); db.flush()
        ta = mk(subj.id, "topic", "Topic A"); db.add(ta); db.flush()
        tb = mk(subj.id, "topic", "Topic B"); db.add(tb); db.flush()
        db.commit()
        ids = {"doc": d.id, "scope": paper.id, "a": ta.id, "b": tb.id}
    yield ids
    with session() as db:
        mcq_ids = [i for (i,) in db.query(SeedItem.mcq_id).join(SeedTosNode, SeedTosNode.id == SeedItem.node_id)
                   .filter(SeedTosNode.document_id == ids["doc"], SeedItem.mcq_id.isnot(None))]
        db.query(MCQ).filter(MCQ.id.in_(mcq_ids or [-1])).delete(synchronize_session=False)
        db.query(SeedTosDocument).filter(SeedTosDocument.id == ids["doc"]).delete()
        db.commit()


CFG = {"batch_size": 1, "notes": False, "difficulty": {"3": 100}, "multiplier": 1, "test": True}


def fake_runner(db, batch, job, stage):
    """Like run_mcq_batch: the batch's target in validated questions plus one rejected."""
    stage("generating", {"fake": True})
    unit = db.get(SeedTosNode, batch.node_id)
    items = []
    for _ in range(batch.target):
        n = next(_stem)
        items.append({"status": "validated", "reason": None, "subtopic": None, "vec": None, "payload": {
            "question_text": f"TEST stem {n} for {unit.name} b{batch.id}", "options": {k: f"opt {k} {n}" for k in "ABCDE"},
            "key": "A", "answer": f"opt A {n}", "explanation": "because", "distractor_rationales": {k: "no" for k in "BCDE"},
            "citations": [], "grounding": "ai"}})
    items.append({"status": "rejected", "reason": "the cited passage does not support the keyed answer", "subtopic": None,
                  "payload": {"question_text": "TEST rejected", "options": {}, "key": "A"}})
    from types import SimpleNamespace
    return {"items": items, "usage": SimpleNamespace(tokens_in=100, tokens_out=40), "unit": unit, "doc": db.get(SeedTosDocument, unit.document_id),
            "line": {"paper": "Paper T"}, "programme": "PGET"}


def drain(job_id, runner=fake_runner, limit=50):
    n = 0
    while n < limit and E.run_one("test", runner=runner, job_id=job_id):
        n += 1
    return n


def make_job(ids, cfg=CFG, dry=False):
    with session() as db:
        d = db.get(SeedTosDocument, ids["doc"])
        return E.create_job(db, "test", d, ids["scope"], cfg, dry, None).id


def test_job_runs_to_completion_and_counts_come_from_rows(doc):
    with session() as db:
        plan = E.manifest(db, db.get(SeedTosDocument, doc["doc"]), doc["scope"], CFG, with_evidence=False)
    assert plan["target_questions"] == 4 and plan["need_questions"] == 4 and plan["batches"] == 4
    job_id = make_job(doc)
    assert drain(job_id) == 4
    with session() as db:
        job = db.get(SeedJob, job_id)
        assert job.status == "completed"
        items = db.query(SeedItem).filter(SeedItem.job_id == job_id).all()
        assert sum(i.status == "inserted" for i in items) == 4
        assert sum(i.status == "rejected" for i in items) == 4
        mcqs = db.query(MCQ).filter(MCQ.id.in_([i.mcq_id for i in items if i.mcq_id])).all()
        assert len(mcqs) == 4 and all(m.status == "pending" and m.source == "seed-studio" for m in mcqs)
        batches = db.query(SeedBatch).filter(SeedBatch.job_id == job_id).all()
        assert sum(b.tokens_in for b in batches) == 400 and sum(b.tokens_out for b in batches) == 160  # usage kept
        # a finished batch is never written twice
        b = db.query(SeedBatch).filter(SeedBatch.job_id == job_id).first()
        assert G.save_mcq_batch(db, b, job, fake_runner(db, b, job, lambda *a: None)) == {"skipped": 1}
        assert db.query(SeedItem).filter(SeedItem.job_id == job_id).count() == 8
        # a second plan on the same scope needs nothing more
        again = E.manifest(db, db.get(SeedTosDocument, doc["doc"]), doc["scope"], CFG, with_evidence=False)
        assert again["need_questions"] == 0 and again["batches"] == 0 and again["have_questions"] == 4


def test_overlap_pause_resume(doc):
    first = make_job(doc, {**CFG, "multiplier": 2})
    with pytest.raises(ValueError, match="same topics"):
        make_job(doc, {**CFG, "multiplier": 3})
    with session() as db:
        E.control(db, "test", db.get(SeedJob, first), "pause")
    assert drain(first) == 0                      # paused: nothing handed out
    with session() as db:
        E.control(db, "test", db.get(SeedJob, first), "resume")
    assert drain(first) == 8
    with session() as db:
        assert db.get(SeedJob, first).status == "completed"


def test_cancel_discards_in_flight_batch(doc):
    job_id = make_job(doc)

    def cancelling_runner(db, batch, job, stage):
        out = fake_runner(db, batch, job, stage)
        with session() as other:                  # the operator cancels while this batch is generating
            E.control(other, "test", other.get(SeedJob, job.id), "cancel")
        return out

    assert E.run_one("test", runner=cancelling_runner, job_id=job_id)
    with session() as db:
        assert db.get(SeedJob, job_id).status == "cancelled"
        assert db.query(SeedItem).filter(SeedItem.job_id == job_id).count() == 0
        states = {b.status for b in db.query(SeedBatch).filter(SeedBatch.job_id == job_id)}
        assert states == {"cancelled"}


def test_restart_recovery_and_retry_failed(doc):
    job_id = make_job(doc)

    def failing(db, batch, job, stage):
        raise PermanentError("Insufficient relevant evidence: retrieval found no textbook passages for this unit.")

    assert drain(job_id, runner=failing) == 4
    with session() as db:
        job = db.get(SeedJob, job_id)
        assert job.status == "failed"
        b = db.query(SeedBatch).filter(SeedBatch.job_id == job_id).first()
        assert b.status == "failed" and b.errors[-1]["kind"] == "permanent"
        E.control(db, "test", job, "retry_failed")
        assert db.get(SeedJob, job_id).status == "queued"
        # simulate a crash mid-batch: one batch left running with an old heartbeat
        b = db.query(SeedBatch).filter(SeedBatch.job_id == job_id).order_by(SeedBatch.id).first()
        b.status, b.heartbeat_at = "running", datetime.utcnow() - timedelta(hours=1)
        crashed = b.id
        db.commit()
    assert E.recover() >= 1
    with session() as db:
        b = db.get(SeedBatch, crashed)
        assert b.status == "pending" and b.errors[-1]["kind"] == "interrupted"
        assert b.attempts == 0                                          # an interruption is not a failed try
    assert drain(job_id) == 4
    with session() as db:
        assert db.get(SeedJob, job_id).status == "completed"


def test_dry_run_writes_a_manifest_and_no_batches(doc):
    job_id = make_job(doc, dry=True)
    with session() as db:
        job = db.get(SeedJob, job_id)
        assert job.dry_run and job.status == "completed" and job.manifest["need_questions"] == 4
        assert db.query(SeedBatch).filter(SeedBatch.job_id == job_id).count() == 0


def test_rereading_a_document_keeps_node_ids_history_and_corrections(doc):
    from labs.seed_studio import tos_parse as TP
    from app.seed_models import SeedTosPage

    rows = [{"paper": "Paper T", "subject": "Subject T", "count": 4, "count_level": "subject"},
            {"paper": "Paper T", "subject": "Subject T", "topic": "Topic A"},
            {"paper": "Paper T", "subject": "Subject T", "topic": "Topic B"}]
    with session() as db:
        d = db.get(SeedTosDocument, doc["doc"])
        db.add(SeedTosPage(document_id=d.id, page_no=1, status="done", rows=rows))
        db.commit()
        TP.build_tree(db, d)
        db.commit()
        a = db.query(SeedTosNode).filter(SeedTosNode.document_id == d.id, SeedTosNode.name == "Topic A").order_by(SeedTosNode.id).first()
        subj = db.get(SeedTosNode, a.parent_id)
        subj.official_count, subj.count_corrected = 5, True          # operator correction
        db.commit()
        a_id = a.id
    job_id = make_job({**doc, "scope": a_id, "a": a_id}, {**CFG, "multiplier": 2})
    drain(job_id)
    with session() as db:
        d = db.get(SeedTosDocument, doc["doc"])
        page = db.query(SeedTosPage).filter(SeedTosPage.document_id == d.id).first()
        page.rows = rows[:2]                                            # Topic B is no longer read
        db.commit()
        TP.build_tree(db, d)
        db.commit()
        a = db.get(SeedTosNode, a_id)
        assert a is not None and a.name == "Topic A"                   # same id: the job's work stays attached
        assert db.query(SeedItem).filter(SeedItem.node_id == a_id).count() > 0
        assert db.get(SeedTosNode, a.parent_id).official_count == 5    # the correction survived
