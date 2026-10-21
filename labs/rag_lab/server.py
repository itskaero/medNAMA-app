"""Local web UI for the RAG Lab:  python -m labs.rag_lab.server   then open http://127.0.0.1:8765

Binds to 127.0.0.1 unless RAG_LAB_HOST says otherwise (0.0.0.0 to reach it from a phone on the LAN). Runs one experiment at a time in a background thread (the rerankers are
CPU-bound; running two at once would only distort each other's latency and RAM numbers).
"""

from __future__ import annotations

import threading
import traceback
import uuid
from typing import Any

from . import settings

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import store  # noqa: E402
from .configs import DEFAULT_SET, OVERRIDABLE, PRESETS, resolve  # noqa: E402

app = FastAPI(title="MedNAMA RAG Lab")
_jobs: dict[str, dict[str, Any]] = {}
_run_lock = threading.Lock()
STATIC = settings.LAB_DIR / "static"


class RunRequest(BaseModel):
    question: str
    question_id: str | None = None
    level: str = "FCPS-I"
    strictness: str = "strict"
    configs: list[str] = DEFAULT_SET
    overrides: dict[str, Any] = {}
    ai_judge: bool = False


class EvalRequest(BaseModel):
    run_id: str
    configuration: str
    scores: dict[str, int | None] = {}
    pass_fail: dict[str, str | None] = {}
    failure_class: str | None = None
    notes: str = ""
    evaluator: str = ""


class QuestionRequest(BaseModel):
    id: str | None = None
    question: str
    level: str = "FCPS-I"
    intent: str = ""
    expected_components: list[str] = []
    gold_notes: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta():
    from .llm_stages import PASS_FAIL, RUBRIC
    from .metrics import FAILURE_CLASSES
    return {"configs": [{"name": k, "label": c.label, "description": c.description} for k, c in PRESETS.items()],
            "default_configs": DEFAULT_SET, "levels": settings.LEVELS, "strictness": settings.STRICTNESS,
            "overridable": list(OVERRIDABLE), "questions": store.load_questions(), "rubric": RUBRIC,
            "pass_fail": PASS_FAIL, "failure_classes": FAILURE_CLASSES, "device": settings.DEVICE}


def _work(job_id: str, req: RunRequest) -> None:
    from .pipeline import run_question
    job = _jobs[job_id]
    with _run_lock:
        job["status"] = "running"
        try:
            configs = resolve(req.configs, req.overrides)
            item = next((q for q in store.load_questions() if q.get("id") == req.question_id), None) \
                if req.question_id else None
            if not item or item.get("question") != req.question:
                item = {"id": req.question_id or None, "question": req.question}
            out = run_question(item, req.level, req.strictness, configs,
                               progress=lambda m: job.update(progress=m), ai_judge=req.ai_judge)
            job.update(status="done", run_id=out["meta"]["run_id"])
        except Exception as e:
            job.update(status="error", error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc()[-3000:])


@app.post("/api/run")
def run(req: RunRequest):
    if req.level not in settings.LEVELS:
        raise HTTPException(400, f"level must be one of {settings.LEVELS}")
    if req.strictness not in settings.STRICTNESS:
        raise HTTPException(400, "strictness must be strict or tutor")
    try:
        resolve(req.configs, req.overrides)
    except (KeyError, AssertionError, ValueError) as e:
        raise HTTPException(400, str(e))
    job_id = uuid.uuid4().hex[:10]
    _jobs[job_id] = {"status": "queued", "progress": "waiting for the previous run" if _run_lock.locked() else ""}
    threading.Thread(target=_work, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404)
    return _jobs[job_id]


@app.get("/api/runs")
def runs():
    return store.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    data = store.load_run(run_id)
    if not data["meta"]:
        raise HTTPException(404)
    return data


@app.post("/api/evaluations")
def save_eval(req: EvalRequest):
    return store.save_human(req.run_id, req.configuration, req.model_dump(exclude={"run_id", "configuration"}))


@app.post("/api/ai_evaluations/{run_id}/{configuration}")
def ai_eval(run_id: str, configuration: str):
    from .llm_stages import judge
    data = store.load_run(run_id)
    rec = next((r for r in data["results"] if r["configuration"] == configuration), None)
    if not rec or rec.get("error"):
        raise HTTPException(404)
    item = data["meta"].get("item") or {}
    ctx = "\n\n".join(f"[{c['book']}, Page {c['page']}]\n{c['text']}" for c in rec.get("context", []))
    out = judge(rec["question"], rec["level"], rec["answer"].get("answer_markdown", ""), ctx,
                item.get("expected_components"), item.get("gold_notes"))
    store.save_ai_eval(run_id, configuration, out)
    return out


@app.post("/api/questions")
def add_question(req: QuestionRequest):
    try:
        return store.add_question({**req.model_dump(), "relevant_chunk_ids": []})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/improve", response_class=PlainTextResponse)
def improve():
    """The improvement report (markdown): where answers lose evidence, what settings changed, what to fix."""
    from .improve import build, load
    text = build(load(None))
    settings.DATA.mkdir(parents=True, exist_ok=True)
    (settings.DATA / "IMPROVEMENT_REPORT.md").write_text(text, encoding="utf-8")
    return text


@app.get("/api/report", response_class=PlainTextResponse)
def report():
    from .report import build, collect, render
    return render(build(collect()))


def main() -> None:
    import uvicorn
    print(f"MedNAMA RAG Lab on http://{settings.HOST}:{settings.PORT}  (device={settings.DEVICE})")
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_level="warning")


if __name__ == "__main__":
    main()
