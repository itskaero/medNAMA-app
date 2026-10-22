"""Copy the topic summaries written on this PC (prewrite_sheets.py) to the NAS, so they open at once there.

    python -m apps.paednama.scripts.sync_sheets_to_nas --dry-run
    python -m apps.paednama.scripts.sync_sheets_to_nas [--overwrite]

Copies paeds_sheets rows written with the current prompt. A summary that already exists on the NAS (someone opened
the topic there first) is left alone unless --overwrite. Citations name book ids and pages; the NAS holds the same
books under the same ids (backend/scripts/books_transfer.py), so they resolve there. Goes through the SSH tunnel in
scripts/nas_tunnel.py; writes nothing else.
"""

from __future__ import annotations

import argparse
import sys

from apps.paednama.server import settings  # noqa: F401  (backend/ on the path)
from apps.paednama.server.models import PaedsSheet
from apps.paednama.server.study import SHEET_VERSION

sys.path.insert(0, str(settings.REPO / "scripts"))
from nas_tunnel import nas_database_url  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.database import SessionLocal  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    rows = [{"stage": s.stage, "topic_id": s.topic_id, "lens": s.lens, "markdown": s.markdown, "citations": s.citations,
             "version": s.version, "created_at": s.created_at}
            for s in db.query(PaedsSheet).filter(PaedsSheet.version >= SHEET_VERSION)]
    db.close()
    print(f"PC: {len(rows)} summaries written with prompt v{SHEET_VERSION}")

    with nas_database_url() as url:
        engine = create_engine(url)
        with engine.begin() as conn:
            if not conn.execute(text("SELECT to_regclass('public.paeds_sheets')")).scalar():
                sys.exit("The NAS has no paeds_sheets table: deploy PaedNAMA's backend there first.")
            have = {(r.stage, r.topic_id, r.lens): r.version for r in conn.execute(
                text("SELECT stage, topic_id, lens, version FROM paeds_sheets"))}
            todo = [r for r in rows if args.overwrite or have.get((r["stage"], r["topic_id"], r["lens"]), 0) < SHEET_VERSION]
            print(f"NAS: {len(have)} already there; {len(todo)} to copy")
            if args.dry_run or not todo:
                return
            table = PaedsSheet.__table__
            for i in range(0, len(todo), 200):
                stmt = insert(table).values(todo[i:i + 200])
                stmt = stmt.on_conflict_do_update(constraint="uq_paeds_sheet", set_={
                    "markdown": stmt.excluded.markdown, "citations": stmt.excluded.citations,
                    "version": stmt.excluded.version})
                conn.execute(stmt)
            print(f"Copied {len(todo)}.")


if __name__ == "__main__":
    main()
