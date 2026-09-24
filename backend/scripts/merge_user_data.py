"""Copy user-created data from another database into the current one.

Use case: the NAS runs database A (with MCQs, chats and quiz attempts created
by its users) and is switched to database B restored from the PC. This copies
A's user data into B so nothing is lost:

  users              matched by username; a missing user is created, and an
                     existing user's password hash + role are taken from the
                     source so NAS logins keep working
  mcqs               copied unless the same (quiz_set_id, question_text) already
                     exists; embeddings are left empty (backfilled lazily)
  chat_conversations / chat_messages, quiz_attempts / attempt_answers,
  mcq_bookmarks, concept_bookmarks, notes, flashcards
                     copied with ids remapped

The target is the database in DATABASE_URL; the source is the same server with
another database name. Dry run by default.

Usage (inside the backend container, from /app/backend):
    python scripts/merge_user_data.py --source-db medrag_new
    python scripts/merge_user_data.py --source-db medrag_new --apply
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import settings  # noqa: E402

MCQ_SKIP_COLUMNS = {"id", "stem_embedding"}


def columns(conn, table: str) -> list[str]:
    return [r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t ORDER BY ordinal_position"
    ), {"t": table})]


def table_exists(conn, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def copy_rows(src, dst, table: str, remap: dict[str, dict[int, int]], fk: dict[str, str],
              skip: set[str], apply: bool) -> dict[int, int]:
    """Copy all rows of `table`; fk maps column -> remap key. Returns old id -> new id."""
    if not (table_exists(src, table) and table_exists(dst, table)):
        return {}
    shared = [c for c in columns(src, table) if c in set(columns(dst, table)) and c not in skip | {"id"}]
    rows = src.execute(text(f"SELECT id, {', '.join(shared)} FROM {table} ORDER BY id")).mappings().all()
    mapping: dict[int, int] = {}
    skipped = 0
    for row in rows:
        values = {c: row[c] for c in shared}
        missing_ref = False
        for col, key in fk.items():
            if values.get(col) is not None:
                new = remap[key].get(values[col])
                if new is None:
                    missing_ref = True
                    break
                values[col] = new
        if missing_ref:
            skipped += 1
            continue
        if apply:
            cols = ", ".join(values)
            params = ", ".join(f":{c}" for c in values)
            mapping[row["id"]] = dst.execute(
                text(f"INSERT INTO {table} ({cols}) VALUES ({params}) RETURNING id"), values
            ).scalar()
        else:
            mapping[row["id"]] = -row["id"]
    print(f"  {table:20} {len(mapping):5} copied" + (f", {skipped} skipped (missing parent)" if skipped else ""))
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    target_url = make_url(settings.database_url)
    source_url = target_url.set(database=args.source_db)
    if source_url.database == target_url.database:
        raise SystemExit("Source and target are the same database.")
    print(f"Source: {source_url.database}  ->  Target: {target_url.database}  ({'APPLY' if args.apply else 'dry run'})")

    src_engine, dst_engine = create_engine(source_url), create_engine(target_url)
    with src_engine.connect() as src, dst_engine.begin() as dst:
        remap: dict[str, dict[int, int]] = {"users": {}, "mcqs": {}}

        # Users by username (keep the source's password so existing logins still work)
        dst_users = {u: i for i, u in dst.execute(text("SELECT id, username FROM users"))}
        created = updated = 0
        for row in src.execute(text("SELECT id, username, password_hash, role FROM users")).mappings():
            if row["username"] in dst_users:
                remap["users"][row["id"]] = dst_users[row["username"]]
                if args.apply:
                    dst.execute(text("UPDATE users SET password_hash = :p, role = :r WHERE id = :i"),
                                {"p": row["password_hash"], "r": row["role"], "i": dst_users[row["username"]]})
                updated += 1
            else:
                new_id = dst.execute(text(
                    "INSERT INTO users (username, password_hash, role) VALUES (:u, :p, :r) RETURNING id"
                ), dict(u=row["username"], p=row["password_hash"], r=row["role"])).scalar() if args.apply else -row["id"]
                remap["users"][row["id"]] = new_id
                created += 1
        print(f"  {'users':20} {updated:5} matched (password kept from source), {created} created")

        # MCQs: skip ones the target already has
        existing = {(q, t): i for i, q, t in dst.execute(text("SELECT id, quiz_set_id, question_text FROM mcqs"))}
        mcq_cols = [c for c in columns(src, "mcqs") if c in set(columns(dst, "mcqs")) and c not in MCQ_SKIP_COLUMNS]
        new_count = dup_count = 0
        for row in src.execute(text(f"SELECT id, {', '.join(mcq_cols)} FROM mcqs ORDER BY id")).mappings():
            key = (row["quiz_set_id"], row["question_text"])
            if key in existing:
                remap["mcqs"][row["id"]] = existing[key]
                dup_count += 1
                continue
            values = {c: row[c] for c in mcq_cols}
            if args.apply:
                from sqlalchemy.dialects.postgresql import JSONB
                from sqlalchemy import bindparam

                stmt = text(
                    f"INSERT INTO mcqs ({', '.join(values)}) VALUES ({', '.join(':' + c for c in values)}) RETURNING id"
                ).bindparams(*[bindparam(c, type_=JSONB) for c in values
                               if c in ("options", "explanation_citations", "explanation_figures", "source_chunk_ids")])
                new_id = dst.execute(stmt, values).scalar()
            else:
                new_id = -row["id"]
            remap["mcqs"][row["id"]] = new_id
            existing[key] = new_id
            new_count += 1
        print(f"  {'mcqs':20} {new_count:5} copied, {dup_count} already present")

        conv = copy_rows(src, dst, "chat_conversations", remap, {"user_id": "users"}, set(), args.apply)
        remap["chat_conversations"] = conv
        copy_rows(src, dst, "chat_messages", remap, {"conversation_id": "chat_conversations"}, set(), args.apply)
        attempts = copy_rows(src, dst, "quiz_attempts", remap, {"user_id": "users"}, set(), args.apply)
        remap["quiz_attempts"] = attempts
        copy_rows(src, dst, "attempt_answers", remap, {"quiz_attempt_id": "quiz_attempts", "mcq_id": "mcqs"}, set(), args.apply)
        copy_rows(src, dst, "mcq_bookmarks", remap, {"user_id": "users", "mcq_id": "mcqs"}, set(), args.apply)
        for table in ("concept_bookmarks", "notes", "flashcards"):
            copy_rows(src, dst, table, remap, {"user_id": "users"}, set(), args.apply)

        if not args.apply:
            dst.rollback()
            print("\nDry run only - nothing written. Re-run with --apply.")
        else:
            print("\nCommitted.")


if __name__ == "__main__":
    main()
