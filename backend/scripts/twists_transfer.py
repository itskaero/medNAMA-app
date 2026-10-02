"""Move Past-paper Twists from one medNAMA to another (write them on the fast PC, serve them from the NAS).

Row ids differ between the two databases (the same MediVerse question can be row 52288 on the PC and 52342
on the NAS), so a twist travels with its seed's identity instead: (source, source_ref), or a hash of the
seed's stem for bank rows without a source_ref. The twist's embedding travels too, so importing needs no model
(numpy-free, fine on the NAS). Textbook references are carried as the book title and page in the explanation;
the book is matched by title on import.

    python scripts/twists_transfer.py export --out ../mcqs/twists.jsonl          # PC
    python scripts/twists_transfer.py import --file /app/mcqs/twists.jsonl [--dry-run]   # NAS (in the container)

Import skips seeds that already have twists on the target (twists written there on demand win), so it is safe
to repeat as the PC writes more.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import MCQ, Book, MCQTag  # noqa: E402

FIELDS = ("question_text", "options", "correct_option", "main_category", "sub_category", "topic", "tested_concept",
          "explanation_markdown", "status", "access", "grounding", "difficulty")


def stem_hash(stem: str) -> str:
    return "sha1:" + hashlib.sha1(" ".join((stem or "").lower().split()).encode()).hexdigest()


def seed_key(seed: MCQ) -> list[str]:
    return [seed.source or "", seed.source_ref or stem_hash(seed.question_text)]


def export(out: str) -> None:
    db = SessionLocal()
    twists = db.query(MCQ).filter(MCQ.twist_of.isnot(None)).order_by(MCQ.twist_of, MCQ.id).all()
    seeds = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_({t.twist_of for t in twists} or {-1}))}
    tags: dict[int, list[list[str]]] = {}
    for mid, axis, label in db.query(MCQTag.mcq_id, MCQTag.axis, MCQTag.label).filter(
            MCQTag.mcq_id.in_([t.id for t in twists] or [-1]), MCQTag.axis.in_(("twist", "referee"))):
        tags.setdefault(mid, []).append([axis, label])
    books = {b.id: b.title for b in db.query(Book.id, Book.title)}
    vecs = dict(db.execute(text("SELECT id, stem_embedding::text FROM mcqs WHERE twist_of IS NOT NULL "
                                "AND stem_embedding IS NOT NULL")).all())
    with open(out, "w", encoding="utf-8") as f:
        for t in twists:
            row = {k: getattr(t, k) for k in FIELDS}
            row.update(seed=seed_key(seeds[t.twist_of]), tags=tags.get(t.id, []), book=books.get(t.book_id),
                       embedding=json.loads(vecs[t.id]) if t.id in vecs else None)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"exported {len(twists)} twists of {len(seeds)} questions to {out}")


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
    has_twists = {i for (i,) in db.execute(text("SELECT DISTINCT twist_of FROM mcqs WHERE twist_of IS NOT NULL"))}
    books = {title: i for i, title in db.query(Book.id, Book.title)}
    added = missing = skipped = 0
    for r in rows:
        seed_id = by_ref.get(tuple(r["seed"]))
        if seed_id is None:
            missing += 1
            continue
        if seed_id in has_twists:
            skipped += 1
            continue
        added += 1
        if dry_run:
            continue
        mcq = MCQ(**{k: r[k] for k in FIELDS}, twist_of=seed_id, book_id=books.get(r.get("book")),
                  stem_embedding=r.get("embedding"), source_chunk_ids=[])
        db.add(mcq)
        db.flush()
        for axis, label in r.get("tags", []):
            db.add(MCQTag(mcq_id=mcq.id, axis=axis, label=label))
    # Seeds count as done only after all their twists are in (several rows share a seed).
    if not dry_run:
        db.commit()
    print(f"{len(rows)} twists in file: {added} {'would be ' if dry_run else ''}added, {skipped} skipped "
          f"(question already has twists here), {missing} whose question is not in this database")


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
