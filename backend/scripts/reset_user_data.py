"""Remove all user-related data, keeping every account except the ones named (default: admin).

Clears study history (answers, quiz attempts, review schedules, Daily Dose / sprint sessions,
streaks, mock sittings, personal timed papers, duels), chats, bookmarks, notes, flashcards and
answer reports, then deletes the other accounts. Content is untouched: books, chunks, figures,
MCQs, concept cards, look-alike pairs, recall bank, past papers, shared weekly papers, topic
summaries.

Dry run by default (prints what would be removed). Everything runs in one transaction.
Imports no ML models, so it is safe to run inside the NAS backend container.

Usage (from backend/):
    python scripts/reset_user_data.py                 # dry run
    python scripts/reset_user_data.py --apply         # do it
    python scripts/reset_user_data.py --keep admin,teacher --apply
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402

# Order matters: children before parents. (table, WHERE clause or None for all rows)
STEPS = [
    ("attempt_answers", None),
    ("quiz_attempts", None),
    ("answer_events", None),
    ("concept_reviews", None),
    ("daily_sessions", None),
    ("study_sessions", None),
    ("weekly_mock_entries", None),
    ("weekly_mocks", "part NOT IN ('p1', 'p2')"),   # personal timed papers; shared weekly papers stay
    ("duel_entries", None),
    ("duels", None),
    ("chat_messages", None),
    ("chat_conversations", None),
    ("mcq_bookmarks", None),
    ("concept_bookmarks", None),
    ("notes", None),
    ("flashcards", None),
    ("saved_sheets", None),
    ("answer_reports", None),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    parser.add_argument("--keep", default="admin", help="comma-separated usernames to keep")
    args = parser.parse_args()
    keep = [u.strip() for u in args.keep.split(",") if u.strip()]

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        exists = lambda t: conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{t}"}).scalar()  # noqa: E731
        kept = conn.execute(text("SELECT id, username FROM users WHERE username = ANY(:k)"), {"k": keep}).all()
        if not kept:
            raise SystemExit(f"None of the accounts to keep exist ({keep}); refusing to delete every user.")
        print(f"{'APPLY' if args.apply else 'DRY RUN'} on {engine.url.database}; keeping users: {[u for _, u in kept]}")
        for table, where in STEPS:
            if not exists(table):
                continue
            clause = f" WHERE {where}" if where else ""
            n = conn.execute(text(f"SELECT count(*) FROM {table}{clause}")).scalar()
            if args.apply and n:
                conn.execute(text(f"DELETE FROM {table}{clause}"))
            print(f"  {table:22} {n:7} {'deleted' if args.apply else 'to delete'}")
        others = conn.execute(text("SELECT count(*) FROM users WHERE NOT (username = ANY(:k))"), {"k": keep}).scalar()
        if args.apply:
            conn.execute(text("DELETE FROM users WHERE NOT (username = ANY(:k))"), {"k": keep})
            conn.execute(text("UPDATE users SET exam_date = NULL, streak_freezes = DEFAULT WHERE username = ANY(:k)"),
                         {"k": keep})
        print(f"  {'users':22} {others:7} {'deleted' if args.apply else 'to delete'} (kept accounts: exam date and "
              f"streak freezes {'reset' if args.apply else 'to reset'})")
        if not args.apply:
            print("Dry run only. Re-run with --apply to delete.")


if __name__ == "__main__":
    main()
