"""Seed Studio web server:  python -m labs.seed_studio.server   then open http://127.0.0.1:8770

Sign in with a medNAMA admin account (the same users table and password hashes as the app); the session is an
HttpOnly cookie signed with the backend's JWT secret for this audience only. No API key ever reaches the browser:
generation runs here, server-side, with the root .env's keys.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from . import settings
from .common import audit, cost_usd, session

import jwt  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request, Response  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.seed_models import (SeedAudit, SeedBatch, SeedItem, SeedJob, SeedNote, SeedTosDocument,  # noqa: E402
                             SeedTosNode, SeedTosPage)

from . import engine as E  # noqa: E402
from . import progress as P  # noqa: E402
from . import tos_parse as TP  # noqa: E402

logger = logging.getLogger("seed_studio")
app = FastAPI(title="medNAMA Seed Studio")
COOKIE = "seed_session"
AUDIENCE = "seed-studio"


# ─── auth ─────────────────────────────────────────────────────────────────────────────────────────

def _secret() -> str:
    from app.auth import JWT_SECRET_KEY
    return JWT_SECRET_KEY


def operator(request: Request) -> str:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Sign in with a medNAMA admin account.")
    try:
        data = jwt.decode(token, _secret(), algorithms=["HS256"], audience=AUDIENCE)
    except jwt.PyJWTError:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    return data["sub"]


class Login(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: Login, response: Response):
    from app.auth import verify_password
    from app.models import User

    from sqlalchemy.exc import OperationalError

    try:
        with session() as db:
            db.execute(text("SELECT 1"))
    except OperationalError:
        raise HTTPException(503, "The database is not reachable. Start Docker (the dev database on port 5434) and try again.")
    with session() as db:
        user = db.query(User).filter(User.username == body.username.strip()).first()
        ok = user is not None and user.role == "admin" and user.is_active is not False and \
            verify_password(body.password, user.password_hash)
        username = user.username if ok else None   # read before commit: the row is expired after it
        audit(db, body.username.strip()[:60], "login" if ok else "login_failed", "operator", body.username.strip()[:60])
        db.commit()
    if not ok:
        raise HTTPException(401, "Wrong username or password, or this account is not an admin.")
    token = jwt.encode({"sub": username, "aud": AUDIENCE, "exp": datetime.utcnow() + timedelta(hours=12)},
                       _secret(), algorithm="HS256")
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=12 * 3600)
    return {"username": username}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(who: str = Depends(operator)):
    return {"username": who}


# ─── overview, documents, tree ────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    # no-cache: browsers otherwise keep showing an old copy of the page for hours after it changes
    return FileResponse(settings.STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/overview")
def overview(who: str = Depends(operator)):
    with session() as db:
        m = P.global_metrics(db)
        m["jobs"] = [_job_row(db, j) for j in db.query(SeedJob).filter(SeedJob.status.in_(E.ACTIVE))
                     .order_by(SeedJob.created_at.desc()).limit(20)]
        m["workers"] = E.running_info()
        return m


def _doc_row(db, d: SeedTosDocument) -> dict:
    pages = dict(db.execute(text("SELECT status, count(*) FROM seed_tos_pages WHERE document_id = :d GROUP BY 1"),
                            {"d": d.id}).all())
    return {"id": d.id, "family": d.family, "title": d.title, "version": d.version, "source_path": d.source_path,
            "source_url": d.source_url, "has_counts": d.has_counts, "page_count": d.page_count,
            "parse_status": "parsing" if TP.parse_running(d.id) else d.parse_status, "parse_error": d.parse_error,
            "parse_method": d.parse_method, "pages": pages, "multiplier": d.multiplier,
            "base_per_topic": d.base_per_topic, "parsed_at": d.parsed_at.isoformat() if d.parsed_at else None,
            "nodes": db.query(SeedTosNode).filter(SeedTosNode.document_id == d.id).count()}


@app.get("/api/documents")
def documents(who: str = Depends(operator)):
    with session() as db:
        return [_doc_row(db, d) for d in db.query(SeedTosDocument).order_by(SeedTosDocument.family, SeedTosDocument.id)]


@app.post("/api/documents/register")
def register(who: str = Depends(operator)):
    return {"added": TP.register_documents()}


@app.post("/api/documents/import-json")
def import_json(who: str = Depends(operator)):
    """Rebuild every document that has a curated JSON file (data/pget_tos.json, data/fcps_part1_tos.json)."""
    from .tos_json import import_all
    return import_all(actor=who, force=True)


class ParseReq(BaseModel):
    restart: bool = False


@app.post("/api/documents/{doc_id}/parse")
def parse(doc_id: int, body: ParseReq, who: str = Depends(operator)):
    try:
        started = TP.start_parse(doc_id, who, body.restart)
    except KeyError:
        raise HTTPException(404, "No such document.")
    if not started:
        raise HTTPException(409, "This document is already being read.")
    return {"started": True}


@app.get("/api/documents/{doc_id}/pages")
def pages(doc_id: int, who: str = Depends(operator)):
    with session() as db:
        return [{"page_no": p.page_no, "status": p.status, "error": p.error, "model": p.model,
                 "rows": len(p.rows or [])} for p in db.query(SeedTosPage).filter(SeedTosPage.document_id == doc_id)
                .order_by(SeedTosPage.page_no)]


@app.get("/api/documents/{doc_id}/pages/{page_no}")
def page_detail(doc_id: int, page_no: int, who: str = Depends(operator)):
    with session() as db:
        p = db.query(SeedTosPage).filter(SeedTosPage.document_id == doc_id, SeedTosPage.page_no == page_no).first()
        if p is None:
            raise HTTPException(404, "That page has not been read yet.")
        return {"page_no": p.page_no, "status": p.status, "error": p.error, "model": p.model,
                "rows": [{k: v for k, v in r.items() if not k.startswith("_")} for r in (p.rows or [])], "text": p.text}


@app.get("/api/documents/{doc_id}/pdf")
def pdf(doc_id: int, who: str = Depends(operator)):
    with session() as db:
        d = db.get(SeedTosDocument, doc_id)
        if d is None:
            raise HTTPException(404)
        return FileResponse(TP.pdf_path(d), media_type="application/pdf")


@app.get("/api/documents/{doc_id}/page/{page_no}.png")
def page_png(doc_id: int, page_no: int, who: str = Depends(operator)):
    with session() as db:
        d = db.get(SeedTosDocument, doc_id)
        if d is None or not 1 <= page_no <= (d.page_count or 0):
            raise HTTPException(404)
        return Response(TP.render_page(d, page_no, scale=1.3), media_type="image/png")


@app.get("/api/documents/{doc_id}/tree")
def tree(doc_id: int, who: str = Depends(operator)):
    with session() as db:
        d = db.get(SeedTosDocument, doc_id)
        if d is None:
            raise HTTPException(404)
        t = P.document_tree(db, d)
        rows = [t["nodes"][i] for i in t["order"] if i in t["nodes"]]
        root = next((r for r in rows if r["parent_id"] is None), None)
        return {"document": _doc_row(db, d), "summary": P.summarise(root) if root else None, "nodes": rows}


@app.get("/api/search")
def search(q: str, who: str = Depends(operator)):
    """Topics, subjects and sub-topics across every read document whose name contains q, with their targets."""
    q = " ".join(q.split())[:80]
    if len(q) < 2:
        return []
    with session() as db:
        hits = (db.query(SeedTosNode).filter(SeedTosNode.name.ilike(f"%{q}%"),
                                             SeedTosNode.level.in_(("specialty", "subject", "topic", "subtopic")))
                .order_by(SeedTosNode.level.in_(("subtopic",)), SeedTosNode.document_id, SeedTosNode.position).limit(20).all())
        out, trees = [], {}
        for n in hits:
            if n.document_id not in trees:
                trees[n.document_id] = P.document_tree(db, db.get(SeedTosDocument, n.document_id))["nodes"]
            r = trees[n.document_id].get(n.id, {})
            doc = db.get(SeedTosDocument, n.document_id)
            where = "PGET" if doc.family == "PGET" else f"FCPS-I {doc.title.split(': ')[-1]}"
            out.append({"id": n.id, "document_id": n.document_id, "name": n.name, "path": n.path, "level": n.level,
                        "where": " › ".join([where, *n.path.split(" > ")[1:-1]]),
                        "planned": r.get("planned"), "inserted": r.get("inserted")})
        return out


class DocPatch(BaseModel):
    multiplier: float | None = None
    base_per_topic: int | None = None


@app.patch("/api/documents/{doc_id}")
def patch_doc(doc_id: int, body: DocPatch, who: str = Depends(operator)):
    with session() as db:
        d = db.get(SeedTosDocument, doc_id)
        if d is None:
            raise HTTPException(404)
        before = {"multiplier": d.multiplier, "base_per_topic": d.base_per_topic}
        if body.multiplier is not None:
            if not 0.5 <= body.multiplier <= 20:
                raise HTTPException(400, "The multiplier must be between 0.5 and 20.")
            d.multiplier = body.multiplier
        if body.base_per_topic is not None:
            if not 0 <= body.base_per_topic <= 50:
                raise HTTPException(400, "Base per topic must be between 0 and 50.")
            d.base_per_topic = body.base_per_topic
        audit(db, who, "target_change", "document", doc_id, before=before,
              after={"multiplier": d.multiplier, "base_per_topic": d.base_per_topic})
        db.commit()
        return _doc_row(db, d)


class NodePatch(BaseModel):
    target_override: int | None = None
    clear_override: bool = False
    official_count: int | None = None     # a correction of a mis-read count, checked against the source page
    clear_official: bool = False
    reason: str = ""


@app.patch("/api/nodes/{node_id}")
def patch_node(node_id: int, body: NodePatch, who: str = Depends(operator)):
    with session() as db:
        n = db.get(SeedTosNode, node_id)
        if n is None:
            raise HTTPException(404)
        before = {"target_override": n.target_override, "official_count": n.official_count}
        if body.clear_override:
            n.target_override = None
        elif body.target_override is not None:
            if body.target_override < 0:
                raise HTTPException(400, "A target cannot be negative.")
            n.target_override = body.target_override
        if body.clear_official:
            n.official_count, n.count_corrected = None, False
        elif body.official_count is not None:
            if not body.reason.strip():
                raise HTTPException(400, "Say why the printed count is being corrected (e.g. 'page 31 shows 3').")
            n.official_count, n.count_corrected = body.official_count, True   # survives re-reading the document
        audit(db, who, "target_change" if body.target_override is not None or body.clear_override else "count_correction",
              "node", node_id, path=n.path, before=before,
              after={"target_override": n.target_override, "official_count": n.official_count}, reason=body.reason)
        db.commit()
        return {"ok": True}


# ─── jobs ─────────────────────────────────────────────────────────────────────────────────────────

class JobReq(BaseModel):
    document_id: int
    scope_node_id: int
    config: dict = {}
    dry_run: bool = False
    confirm: bool = False
    expected_batches: int | None = None


@app.post("/api/jobs/preview")
def preview(body: JobReq, who: str = Depends(operator)):
    with session() as db:
        d = db.get(SeedTosDocument, body.document_id)
        if d is None:
            raise HTTPException(404, "No such document.")
        try:
            return E.manifest(db, d, body.scope_node_id, body.config)
        except KeyError as e:
            raise HTTPException(400, str(e))


@app.post("/api/jobs")
def create(body: JobReq, who: str = Depends(operator)):
    if not body.dry_run and not body.confirm:
        raise HTTPException(400, "Confirm the job first: generation spends AI credits and writes to the database.")
    with session() as db:
        d = db.get(SeedTosDocument, body.document_id)
        if d is None:
            raise HTTPException(404, "No such document.")
        try:
            job = E.create_job(db, who, d, body.scope_node_id, body.config, body.dry_run, body.expected_batches)
        except (KeyError, ValueError) as e:
            raise HTTPException(409, str(e))
        return _job_row(db, job)


def _job_row(db, j: SeedJob) -> dict:
    b = dict(db.execute(text("SELECT kind || ':' || status, count(*) FROM seed_batches WHERE job_id = :j GROUP BY 1"),
                        {"j": j.id}).all())
    items = dict(db.execute(text("SELECT status, count(*) FROM seed_items WHERE job_id = :j GROUP BY 1"), {"j": j.id}).all())
    tok = db.execute(text("SELECT coalesce(sum(tokens_in),0), coalesce(sum(tokens_out),0), "
                          "coalesce(sum(jsonb_array_length(errors)),0), coalesce(sum(attempts),0) "
                          "FROM seed_batches WHERE job_id = :j"), {"j": j.id}).first()
    scope = db.get(SeedTosNode, j.scope_node_id)
    m = j.manifest or {}
    mcq_total = sum(v for k, v in b.items() if k.startswith("mcq:"))
    mcq_done = sum(v for k, v in b.items() if k.startswith("mcq:") and k.split(":")[1] in ("done", "failed", "cancelled"))
    notes_total = sum(v for k, v in b.items() if k.startswith("note:"))
    notes_done = db.query(SeedNote).filter(SeedNote.job_id == j.id, SeedNote.status == "done").count()
    end = j.finished_at or datetime.utcnow()
    return {
        "id": j.id, "status": j.status, "dry_run": j.dry_run, "created_by": j.created_by,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "elapsed_s": int((end - j.started_at).total_seconds()) if j.started_at else 0,
        "document_id": j.document_id, "scope": scope.path if scope else None, "scope_node_id": j.scope_node_id,
        "error": j.error, "config": j.config,
        "planned_questions": m.get("need_questions"), "planned_notes": m.get("notes_need"),
        "batches": b, "mcq_batches": mcq_total, "mcq_batches_finished": mcq_done,
        "note_batches": notes_total, "notes_done": notes_done,
        "generated": sum(items.values()), "validated": items.get("validated", 0) + items.get("inserted", 0),
        "inserted": items.get("inserted", 0), "rejected": items.get("rejected", 0),
        "duplicate": items.get("duplicate", 0),
        "tokens_in": int(tok[0]), "tokens_out": int(tok[1]), "failed_attempts": int(tok[2]),
        "retries": max(0, int(tok[3]) - sum(b.values()) + b.get("mcq:pending", 0) + b.get("note:pending", 0)),
        "cost_usd": cost_usd(int(tok[0]), int(tok[1])),
    }


@app.get("/api/jobs")
def jobs(status: str | None = None, who: str = Depends(operator)):
    with session() as db:
        q = db.query(SeedJob)
        if status:
            q = q.filter(SeedJob.status.in_(status.split(",")))
        return [_job_row(db, j) for j in q.order_by(SeedJob.created_at.desc()).limit(200)]


def _batch_row(db, b: SeedBatch) -> dict:
    n = db.get(SeedTosNode, b.node_id)
    started = b.started_at
    return {"id": b.id, "kind": b.kind, "node_id": b.node_id, "path": n.path if n else None, "difficulty": b.difficulty,
            "seq": b.seq, "target": b.target, "status": b.status, "stage": b.stage, "attempts": b.attempts,
            "errors": b.errors or [], "progress": b.progress or {}, "tokens_in": b.tokens_in, "tokens_out": b.tokens_out,
            "worker": b.worker, "started_at": started.isoformat() if started else None,
            "heartbeat_at": b.heartbeat_at.isoformat() if b.heartbeat_at else None,
            "finished_at": b.finished_at.isoformat() if b.finished_at else None,
            "elapsed_s": int(((b.finished_at or datetime.utcnow()) - started).total_seconds()) if started else None}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str, status: str | None = None, who: str = Depends(operator)):
    with session() as db:
        j = db.get(SeedJob, job_id)
        if j is None:
            raise HTTPException(404)
        q = db.query(SeedBatch).filter(SeedBatch.job_id == job_id)
        if status:
            q = q.filter(SeedBatch.status.in_(status.split(",")))
        batches = q.order_by(SeedBatch.kind, SeedBatch.id).limit(2000).all()
        running = [_batch_row(db, b) for b in batches if b.status == "running"]
        return {"job": _job_row(db, j), "running": running, "batches": [_batch_row(db, b) for b in batches],
                "manifest": {k: v for k, v in (j.manifest or {}).items() if k != "units"},
                "units": (j.manifest or {}).get("units", [])}


@app.get("/api/jobs/{job_id}/manifest")
def job_manifest(job_id: str, who: str = Depends(operator)):
    with session() as db:
        j = db.get(SeedJob, job_id)
        if j is None:
            raise HTTPException(404)
        return JSONResponse(j.manifest or {}, headers={"Content-Disposition": f'attachment; filename="manifest-{job_id}.json"'})


@app.post("/api/jobs/{job_id}/{action}")
def job_action(job_id: str, action: str, who: str = Depends(operator)):
    if action not in ("pause", "resume", "cancel", "retry_failed"):
        raise HTTPException(404)
    with session() as db:
        j = db.get(SeedJob, job_id)
        if j is None:
            raise HTTPException(404)
        try:
            E.control(db, who, j, action)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return _job_row(db, j)


# ─── content review ───────────────────────────────────────────────────────────────────────────────

@app.get("/api/items")
def items(node_id: int | None = None, job_id: str | None = None, status: str | None = None, review: str | None = None,
          document_id: int | None = None, limit: int = 50, offset: int = 0, who: str = Depends(operator)):
    from app.models import MCQ

    with session() as db:
        q = db.query(SeedItem)
        if node_id:
            q = q.filter(SeedItem.node_id == node_id)
        if job_id:
            q = q.filter(SeedItem.job_id == job_id)
        if status:
            q = q.filter(SeedItem.status.in_(status.split(",")))
        if review:
            q = q.filter(SeedItem.review_state == review)
        if document_id:
            q = q.filter(SeedItem.node_id.in_(db.query(SeedTosNode.id).filter(SeedTosNode.document_id == document_id)))
        total = q.count()
        rows = q.order_by(SeedItem.id.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200))).all()
        mcqs = {m.id: m.status for m in db.query(MCQ.id, MCQ.status).filter(MCQ.id.in_([r.mcq_id for r in rows if r.mcq_id] or [-1]))}
        nodes = {n.id: n.path for n in db.query(SeedTosNode).filter(SeedTosNode.id.in_({r.node_id for r in rows} or {-1}))}
        return {"total": total, "items": [{
            "id": r.id, "job_id": r.job_id, "node_id": r.node_id, "path": nodes.get(r.node_id), "status": r.status,
            "reason": r.reason, "difficulty": r.difficulty, "subtopic": r.subtopic, "payload": r.payload,
            "mcq_id": r.mcq_id, "mcq_status": mcqs.get(r.mcq_id), "review_state": r.review_state,
            "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}


class Review(BaseModel):
    state: str          # approved | flagged | none
    note: str = ""


@app.post("/api/items/{item_id}/review")
def review_item(item_id: int, body: Review, who: str = Depends(operator)):
    from app.models import MCQ

    if body.state not in ("approved", "flagged", "none"):
        raise HTTPException(400)
    with session() as db:
        it = db.get(SeedItem, item_id)
        if it is None:
            raise HTTPException(404)
        if body.state == "approved" and (it.status != "inserted" or not it.mcq_id):
            raise HTTPException(409, "Only an inserted question can be approved (published).")
        before = it.review_state
        it.review_state = body.state
        if it.mcq_id:
            m = db.get(MCQ, it.mcq_id)
            if m is not None:
                # approve publishes; flagging or clearing takes it back out of what students are served
                m.status = "ready" if body.state == "approved" else "pending"
        audit(db, who, f"review_{body.state}", "item", item_id, before=before, mcq_id=it.mcq_id, note=body.note)
        db.commit()
        return {"ok": True}


class BulkReview(BaseModel):
    node_id: int | None = None
    job_id: str | None = None


@app.post("/api/items/approve_all")
def approve_all(body: BulkReview, who: str = Depends(operator)):
    """Publish every inserted, unflagged question of a unit or job (flagged ones stay held)."""
    from app.models import MCQ

    if not body.node_id and not body.job_id:
        raise HTTPException(400, "Give a unit or a job.")
    with session() as db:
        q = db.query(SeedItem).filter(SeedItem.status == "inserted", SeedItem.mcq_id.isnot(None),
                                      SeedItem.review_state == "none")
        q = q.filter(SeedItem.node_id == body.node_id) if body.node_id else q.filter(SeedItem.job_id == body.job_id)
        rows = q.all()
        for it in rows:
            it.review_state = "approved"
        db.query(MCQ).filter(MCQ.id.in_([it.mcq_id for it in rows] or [-1])).update({"status": "ready"}, synchronize_session=False)
        audit(db, who, "approve_all", "node" if body.node_id else "job", body.node_id or body.job_id, count=len(rows))
        db.commit()
        return {"approved": len(rows)}


@app.get("/api/notes")
def notes(node_id: int | None = None, document_id: int | None = None, status: str | None = None,
          limit: int = 50, offset: int = 0, who: str = Depends(operator)):
    with session() as db:
        q = db.query(SeedNote)
        if node_id:
            q = q.filter(SeedNote.node_id == node_id)
        if document_id:
            q = q.filter(SeedNote.node_id.in_(db.query(SeedTosNode.id).filter(SeedTosNode.document_id == document_id)))
        if status:
            q = q.filter(SeedNote.status == status)
        total = q.count()
        rows = q.order_by(SeedNote.id.desc()).offset(offset).limit(min(limit, 200)).all()
        nodes = {n.id: n.path for n in db.query(SeedTosNode).filter(SeedTosNode.id.in_({r.node_id for r in rows} or {-1}))}
        # how the note was planned (outline, intent, which books and why) lives on its batch
        plans = {b.id: (b.progress or {}).get("plan") for b in
                 db.query(SeedBatch).filter(SeedBatch.id.in_({r.batch_id for r in rows if r.batch_id} or {-1}))}
        return {"total": total, "notes": [{"id": r.id, "node_id": r.node_id, "path": nodes.get(r.node_id), "job_id": r.job_id,
                                           "plan": plans.get(r.batch_id),
                                           "status": r.status, "markdown": r.markdown, "citations": r.citations,
                                           "reason": r.reason, "review_state": r.review_state,
                                           "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}


@app.post("/api/notes/{note_id}/review")
def review_note(note_id: int, body: Review, who: str = Depends(operator)):
    if body.state not in ("approved", "flagged", "none"):
        raise HTTPException(400)
    with session() as db:
        n = db.get(SeedNote, note_id)
        if n is None:
            raise HTTPException(404)
        if n.status != "done" and body.state == "approved":
            raise HTTPException(409, "A failed note cannot be approved.")
        before, n.review_state = n.review_state, body.state
        audit(db, who, f"note_review_{body.state}", "note", note_id, before=before, note=body.note)
        db.commit()
        return {"ok": True}


# ─── diagnostics, settings, audit ─────────────────────────────────────────────────────────────────

@app.get("/api/failures")
def failures(who: str = Depends(operator)):
    with session() as db:
        failed = [_batch_row(db, b) for b in db.query(SeedBatch).filter(SeedBatch.status == "failed")
                  .order_by(SeedBatch.finished_at.desc().nullslast()).limit(300)]
        reasons = db.execute(text("""
            SELECT status, split_part(coalesce(reason, ''), ':', 1) AS why, count(*)
            FROM seed_items WHERE status IN ('rejected', 'duplicate') GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30""")).all()
        errors = db.execute(text("""
            SELECT e->>'kind', left(e->>'message', 120), count(*)
            FROM seed_batches, jsonb_array_elements(errors) e GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30""")).all()
        failed_notes = db.query(SeedNote).filter(SeedNote.status == "failed").order_by(SeedNote.id.desc()).limit(200).all()
        nodes = {n.id: n.path for n in db.query(SeedTosNode).filter(SeedTosNode.id.in_({x.node_id for x in failed_notes} or {-1}))}
        pages = db.query(SeedTosPage).filter(SeedTosPage.status == "failed").limit(200).all()
        return {"failed_batches": failed,
                "rejection_reasons": [{"status": s, "reason": w or "(none)", "count": n} for s, w, n in reasons],
                "error_kinds": [{"kind": k, "message": m, "count": n} for k, m, n in errors],
                "failed_notes": [{"id": x.id, "path": nodes.get(x.node_id), "reason": x.reason, "job_id": x.job_id} for x in failed_notes],
                "failed_pages": [{"document_id": p.document_id, "page_no": p.page_no, "error": p.error} for p in pages]}


@app.get("/api/settings")
def get_settings(who: str = Depends(operator)):
    from app.config import settings as app_settings
    from app.llm import endpoint, llm_configured
    from app.photo_mcq import FALLBACK_MODELS, configured

    return {"generation": {"role": "fast", "model": endpoint("fast").model, "configured": llm_configured("fast")},
            "notes": {"role": "chat", "model": endpoint("chat").model, "configured": llm_configured("chat")},
            "tos_reader": {"model": app_settings.vision_model, "fallbacks": list(FALLBACK_MODELS), "configured": configured()},
            "workers": settings.WORKERS, "host": settings.HOST, "port": settings.PORT,
            "prices": {"input_per_m": settings.PRICE_IN, "output_per_m": settings.PRICE_OUT},
            "defaults": E.DEFAULT_CONFIG, "stale_after_s": settings.STALE_S}


@app.get("/api/audit")
def audit_log(limit: int = 200, who: str = Depends(operator)):
    with session() as db:
        return [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "entity": a.entity,
                 "entity_id": a.entity_id, "detail": a.detail}
                for a in db.query(SeedAudit).order_by(SeedAudit.id.desc()).limit(min(limit, 1000))]


@app.get("/api/stream")
async def stream(request: Request, who: str = Depends(operator)):
    """Server-sent events: active jobs (with their running batches) every 2 s, read from the database."""
    async def gen():
        while not await request.is_disconnected():
            def snapshot() -> dict[str, Any]:
                with session() as db:
                    active = db.query(SeedJob).filter(SeedJob.status.in_(E.ACTIVE)).order_by(SeedJob.created_at).all()
                    out = []
                    for j in active:
                        row = _job_row(db, j)
                        row["running"] = [_batch_row(db, b) for b in db.query(SeedBatch).filter(
                            SeedBatch.job_id == j.id, SeedBatch.status == "running")]
                        out.append(row)
                    parsing = [d.id for d in db.query(SeedTosDocument) if TP.parse_running(d.id)]
                    return {"at": datetime.utcnow().isoformat() + "Z", "jobs": out, "parsing": parsing,
                            "workers": E.running_info()}
            data = await asyncio.get_running_loop().run_in_executor(None, snapshot)
            yield f"data: {json.dumps(data, default=str)}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.on_event("startup")
def startup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("app.retrieval").setLevel(logging.WARNING)
    try:
        added = TP.register_documents()
        if added:
            logger.info("Registered %d TOS document(s)", added)
    except Exception:
        logger.exception("TOS registration failed")
    try:
        from .tos_json import import_all
        res = import_all()
        if res["imported"] or res["unmatched"]:
            logger.info("Curated TOS JSON: imported %s; unmatched %s", [r["document"] for r in res["imported"]], res["unmatched"])
    except Exception:
        logger.exception("Curated TOS import failed")
    E.start_workers()


# TOS sheets: pre-write refreshers and revision notes for the TOS screens (labs/seed_studio/sheets.py).
from .sheets import router as _sheets_router  # noqa: E402

app.include_router(_sheets_router)

# Station Studio: TOACS / viva stations, gold answers and scorer calibration (labs/seed_studio/stations_studio.py).
from .stations_studio import router as _stations_router  # noqa: E402

app.include_router(_stations_router)


def main() -> None:
    import uvicorn
    print(f"medNAMA Seed Studio on http://{settings.HOST}:{settings.PORT}  ({settings.WORKERS} workers)")
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_level="warning")


if __name__ == "__main__":
    main()
