"""Pre-write PaedNAMA's topic summaries (all books), so every topic opens at once.

    python -m apps.paednama.scripts.prewrite_sheets                 # both stages, every topic not yet written
    python -m apps.paednama.scripts.prewrite_sheets --stage imm --limit 20 --workers 4

Run on the PC (fast retrieval), then copy them to the NAS with scripts/sync_sheets_to_nas.py. Resumable: a topic
already written with the current prompt is skipped. Uses the same code as opening a topic in the app (study.get_sheet).
"""

from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from apps.paednama.server import registry, settings  # noqa: F401  (backend/ on the path)
from apps.paednama.server import study
from apps.paednama.server.models import PaedsSheet, create_tables
from apps.paednama.server.routes import _ready_books

from app.database import SessionLocal  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def todo(stage: str) -> list[str]:
    db = SessionLocal()
    try:
        done = {t for (t,) in db.query(PaedsSheet.topic_id).filter(
            PaedsSheet.stage == stage, PaedsSheet.lens == "all", PaedsSheet.version >= study.SHEET_VERSION)}
    finally:
        db.close()
    return [t["id"] for s in registry.stage(stage)["syllabus"] for t in s["topics"] if t["id"] not in done]


def write_one(stage: str, topic_id: str, ids: list[int], titles: dict[int, str]) -> str:
    db = SessionLocal()
    try:
        study.get_sheet(db, stage, topic_id, "all", ids, titles, upgrade=True)
        return "ok"
    except study.StudyUnavailable as e:
        return f"skipped: {e}"
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=registry.STAGES, action="append")
    ap.add_argument("--limit", type=int, default=0, help="at most this many topics per stage (0: all)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    create_tables()
    from app.main import warmup_models
    warmup_models()                     # load the models before the worker threads start (HANDOFF §9)

    db = SessionLocal()
    try:
        books = _ready_books(db)
    finally:
        db.close()
    ids, titles = [b["id"] for b in books], {b["id"]: b["title"] for b in books}

    for stage in args.stage or registry.STAGES:
        topics = todo(stage)[: args.limit or None]
        print(f"{stage}: {len(topics)} topics to write", flush=True)
        t0, n, bad = time.monotonic(), 0, 0
        with ThreadPoolExecutor(args.workers) as pool:
            futures = {pool.submit(write_one, stage, t, ids, titles): t for t in topics}
            for f in as_completed(futures):
                n += 1
                try:
                    res = f.result()
                except Exception as e:          # one topic failing never stops the batch
                    res = f"failed: {e}"
                if res != "ok":
                    bad += 1
                    print(f"  {futures[f]}: {res}", flush=True)
                if n % 25 == 0:
                    print(f"  {n}/{len(topics)} ({time.monotonic() - t0:.0f}s)", flush=True)
        print(f"{stage}: wrote {n - bad}, not written {bad}, {time.monotonic() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
