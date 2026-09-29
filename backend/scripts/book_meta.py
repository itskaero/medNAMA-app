"""Give every book its real name, edition and printed page numbers.

Step 1, propose (PC, needs the PDFs and the AI key). Reads each ready book's title and copyright pages
and its printed page numbers, and writes a proposal file you can read and edit:

    python scripts/book_meta.py propose                      # -> ../data/book_meta.json
    python scripts/book_meta.py propose --book-id 44         # one book

Step 2, apply the (edited) file. Renames the book everywhere its name is stored as text: the
"Textbook: <title> | ..." line inside its passages, concept cards, MCQ explanations and cached revision
sheets. The old name is kept in books.aliases so citations saved under it still open the page viewer.
Passage embeddings are not recomputed (the title is a few tokens of a 150-word passage).

    python scripts/book_meta.py apply                        # dry run: prints what would change
    python scripts/book_meta.py apply --apply

The file matches books by file name, so the same file applies to the NAS database (copy it over and run
`apply --apply` inside the backend container there; no PDFs or AI needed).
"""

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.book_meta import extract_metadata, page_labels  # noqa: E402
from app.book_pages import pdf_path  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Book  # noqa: E402

DEFAULT_FILE = BACKEND_DIR.parent / "data" / "book_meta.json"
FIELDS = ("full_title", "authors", "edition", "year", "publisher", "isbn", "subject")


def propose(args) -> None:
    db = SessionLocal()
    out: dict = {}
    if args.file.exists():
        out = json.loads(args.file.read_text(encoding="utf-8"))
    try:
        q = db.query(Book).filter(Book.status == "ready")
        if args.book_id:
            q = q.filter(Book.id == args.book_id)
        for b in q.order_by(Book.id):
            path = pdf_path(b.filename)
            if not path:
                print(f"- {b.title}: PDF {b.filename} not found, skipped")
                continue
            print(f"- {b.title} ({b.filename})")
            meta = extract_metadata(path)
            labels, src = page_labels(path)
            entry = {
                "current_title": b.title,
                "title": meta.get("short_title") or b.title,
                **{k: meta.get(k) for k in FIELDS},
                "page_labels_source": src,
                "page_labels": labels,
                "warnings": meta.get("warnings", []) + ([meta["error"]] if meta.get("error") else []),
            }
            out[b.filename] = entry
            print(f"    -> {entry['title']!r} | {entry['full_title']!r} | {entry['authors']} | ed {entry['edition']}"
                  f" | {entry['year']} | ISBN {entry['isbn']} | subject {entry['subject']}")
            print(f"       printed pages: {src}")
            for w in entry["warnings"]:
                print(f"       ! {w}")
    finally:
        db.close()
    args.file.parent.mkdir(parents=True, exist_ok=True)
    args.file.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {args.file}. Check the titles, edit anything wrong, then: python scripts/book_meta.py apply")


def _cite_re(old: str) -> re.Pattern:
    """The old title where it is used as a citation: "Source: X, Page 12", "[X, Page 12]", "(X, p. 12)".
    Not preceded by another word, so "Oxford Microbiology, Ed. 2" (a different book) is left alone."""
    return re.compile(r"(?<![A-Za-z’'] )(?<![A-Za-z’'])" + re.escape(old) + r"(?=,? (?:Page|Pg\.?|p\.|pp\.) ?\d)")


def _retitle_citations(value, old: str, new: str):
    """JSON citations: swap book_title where it is exactly the old title."""
    if isinstance(value, list):
        return [_retitle_citations(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: (new if k == "book_title" and v == old else _retitle_citations(v, old, new))
                for k, v in value.items()}
    return value


def _rename(db, book: Book, old: str, new: str, apply: bool) -> None:
    """Replace the old title with the new one wherever it is stored as a citation."""
    if not old or old == new:
        return
    old_prefix, new_prefix = f"Textbook: {old} |", f"Textbook: {new} |"
    like = old_prefix.replace("%", r"\%").replace("_", r"\_") + "%"
    n = db.execute(text("SELECT count(*) FROM chunks WHERE book_id = :b AND content LIKE :l"),
                   {"b": book.id, "l": like}).scalar()
    if apply:
        db.execute(text("UPDATE chunks SET content = :np || substr(content, length(:op) + 1) "
                        "WHERE book_id = :b AND content LIKE :l"),
                   {"b": book.id, "l": like, "op": old_prefix, "np": new_prefix})
    print(f"    {'passages':17} {n:>7} row(s)")

    n = db.execute(text("SELECT count(*) FROM concept_cards WHERE book_title = :o"), {"o": old}).scalar()
    if apply:
        db.execute(text("UPDATE concept_cards SET book_title = :n WHERE book_title = :o"), {"o": old, "n": new})
    print(f"    {'concept cards':17} {n:>7} row(s)")

    cite = _cite_re(old)
    for label, table, md_col, json_col in (("MCQ explanations", "mcqs", "explanation_markdown", "explanation_citations"),
                                           ("revision sheets", "topic_summaries", "markdown", "citations")):
        rows = db.execute(text(f"SELECT id, {md_col}, {json_col} FROM {table} "
                               f"WHERE {md_col} LIKE :l OR {json_col}::text LIKE :l"), {"l": f"%{old}%"}).fetchall()
        changed = 0
        for rid, md, cj in rows:
            new_md = cite.sub(new, md) if md else md
            new_cj = _retitle_citations(cj, old, new) if cj else cj
            if new_md != md or new_cj != cj:
                changed += 1
                if apply:
                    db.execute(text(f"UPDATE {table} SET {md_col} = :m, {json_col} = CAST(:j AS jsonb) WHERE id = :i"),
                               {"m": new_md, "j": json.dumps(new_cj) if new_cj is not None else None, "i": rid})
        print(f"    {label:17} {changed:>7} row(s)")


def apply_file(args) -> None:
    if not args.file.exists():
        sys.exit(f"{args.file} not found. Run `propose` first (or copy it here).")
    data = json.loads(args.file.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        for filename, e in data.items():
            book = db.query(Book).filter(Book.filename == filename).first()
            if not book:
                print(f"- {filename}: not in this database, skipped")
                continue
            new_title = (e.get("title") or book.title).strip()
            print(f"- {book.title!r} -> {new_title!r}")
            for k in FIELDS:
                if e.get(k) != getattr(book, k):
                    print(f"    {k:10} {getattr(book, k)!r} -> {e.get(k)!r}")
            labels = e.get("page_labels")
            print(f"    printed pages: {e.get('page_labels_source')}")
            _rename(db, book, book.title, new_title, args.apply)
            if args.apply:
                for k in FIELDS:
                    setattr(book, k, e.get(k))
                if labels is not None and (not book.total_pages or len(labels) == book.total_pages):
                    book.page_labels = labels
                if new_title != book.title:
                    book.aliases = sorted(set((book.aliases or []) + [book.title]))
                    book.title = new_title
                book.meta_source = "book_meta.py"
                db.commit()
        if not args.apply:
            print("\nDry run. Re-run with --apply to write these changes.")
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["propose", "apply"])
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--book-id", type=int)
    ap.add_argument("--apply", action="store_true", help="write the changes (apply only)")
    args = ap.parse_args()
    propose(args) if args.command == "propose" else apply_file(args)


if __name__ == "__main__":
    main()
