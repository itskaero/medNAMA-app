"""Move textbook page references (app/textbook_refs.py) from one medNAMA to another: written on the PC
(scripts/prewarm_refs.py), served from the NAS.

Each row carries its question's identity ((source, source_ref) or a stem hash, as scripts/twists_transfer.py)
and only the added section and citations, which are appended to the target's own explanation. Questions the
target has already done are skipped; disputed keys travel as the refs=disputed tag plus a key-conflict flag.

    python scripts/refs_transfer.py export --out ../mcqs/refs.jsonl                   # PC
    python scripts/refs_transfer.py import --file /app/mcqs/refs.jsonl [--dry-run]    # NAS (in the container)
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
from app.models import MCQ, MCQTag  # noqa: E402
from app.textbook_refs import REF_MARK  # noqa: E402


def export(out: str) -> None:
    db = SessionLocal()
    rows = (db.query(MCQ, MCQTag.label).join(MCQTag, MCQTag.mcq_id == MCQ.id)
            .filter(MCQTag.axis == "refs", MCQTag.label.in_(("added", "disputed"))).all())
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for m, label in rows:
            section = ""
            if label == "added" and REF_MARK in (m.explanation_markdown or ""):
                section = REF_MARK + (m.explanation_markdown or "").split(REF_MARK, 1)[1]
            cites = [c for c in (m.explanation_citations or []) if isinstance(c, dict) and c.get("excerpt")
                     and c["excerpt"] in section]
            f.write(json.dumps({"seed": seed_key(m), "label": label, "section": section, "citations": cites},
                               ensure_ascii=False) + "\n")
            n += 1
    print(f"exported {n} referenced questions to {out}")


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
    done = {i for (i,) in db.query(MCQTag.mcq_id).filter(MCQTag.axis == "refs")}
    added = missing = skipped = 0
    for r in rows:
        mid = by_ref.get(tuple(r["seed"]))
        if mid is None:
            missing += 1
            continue
        if mid in done:
            skipped += 1
            continue
        added += 1
        if dry_run:
            continue
        m = db.get(MCQ, mid)
        if r["label"] == "added" and r["section"] and REF_MARK not in (m.explanation_markdown or ""):
            m.explanation_markdown = ((m.explanation_markdown or "").rstrip() + "\n\n" + r["section"].strip()).strip()
            m.explanation_citations = list(m.explanation_citations or []) + r["citations"]
        if r["label"] == "disputed" and not db.query(MCQTag).filter_by(mcq_id=mid, axis="flag", label="key-conflict").first():
            db.add(MCQTag(mcq_id=mid, axis="flag", label="key-conflict"))
        db.add(MCQTag(mcq_id=mid, axis="refs", label=r["label"]))
    if not dry_run:
        db.commit()
    print(f"{len(rows)} in file: {added} {'would be ' if dry_run else ''}applied, {skipped} already done here, "
          f"{missing} whose question is not in this database")


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
