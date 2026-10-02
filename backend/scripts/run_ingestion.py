"""Resumable PDF ingestion runner for the medNAMA pipeline.

Design
------
ingest_book() treats slices (PAGE_CHUNK_SIZE pages) as the unit of atomicity: a
book record in "ready" is skipped, and a book left in "failed"/"processing" by
an interrupted run is resumed at the slice level — slices whose data already
committed (detected via an explicit 'slice' marker in chunk metadata, with a
row-coverage fallback for older data) are skipped, and only the missing slices
are re-processed. A full run is therefore resumable at far better than book
granularity: kill it any time, even mid-book, and the next run finishes only
the outstanding slices instead of redoing the whole book.

This script wraps that with planning and ordering:

  * incomplete books first (failed / stale 'processing' from a crashed run)
  * then new books, smallest page count first (fast, visible wins)
  * ready books are skipped unless --verify finds broken data
  * every book is logged with a machine-readable marker line so the live
    progress dashboard (watch_ingest.py) can track the run

Usage
-----
    python scripts/run_ingestion.py                     # resume from where it left off
    python scripts/run_ingestion.py --dry-run           # show the plan, change nothing
    python scripts/run_ingestion.py --file patho.pdf    # re-ingest / finish one book
    python scripts/run_ingestion.py --force             # re-ingest every book in place (ids kept,
                                                        #   citations remapped: app/reingest.py)
    python scripts/run_ingestion.py --force --file x.pdf  # re-ingest one book
    python scripts/run_ingestion.py --verify            # also re-ingest ready books that have 0 children
    python scripts/run_ingestion.py --workers 0         # parallel slices per book (0 = auto-fit to free RAM)
    python scripts/run_ingestion.py --ram-budget-mb 6000 # cap RAM used by worker models
    python scripts/run_ingestion.py --slice-size 25     # narrower slices (halved per retry)
    python scripts/run_ingestion.py --slice-timeout 300 # fail a slice instead of letting it hang
    python scripts/run_ingestion.py --limit 2           # only process the first N books
    python scripts/run_ingestion.py --watchdog          # keep running until every PDF is ready

Since 2026-09-22 the atomicity is finer-grained: ingest_book() resumes an
interrupted book at the page-slice level (committed slices are detected and
skipped), so a crash/reboot no longer throws away hours of work. Use
--watchdog to make the whole pipeline self-healing: it replans after every
crash, tolerates a briefly-unreachable DB, and only exits once every PDF in
pdfs/ is 'ready' (or a PDF has failed --max-failures times in a row).

Since 2026-09-22 (RAM-aware fix): workers and page-slice width are auto-tuned
to the free RAM every time a book starts, so a 16 GB machine does not pile up
multiple Docling + embedding model copies until it OOMs and hangs. The pool
created for each book is fully reaped between books, and a book that trips its
slice timeout is retried with 1 worker + halved slices while the run keeps
going with the remaining books.

Run the progress dashboard in a second terminal:

    python scripts/watch_ingest.py --log ..\\..\\logs\\ingestion.log
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Make the `app` package importable no matter which cwd the script is run from.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

REPO_ROOT = BACKEND_DIR.parent
DEFAULT_PDFS_DIR = REPO_ROOT / "pdfs"
DEFAULT_LOG = REPO_ROOT / "logs" / "ingestion.log"

# IMPORTANT: app.ingestion attaches a file handler at import time, keyed on
# $MEDNAMA_INGEST_LOG, so the env var must exist BEFORE that import runs. The
# parent process then writes its own log lines (including the "=== [i/N] ..."
# book markers the dashboard tracks) into the same file as the Docling worker
# logs. Default to the standard log path; main() overrides it from --log.
if "--log" in sys.argv:
    _log_idx = sys.argv.index("--log")
    if len(sys.argv) > _log_idx + 1:
        os.environ["MEDNAMA_INGEST_LOG"] = sys.argv[_log_idx + 1]
os.environ.setdefault("MEDNAMA_INGEST_LOG", str(DEFAULT_LOG))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.ingestion import PAGE_CHUNK_SIZE, ingest_book  # noqa: E402

try:  # pragma: no cover - optional runtime dependency for the RAM readout
    import psutil as _psutil
except Exception:
    _psutil = None

BOOK_MARK_RE = r"=== [{i}/{total}] {name} ({pages} pages) ==="

logger = logging.getLogger("run_ingestion")


def load_page_counts(pdf_dir: Path, cache: Path) -> dict[str, int]:
    """Page count per PDF, cached on disk (pypdf is slow on 300MB files).

    Dynamic: always re-globs the directory so a PDF dropped in since the last
    run is discovered; only the pypdf page-count step is cached.
    """
    cached: dict[str, int] = {}
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            if not isinstance(cached, dict):
                cached = {}
        except (json.JSONDecodeError, OSError):
            cached = {}

    names_in_dir = {p.name for p in pdf_dir.glob("*.pdf")}
    for stale in set(cached) - names_in_dir:
        cached.pop(stale, None)

    missing = sorted(names_in_dir - set(cached))
    if missing:
        from pypdf import PdfReader

        for p in sorted(pdf_dir.glob("*.pdf")):
            if p.name not in cached:
                try:
                    cached[p.name] = len(PdfReader(p).pages)
                except Exception:
                    cached[p.name] = 0
        cache.write_text(json.dumps(cached, indent=2))
    return cached


def db_book_state() -> dict[str, tuple[str, int]]:
    """filename -> (status, number of child chunks)."""
    session = SessionLocal()
    try:
        rows = session.execute(text("""
            SELECT b.filename, b.status,
                   (SELECT count(*) FROM chunks c
                     WHERE c.book_id = b.id AND c.embedding IS NOT NULL)
            FROM books b
        """)).fetchall()
        return {r[0]: (r[1], r[2] or 0) for r in rows}
    finally:
        session.close()


def check_db_ready() -> None:
    """Fail fast with a helpful message when the DB is unreachable or empty."""
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1 FROM books LIMIT 1")).fetchall()
    except Exception as e:
        print(f"ERROR: cannot reach the database at {os.environ.get('DATABASE_URL', '')}")
        print(f"  ({e})")
        print("  1. Start the DB:  docker compose up -d")
        print("  2. Re-check:       python scripts/run_ingestion.py --dry-run")
        sys.exit(2)
    finally:
        session.close()


def plan_books(pdf_dir: Path, state: dict, force: bool, verify: bool, only: str | None) -> list[dict]:
    """Classify every PDF in the directory into SKIP / RETRY / NEW jobs."""
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if only:
        pdfs = [p for p in pdfs if p.name.lower() == only.lower()]
        if not pdfs:
            print(f"ERROR: no PDF named '{only}' in {pdf_dir}")
            sys.exit(2)

    jobs = []
    for pdf in pdfs:
        name = pdf.name
        status, child_count = state.get(name, (None, 0))
        if status == "ready":
            if force:
                action = "RETRY(force)"
            elif verify and child_count == 0:
                action = "RETRY(no-data)"
            else:
                action = "SKIP"
        elif status in ("failed", "processing"):
            action = "RETRY(incomplete)"
        else:
            action = "NEW"
        jobs.append({"name": name, "path": pdf, "action": action})

    # Order: incomplete books first (resume exactly where we left off),
    # then new books by ascending page count.
    rank = {"RETRY(incomplete)": 0, "RETRY(force)": 0, "RETRY(no-data)": 0, "NEW": 1, "SKIP": 2}
    jobs.sort(key=lambda j: (rank[j["action"]], j["name"]))
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable medNAMA ingestion runner.")
    parser.add_argument("--pdfs", type=Path, default=DEFAULT_PDFS_DIR, help="Directory containing PDFs")
    parser.add_argument("--file", help="Process a single PDF by filename")
    parser.add_argument("--force", action="store_true",
                        help="Re-ingest ready books in place (with --file: just that one)")
    parser.add_argument("--verify", action="store_true",
                        help="Re-ingest 'ready' books whose data is missing")
    parser.add_argument("--dry-run", action="store_true", help="Show the plan and exit")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N books this run")
    parser.add_argument("--workers", type=int, default=0,
                        help="Page slices processed in parallel per book. 0 = auto-tuned "
                             "to the free RAM (default). Retries always drop to 1 worker.")
    parser.add_argument("--ram-budget-mb", type=int, default=0,
                        help="Cap on RAM (MB) used by worker models across the machine. "
                             "0 = auto (~60%% of total RAM). Used to fit the worker count.")
    parser.add_argument("--slice-size", type=int, default=0,
                        help="Base page-slice width (default 50, or auto-shrunk when free "
                             "RAM is low). Halved on each consecutive retry of a book so a "
                             "pathological page gets isolated.")
    parser.add_argument("--slice-timeout", type=int, default=600,
                        help="Wall-clock seconds a slice may run before it fails instead of hanging (default 600)")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="Log file the dashboard/watch script reads")
    parser.add_argument("--watchdog", action="store_true",
                        help="Keep re-planning until every PDF is 'ready' (self-healing).")
    parser.add_argument("--watchdog-sleep", type=int, default=60,
                        help="Seconds between watchdog passes when work remains (default 60)")
    parser.add_argument("--max-failures", type=int, default=3,
                        help="Give up on a book after N consecutive failed passes (default 3)")
    args = parser.parse_args()

    if not args.pdfs.is_dir():
        print(f"ERROR: PDF directory not found: {args.pdfs}")
        sys.exit(2)
    args.log.parent.mkdir(parents=True, exist_ok=True)

    # Fan Docling / worker logs into the same file from every subprocess.
    os.environ["MEDNAMA_INGEST_LOG"] = str(args.log)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )

    cache = args.log.parent / ".page_counts.json"
    consecutive_failures: dict[str, int] = {}   # filename -> consecutive failures this session
    exhausted: dict[str, bool] = {}             # gave up after --max-failures
    forced_done: set[str] = set()               # --force: re-ingested once this run (the watchdog must not loop)

    while True:
        # DB up? A watchdog must not die just because the DB briefly blinked.
        try:
            check_db_ready()
        except SystemExit:
            if not args.watchdog:
                raise
            print(f"[watchdog] database unreachable - retrying in {args.watchdog_sleep}s... "
                  f"(start the DB with: docker compose up -d)")
            time.sleep(args.watchdog_sleep)
            continue

        counts = load_page_counts(args.pdfs, cache)
        state = db_book_state()
        jobs = plan_books(args.pdfs, state, args.force, args.verify, args.file)
        todo = [j for j in jobs if j["action"] != "SKIP"
                and not (j["action"] == "RETRY(force)" and j["name"] in forced_done)]

        # Drop books that exhausted their failure budget; remember them for the summary.
        todo = [j for j in todo if consecutive_failures.get(j["name"], 0) < args.max_failures]
        for j in jobs:
            if j["action"] != "SKIP" and consecutive_failures.get(j["name"], 0) >= args.max_failures:
                exhausted[j["name"]] = True

        print(f"\nPDFs found: {len(jobs)}   to process now: {len(todo)}")
        for j in jobs:
            print(f"  [{j['action']:>15}] {j['name']}")
        if not todo:
            if exhausted:
                print(f"Gave up on {len(exhausted)} book(s) after {args.max_failures} consecutive failures: "
                      f"{sorted(exhausted)}")
                print("Fix the PDF / logs and re-run; ready books are untouched.")
                sys.exit(1)
            print("All PDFs in pdfs/ are ingested as 'ready'. Nothing left to do.")
            return
        if args.dry_run:
            return
        if args.limit > 0:
            todo = todo[: args.limit]
            print(f"(--limit {args.limit}, processing {len(todo)} books)")

        ok_count = 0
        total = len(todo)
        base_slice = args.slice_size or PAGE_CHUNK_SIZE
        for i, job in enumerate(todo, 1):
            name = job["name"]
            pages = counts.get(name, "?")
            tries = consecutive_failures.get(name, 0)
            # Shrink the slice size on every consecutive failure (50 -> 25 -> 12
            # -> 6 -> 3) so a single pathological page is isolated rather than
            # letting a wide slice hang the whole pipeline, and drop to a single
            # worker on retries so the smallest possible memory footprint is
            # used while a book is already struggling.
            slice_size = max(3, base_slice >> min(tries, 4))
            eff_workers = 1 if tries else (args.workers or None)
            # First attempt: leave the width to ingest_book's RAM-aware auto
            # picker unless the user pinned one. Retries: shrink explicitly.
            slice_arg = slice_size if tries else (args.slice_size or None)
            ram_tag = ""
            if _psutil is not None:
                try:
                    ram_tag = f" | free RAM {_psutil.virtual_memory().available // 2**20} MB"
                except Exception:
                    ram_tag = ""
            marker = f"=== [{i}/{total}] {name} ({pages} pages){ram_tag} ==="
            print(f"\n{marker}")
            logger.info(marker)
            print(f"  (workers={eff_workers or 'auto'}, slice_size={slice_arg or 'auto'})")
            if tries:
                print(f"  (retry {tries + 1}; slice size shrunk and worker count forced to 1)")
            try:
                book_id = ingest_book(job["path"], workers=eff_workers,
                                      slice_size=slice_arg,
                                      slice_timeout=args.slice_timeout,
                                      ram_budget_mb=args.ram_budget_mb or None,
                                      replace=job["action"] == "RETRY(force)")
                consecutive_failures[name] = 0
                if job["action"] == "RETRY(force)":
                    forced_done.add(name)
                ok_count += 1
                print(f"OK: {name} (book_id={book_id})")
            except Exception as e:
                consecutive_failures[name] = consecutive_failures.get(name, 0) + 1
                fail_line = f"=== FAILED {name}: {str(e)[:200]} ==="
                print(fail_line)
                logger.info(fail_line)

        if not args.watchdog:
            break

        pending = total - ok_count
        if pending:
            print(f"\n[watchdog] {pending} book(s) still pending - replanning in {args.watchdog_sleep}s "
                  f"(waiting for: {[j['name'] for j in todo if consecutive_failures.get(j['name'], 0) > 0]})")
        else:
            print("\n[watchdog] pass complete with no failures - sleeping briefly, then confirming all ready.")
        time.sleep(args.watchdog_sleep)

    print("\n" + "=" * 60)
    print(f"Ingestion run finished: {ok_count} succeeded")
    if exhausted:
        print(f"Gave up on: {sorted(exhausted)}")
    sys.exit(1 if exhausted else 0)


if __name__ == "__main__":
    main()