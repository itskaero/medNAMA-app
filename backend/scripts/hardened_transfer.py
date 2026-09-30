"""Move harder versions (Hardened MCQs) from one medNAMA to another: write the stock on the fast PC
(scripts/prewarm_harder.py), serve it from the NAS.

Row ids differ between the two databases, so each harder version travels with its original's identity
((source, source_ref), or a hash of the stem; see scripts/twists_transfer.py) and its stem embedding (so importing
needs no model). The book is matched by title. Import skips a version whose statement the target already has, so
it is safe to repeat.

    python scripts/hardened_transfer.py export --out ../mcqs/hardened.jsonl                  # PC
    python scripts/hardened_transfer.py import --file /app/mcqs/hardened.jsonl [--dry-run]    # NAS (in the container)
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402
from twists_transfer import seed_key, stem_hash  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.hardening import CATEGORY  # noqa: E402
from app.models import MCQ, Book, MCQTag  # noqa: E402

FIELDS = ("quiz_set_id", "quiz_set_title", "question_text", "options", "correct_option", "main_category",
          "sub_category", "topic", "tested_concept", "explanation_markdown", "status", "access", "grounding",
          "difficulty")


def export(out: str) -> None:
    db = SessionLocal()
    rows = (db.query(MCQ, MCQTag.label).join(MCQTag, MCQTag.mcq_id == MCQ.id)
            .filter(MCQTag.axis == "hardened", MCQ.main_category == CATEGORY, MCQ.status == "ready").all())
    seeds = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_({int(sid) for _, sid in rows} or {-1}))}
    books = {b.id: b.title for b in db.query(Book.id, Book.title)}
    vecs = dict(db.execute(text("SELECT id, stem_embedding::text FROM mcqs WHERE main_category = :c "
                                "AND stem_embedding IS NOT NULL"), {"c": CATEGORY}).all())
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for m, sid in rows:
            seed = seeds.get(int(sid))
            if seed is None:
                continue
            row = {k: getattr(m, k) for k in FIELDS}
            row.update(seed=seed_key(seed), book=books.get(m.book_id),
                       embedding=json.loads(vecs[m.id]) if m.id in vecs else None)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    print(f"exported {n} harder versions of {len(seeds)} questions to {out}")


def import_(path: str, dry_run: bool) -> None:
    db = SessionLocal()
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    by_ref = {(s or "", r): i for i, s, r in db.execute(text(
        "SELECT id, source, source_ref FROM mcqs WHERE source_ref IS NOT NULL AND twist_of IS NULL"))}
    need_hash = {tuple(r["seed"]) for r in rows if r["seed"][1].startswith("sha1:")}
    if need_hash:
        sources = sorted({s for s, _ in need_hash})
        for i, s, q in db.execute(text("SELECT id, source, question_text FROM mcqs WHERE source_ref IS NULL "
                                       "AND twist_of IS NULL AND coalesce(source, '') = ANY(:s)"), {"s": sources}):
            by_ref.setdefault((s or "", stem_hash(q)), i)
    have = {stem_hash(q) for (q,) in db.execute(text("SELECT question_text FROM mcqs WHERE main_category = :c"),
                                                 {"c": CATEGORY})}
    books = {title: i for i, title in db.query(Book.id, Book.title)}
    added = missing = skipped = 0
    for r in rows:
        seed_id = by_ref.get(tuple(r["seed"]))
        if seed_id is None:
            missing += 1
            continue
        if stem_hash(r["question_text"]) in have:
            skipped += 1
            continue
        added += 1
        have.add(stem_hash(r["question_text"]))
        if dry_run:
            continue
        mcq = MCQ(**{k: r[k] for k in FIELDS}, book_id=books.get(r.get("book")), stem_embedding=r.get("embedding"),
                  source_chunk_ids=[])
        db.add(mcq)
        db.flush()
        db.add(MCQTag(mcq_id=mcq.id, axis="hardened", label=str(seed_id)))
    if not dry_run:
        db.commit()
    print(f"{len(rows)} harder versions in file: {added} {'would be ' if dry_run else ''}added, {skipped} already "
          f"here, {missing} whose original is not in this database")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    i = sub.add_parser("import")
    i.add_argument("--file", required=True)
    i.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.cmd == "export":
        export(args.out)
    else:
        import_(args.file, args.dry_run)


if __name__ == "__main__":
    main()
