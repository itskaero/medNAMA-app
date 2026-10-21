#!/usr/bin/env python3
"""Copy the TOS refreshers and revision notes written on this PC to the NAS, so learners there open them at once.

What is copied, for every topic of every TOS paper (and every subtopic that has one):
  * refreshers   topic_summaries 'tosref:...' rows, as they are;
  * notes        what the app would serve on the PC: Seed Studio's validated notes (one per subtopic, combined
                 under subtopic headings for the topic) or the cached sheet, written to the NAS under the
                 'tos:...' keys, so the NAS needs no Seed Studio tables to show them.

A sheet that already exists on the NAS is left alone unless --overwrite. Writes only topic_summaries rows.

Usage (repo root, .venv-gpu, dev DB up):
    python scripts/nas_sync_tos_sheets.py --dry-run
    python scripts/nas_sync_tos_sheets.py [--exam pget] [--paper mbbs-p1] [--overwrite]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nas_tunnel import ROOT, nas_database_url  # noqa: E402

sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import tos_sheets  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import TopicSummary  # noqa: E402
from app.tos import tos_registry  # noqa: E402


def collect(db, exam_filter: str | None, paper_filter: str | None) -> list[dict]:
    """Every sheet available on the PC, keyed as the NAS app will look it up."""
    rows = []
    for exam_id, exam in tos_registry.examinations.items():
        if exam_filter and exam_id != exam_filter:
            continue
        for paper in exam["papers"]:
            if paper_filter and paper["id"] != paper_filter:
                continue
            for s in paper["subjects"]:
                for t in s["topics"]:
                    for sub in [None, *t.get("subtopics", [])]:
                        for kind in tos_sheets.KINDS:
                            sheet = tos_sheets.get_sheet(db, kind, exam_id, paper, s, t, sub, write=False)
                            if not sheet.get("available"):
                                continue
                            md, cites = sheet["markdown"], sheet.get("citations") or []
                            if sheet.get("origin") == "seed" and not sub:
                                parts = [(f"### {x['subtopic']}\n\n" if x["subtopic"] else "") + x["markdown"]
                                         for x in sheet["sheets"]]
                                md = "\n\n---\n\n".join(parts)
                                cites = [c for x in sheet["sheets"] for c in (x.get("citations") or [])]
                            if not md.strip():
                                continue
                            rows.append({"scope_key": tos_sheets.scope_key(kind, exam_id, paper["id"], t["id"], sub),
                                         "label": sheet["label"], "markdown": md, "citations": cites,
                                         "access": "open"})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exam")
    ap.add_argument("--paper")
    ap.add_argument("--overwrite", action="store_true", help="replace sheets the NAS already has")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pc = SessionLocal()
    try:
        rows = collect(pc, args.exam, args.paper)
    finally:
        pc.close()
    refreshers = sum(r["scope_key"].startswith("tosref:") for r in rows)
    print(f"On this PC: {len(rows)} sheets ({refreshers} refreshers, {len(rows) - refreshers} notes)", flush=True)
    if args.dry_run or not rows:
        return 0

    with nas_database_url() as url:
        engine = create_engine(url.replace("postgresql://", "postgresql+psycopg2://", 1), pool_pre_ping=True)
        nas = sessionmaker(bind=engine)()
        try:
            have = {k for (k,) in nas.query(TopicSummary.scope_key).filter(
                TopicSummary.scope_key.in_([r["scope_key"] for r in rows]))}
            todo = rows if args.overwrite else [r for r in rows if r["scope_key"] not in have]
            for i in range(0, len(todo), 200):
                stmt = insert(TopicSummary.__table__).values(todo[i:i + 200])
                stmt = stmt.on_conflict_do_update(index_elements=["scope_key"], set_={
                    "label": stmt.excluded.label, "markdown": stmt.excluded.markdown,
                    "citations": stmt.excluded.citations}) if args.overwrite else stmt.on_conflict_do_nothing(
                    index_elements=["scope_key"])
                nas.execute(stmt)
            nas.commit()
            print(f"NAS: {len(have)} already there, {len(todo)} written{' (overwrite)' if args.overwrite else ''}.")
        finally:
            nas.close()
            engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
