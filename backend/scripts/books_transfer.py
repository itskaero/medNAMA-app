"""Copy re-ingested (or new) books from the PC to the NAS: passages, embeddings, figures, metadata.

The NAS never ingests books itself (not enough memory), so its books, passages and figures carry the
PC's ids. After a book is re-ingested on the PC, its new rows are copied over with the same ids, the old
ones are removed, and concept cards / MCQs on the NAS that cited the old passages are pointed at the new
ones by page and text (app/reingest.py). Embeddings travel with the passages, so the NAS needs no model.

    # PC: write data/transfer/<name>/ (binary COPY files + manifest.json)
    python scripts/books_transfer.py export --book-id 2 --book-id 3
    python scripts/books_transfer.py export --all

    # NAS, inside the backend container (the folder copied to /app/mcqs/transfer/<name>):
    python scripts/books_transfer.py import --dir /app/mcqs/transfer/<name>            # dry run
    python scripts/books_transfer.py import --dir /app/mcqs/transfer/<name> --apply

Each book is replaced in one transaction: if anything fails, the NAS keeps the old version.

Inserting passages keeps the vector search index (HNSW, ~850 MB) up to date row by row, which is slow
on the NAS: about 10k passages took 15 minutes on the PC. For many books at once add --rebuild-index:
the index is dropped, everything is loaded, and the index is built once at the end (searches are
slower, not broken, meanwhile). Run it when nobody is studying.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.models import Book  # noqa: E402

HNSW_INDEX = "idx_chunks_child_embedding_hnsw"
HNSW_DEF = (f"CREATE INDEX {HNSW_INDEX} ON public.chunks USING hnsw (embedding vector_cosine_ops) "
            "WHERE (parent_id IS NOT NULL)")
BOOK_FIELDS = ("title", "filename", "status", "total_pages", "full_title", "authors", "edition", "year",
               "publisher", "isbn", "subject", "page_labels", "aliases", "meta_source")
JSON_FIELDS = {"authors", "page_labels", "aliases"}


def _columns(conn, table: str) -> list[str]:
    return [r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t AND table_schema = 'public' "
        "ORDER BY ordinal_position"), {"t": table})]


def _raw(conn):
    """The DB-API connection under a SQLAlchemy connection (for COPY)."""
    return conn.connection.driver_connection


def _copy_out(raw, sql: str, f) -> None:
    """COPY ... TO STDOUT into a file, with psycopg 3 (PC) or psycopg2 (the NAS image)."""
    with raw.cursor() as cur:
        if hasattr(cur, "copy"):
            with cur.copy(sql) as cp:
                for block in cp:
                    f.write(block)
        else:
            cur.copy_expert(sql, f)


def _copy_in(raw, sql: str, f) -> None:
    """COPY ... FROM STDIN from a file, with psycopg 3 or psycopg2."""
    with raw.cursor() as cur:
        if hasattr(cur, "copy"):
            with cur.copy(sql) as cp:
                while block := f.read(1 << 20):
                    cp.write(block)
        else:
            cur.copy_expert(sql, f, size=1 << 20)


def export(args) -> None:
    db = SessionLocal()
    try:
        q = db.query(Book).filter(Book.status == "ready")
        if not args.all:
            q = q.filter(Book.id.in_(args.book_id or [-1]))
        books = q.order_by(Book.id).all()
        if not books:
            sys.exit("No ready books matched.")
        name = args.name or f"books-{datetime.now():%Y%m%d-%H%M}"
        out = Path(args.out) / name
        out.mkdir(parents=True, exist_ok=True)
        conn = db.connection()
        cols = {t: _columns(conn, t) for t in ("chunks", "figures")}
        manifest = {"created": datetime.now().isoformat(timespec="seconds"), "columns": cols, "books": []}
        raw = _raw(conn)
        for b in books:
            entry = {"id": b.id, **{f: getattr(b, f) for f in BOOK_FIELDS}}
            for table in ("chunks", "figures"):
                path = out / f"{table}-{b.id}.bin"
                col_list = ", ".join(cols[table])
                n = 0
                with open(path, "wb") as f:
                    _copy_out(raw, f"COPY (SELECT {col_list} FROM {table} WHERE book_id = {int(b.id)} ORDER BY id) "
                                   f"TO STDOUT (FORMAT binary)", f)
                n = db.execute(text(f"SELECT count(*) FROM {table} WHERE book_id = :b"), {"b": b.id}).scalar()
                entry[f"{table}_rows"] = n
                entry[f"{table}_bytes"] = path.stat().st_size
            manifest["books"].append(entry)
            print(f"- {b.title} (id {b.id}): {entry['chunks_rows']} passages "
                  f"({entry['chunks_bytes'] / 1e6:.0f} MB), {entry['figures_rows']} figures "
                  f"({entry['figures_bytes'] / 1e6:.0f} MB)")
        (out / "manifest.json").write_text(json.dumps(manifest, default=str, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
        print(f"\nWrote {out}. Copy the folder to the NAS (/DATA/mednama/mcqs/transfer/{name}) and run import there.")
    finally:
        db.close()


def _import_book(entry: dict, folder: Path, cols: dict, apply: bool) -> None:
    from app.reingest import remap, save_snapshot, snapshot

    bid = entry["id"]
    t0 = time.monotonic()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            raw = _raw(conn)
            for table in ("chunks", "figures"):
                conn.execute(text(f"CREATE TEMP TABLE t_{table} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP"))
                col_list = ", ".join(cols[table])
                with open(folder / f"{table}-{bid}.bin", "rb") as f:
                    _copy_in(raw, f"COPY t_{table} ({col_list}) FROM STDIN (FORMAT binary)", f)
                clash = conn.execute(text(
                    f"SELECT count(*) FROM {table} x JOIN t_{table} t USING (id) WHERE x.book_id <> :b"),
                    {"b": bid}).scalar()
                if clash:
                    raise RuntimeError(f"{clash} {table} ids already belong to other books here; not importing")
            n_new = {t: conn.execute(text(f"SELECT count(*) FROM t_{t}")).scalar() for t in ("chunks", "figures")}
            n_old = {t: conn.execute(text(f"SELECT count(*) FROM {t} WHERE book_id = :b"), {"b": bid}).scalar()
                     for t in ("chunks", "figures")}
            exists = conn.execute(text("SELECT 1 FROM books WHERE id = :b"), {"b": bid}).scalar()
            # An unfinished record of the same PDF under another id (an ingest that stopped, later redone
            # from scratch on the PC) would block this one: file names are unique.
            dup = conn.execute(text("SELECT id, status FROM books WHERE filename = :f AND id <> :b"),
                               {"f": entry["filename"], "b": bid}).first()
            if dup:
                refs = conn.execute(text(
                    "SELECT (SELECT count(*) FROM mcqs WHERE book_id = :d) + (SELECT count(*) FROM concept_cards cc "
                    "JOIN chunks c ON c.id = cc.chunk_id WHERE c.book_id = :d)"), {"d": dup.id}).scalar()
                if dup.status == "ready" or refs:
                    raise RuntimeError(f"book {dup.id} here already holds {entry['filename']} "
                                       f"({dup.status}, {refs} references); not replacing it")
            print(f"- {entry['title']} (id {bid}): passages {n_old['chunks']} -> {n_new['chunks']}, "
                  f"figures {n_old['figures']} -> {n_new['figures']}{'' if exists else ' (new book)'}"
                  + (f"; removes unfinished record {dup.id} of the same file" if dup else ""))
            if not apply:
                trans.rollback()
                return
            if dup:
                conn.execute(text("DELETE FROM books WHERE id = :d"), {"d": dup.id})

            from sqlalchemy.orm import Session
            sess = Session(bind=conn)
            snap = snapshot(sess, bid) if exists else None
            if snap:
                save_snapshot(snap)
            fields = {f: (json.dumps(entry[f]) if f in JSON_FIELDS and entry[f] is not None else entry[f])
                      for f in BOOK_FIELDS}
            sets = ", ".join(f"{f} = " + (f"CAST(:{f} AS jsonb)" if f in JSON_FIELDS else f":{f}") for f in BOOK_FIELDS)
            if exists:
                conn.execute(text(f"UPDATE books SET {sets} WHERE id = :id"), {**fields, "id": bid})
            else:
                names = ", ".join(("id",) + BOOK_FIELDS)
                vals = ", ".join([":id"] + [f"CAST(:{f} AS jsonb)" if f in JSON_FIELDS else f":{f}" for f in BOOK_FIELDS])
                conn.execute(text(f"INSERT INTO books ({names}) VALUES ({vals})"), {**fields, "id": bid})
                conn.execute(text("SELECT setval(pg_get_serial_sequence('books', 'id'), "
                                  "GREATEST((SELECT max(id) FROM books), 1))"))
            conn.execute(text("DELETE FROM figures WHERE book_id = :b"), {"b": bid})
            conn.execute(text("DELETE FROM chunks WHERE book_id = :b AND parent_id IS NOT NULL"), {"b": bid})
            conn.execute(text("DELETE FROM chunks WHERE book_id = :b"), {"b": bid})
            for table in ("chunks", "figures"):
                col_list = ", ".join(cols[table])
                # Parents before children (parent_id references chunks.id).
                order = "ORDER BY (parent_id IS NOT NULL), id" if table == "chunks" else "ORDER BY id"
                conn.execute(text(f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM t_{table} {order}"))
                conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                                  f"GREATEST((SELECT max(id) FROM {table}), 1))"))
            conn.execute(text("UPDATE books SET status = :s WHERE id = :b"), {"s": entry["status"], "b": bid})
            trans.commit()
        except Exception:
            trans.rollback()
            raise
    if apply and snap:
        db = SessionLocal()
        try:
            result = remap(db, bid, snap)
            print(f"    references: {result}")
        finally:
            db.close()
    print(f"    done in {time.monotonic() - t0:.0f}s")


def import_(args) -> None:
    folder = Path(args.dir)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    with engine.connect() as conn:
        here = {t: _columns(conn, t) for t in ("chunks", "figures")}
    for t, cols in manifest["columns"].items():
        missing = [c for c in cols if c not in here[t]]
        if missing:
            sys.exit(f"This database's {t} table lacks {missing}: deploy the matching backend first.")
    hnsw = None
    if args.apply and args.rebuild_index:
        with engine.begin() as conn:
            hnsw = conn.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
                                {"n": HNSW_INDEX}).scalar() or HNSW_DEF
            conn.execute(text(f"DROP INDEX IF EXISTS {HNSW_INDEX}"))
            print(f"Dropped {HNSW_INDEX} (if it existed); it is built once after the import.")
    try:
        for entry in manifest["books"]:
            if args.book_id and entry["id"] not in args.book_id:
                continue
            _import_book(entry, folder, manifest["columns"], args.apply)
    finally:
        if hnsw:
            t0 = time.monotonic()
            print("Rebuilding the vector search index (can take a while on the NAS)...")
            with engine.begin() as conn:
                conn.execute(text(f"SET maintenance_work_mem = '{args.index_mem}'"))
                # One process: a parallel build needs more /dev/shm than the Docker default (64 MB) gives.
                conn.execute(text("SET max_parallel_maintenance_workers = 0"))
                conn.execute(text(hnsw))
            print(f"Index rebuilt in {time.monotonic() - t0:.0f}s")
    if not args.apply:
        print("\nDry run (the files were loaded and checked, nothing changed). Re-run with --apply.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["export", "import"])
    ap.add_argument("--book-id", type=int, action="append")
    ap.add_argument("--all", action="store_true", help="export every ready book")
    ap.add_argument("--out", default=str(BACKEND_DIR.parent / "data" / "transfer"))
    ap.add_argument("--name", help="folder name (default books-<date>)")
    ap.add_argument("--dir", help="import: the exported folder")
    ap.add_argument("--apply", action="store_true", help="import: write the changes")
    ap.add_argument("--rebuild-index", action="store_true",
                    help="import: drop the vector index first and build it once at the end (many books)")
    ap.add_argument("--index-mem", default="256MB", help="maintenance_work_mem for the rebuild (NAS: 256MB)")
    args = ap.parse_args()
    export(args) if args.command == "export" else import_(args)


if __name__ == "__main__":
    main()
