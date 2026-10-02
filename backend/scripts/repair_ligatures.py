"""Repair words whose fi/fl/ff/ffi/ffl ligatures were lost during PDF extraction.

Bailey & Love's PDF encodes these ligatures as single glyphs that the extractor
turned into a bare "f": "fluid" -> "fuid", "deficit" -> "defcit", "affect" ->
"afect", "sufficient" -> "sufcient". ~2,600 of its passages are affected, so
keyword search for "fluid" never matches them and the embeddings are noisier.

How the fix is learned (no hand-written word list):
  1. Build a vocabulary with frequencies from every book that is NOT damaged.
  2. For each word in the target book that is not in that vocabulary, try
     replacing each "f" with fi/fl/ff/ffi/ffl. If exactly the repaired spelling
     is a known word (seen >= --min-freq times), record the mapping.
  3. Report the mappings (dry run) or apply them to parent and child chunks and
     re-embed the changed child chunks (--apply).

Usage (from backend/):
    python scripts/repair_ligatures.py --book-id 44                # dry run, prints mappings
    python scripts/repair_ligatures.py --book-id 44 --apply        # writes changes + re-embeds

Take a database backup first (scripts/export_db.py) before --apply. Re-embedding
runs on CPU with the same bge-large model as ingestion; expect several minutes.
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

WORD = re.compile(r"[A-Za-z]+")
LIGATURES = ("fi", "fl", "ff", "ffi", "ffl")


def vocabulary(session, exclude_book: int) -> Counter:
    vocab: Counter = Counter()
    rows = session.execute(
        text("SELECT content FROM chunks WHERE parent_id IS NULL AND book_id <> :b"), {"b": exclude_book}
    )
    for (content,) in rows:
        vocab.update(w.lower() for w in WORD.findall(content))
    return vocab


def learn_mappings(session, book_id: int, vocab: Counter, min_freq: int) -> dict[str, str]:
    target: Counter = Counter()
    rows = session.execute(text("SELECT content FROM chunks WHERE parent_id IS NULL AND book_id = :b"), {"b": book_id})
    for (content,) in rows:
        target.update(w.lower() for w in WORD.findall(content))

    mapping: dict[str, str] = {}
    for word, _ in target.items():
        if "f" not in word or vocab[word] >= min_freq:
            continue
        candidates = set()
        for i, ch in enumerate(word):
            if ch != "f":
                continue
            for lig in LIGATURES:
                fixed = word[:i] + lig + word[i + 1:]
                if vocab[fixed] >= min_freq:
                    candidates.add(fixed)
        if len(candidates) == 1:
            mapping[word] = candidates.pop()
        elif candidates:
            best = max(candidates, key=lambda c: vocab[c])
            if vocab[best] >= 3 * sorted((vocab[c] for c in candidates), reverse=True)[1]:
                mapping[word] = best
    return mapping


def apply_case(original: str, fixed_lower: str) -> str:
    if original.isupper():
        return fixed_lower.upper()
    if original[0].isupper():
        return fixed_lower[0].upper() + fixed_lower[1:]
    return fixed_lower


def repair_text(content: str, mapping: dict[str, str]) -> str:
    return WORD.sub(lambda m: apply_case(m.group(0), mapping[m.group(0).lower()])
                    if m.group(0).lower() in mapping else m.group(0), content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--min-freq", type=int, default=3, help="min occurrences in other books for a repaired word")
    parser.add_argument("--apply", action="store_true", help="write changes and re-embed (default: dry run)")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        title = session.execute(text("SELECT title FROM books WHERE id = :b"), {"b": args.book_id}).scalar()
        if not title:
            raise SystemExit(f"No book with id {args.book_id}")
        print(f"Learning vocabulary from the other books (target: {title})...")
        vocab = vocabulary(session, args.book_id)
        mapping = learn_mappings(session, args.book_id, vocab, args.min_freq)
        print(f"{len(mapping)} damaged spellings found. Most common repairs:")
        target_counts: Counter = Counter()
        for (content,) in session.execute(
            text("SELECT content FROM chunks WHERE parent_id IS NULL AND book_id = :b"), {"b": args.book_id}
        ):
            target_counts.update(w.lower() for w in WORD.findall(content) if w.lower() in mapping)
        for word, n in target_counts.most_common(40):
            print(f"  {word:>18} -> {mapping[word]:<20} x{n}")

        if not args.apply:
            print("\nDry run only. Re-run with --apply to write the repairs and re-embed (back up the DB first).")
            return

        from app.ingestion import get_embedding_model

        parents = session.execute(
            text("SELECT id, content FROM chunks WHERE parent_id IS NULL AND book_id = :b"), {"b": args.book_id}
        ).fetchall()
        changed_parents = 0
        for pid, content in parents:
            fixed = repair_text(content, mapping)
            if fixed != content:
                session.execute(text("UPDATE chunks SET content = :c WHERE id = :i"), {"c": fixed, "i": pid})
                changed_parents += 1

        children = session.execute(
            text("SELECT id, content, extra_metadata FROM chunks WHERE parent_id IS NOT NULL AND book_id = :b"),
            {"b": args.book_id},
        ).fetchall()
        to_embed = []
        for cid, content, meta in children:
            fixed = repair_text(content, mapping)
            if fixed == content:
                continue
            meta = dict(meta or {})
            if meta.get("original_text"):
                meta["original_text"] = repair_text(meta["original_text"], mapping)
            to_embed.append((cid, fixed, meta))
        print(f"Updating {changed_parents} parent and {len(to_embed)} child chunks; re-embedding children...")

        import json

        model = get_embedding_model()
        for start in range(0, len(to_embed), 64):
            batch = to_embed[start:start + 64]
            vecs = model.encode([c for _, c, _ in batch], normalize_embeddings=True, batch_size=32)
            for (cid, content, meta), vec in zip(batch, vecs):
                session.execute(
                    text("UPDATE chunks SET content = :c, extra_metadata = CAST(:m AS jsonb), "
                         "embedding = CAST(:e AS vector) WHERE id = :i"),
                    {"c": content, "m": json.dumps(meta), "e": str(vec.tolist()), "i": cid},
                )
            session.commit()
            print(f"  {min(start + 64, len(to_embed))}/{len(to_embed)} children re-embedded", flush=True)
        session.commit()
        print("Done.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
