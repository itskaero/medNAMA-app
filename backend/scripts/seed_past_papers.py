"""Import a past-paper archive export (mcqs/past-paper/*.jsonl) into the MCQ bank + Past Papers.

Reads the JSONL files produced by radiant-reader's export_seed.py (NOT its .sql files: those
assume options stored as an array and grading by answer text, while medNAMA stores options
as {A..E} and grades by letter):

  pool.jsonl             one row per unique question   -> mcqs (source 'pastpaper:<source>')
  papers.jsonl           one row per exam year          -> past_papers
  paper_questions.jsonl  question in a paper, ordered   -> past_paper_questions
  tags.jsonl             subject / topic / specialty    -> mcq_tags (every label kept)
  inline_media.jsonl     base64 images cut from explanations -> mcq_media (role 'explanation')
  pool.media_url         question images                -> mcq_media (role 'question'), downloaded once

Mapping:
  options (array) -> {"A": .., "B": ..}; correct_index -> correct_option letter
  explanation (HTML) + wrong_explanation ("(a) False - ...") -> one markdown explanation with
    "Why the other options are wrong"; option order is kept, since those notes name options by letter
  main_category 'Past papers - <exam>'; sub_category = a standard subject for readiness
    (e.g. Special/General Pathology -> Pathology, anatomy topics -> Anatomy); topic = topic label
  access 'restricted' (personal study material: PAST_PAPERS_ACCESS decides who sees it)
  stems that mention a figure the archive lacks get tag flag=figure-missing (skipped by timed papers)

A question already in the bank (same stem and answer, e.g. from mcqs/p1) is linked to its papers
and tags instead of being inserted again; it gains this explanation if it had none. Re-running
updates rows in place (keyed on source + source_ref).

Usage (from backend/):
    python scripts/seed_past_papers.py --dir ../mcqs/past-paper --dry-run
    python scripts/seed_past_papers.py --dir ../mcqs/past-paper                 # FCPS-1 only (default)
    python scripts/seed_past_papers.py --dir ../mcqs/past-paper --exams all     # + FCPS-2, IMM, Cardiology
    python scripts/seed_past_papers.py --embed                                  # backfill stem embeddings
"""

import argparse
import base64
import collections
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import MCQ, MCQMedia, MCQTag, PastPaper, PastPaperQuestion  # noqa: E402
from seed_mcqs import html_to_md, norm  # noqa: E402

LETTERS = "ABCDE"
EXAM_NAMES = {
    "FCPS-1": "FCPS Part 1",
    "Medicine (FCPS-2)": "FCPS Part 2 · Medicine",
    "Gynae & Obs (FCPS-2)": "FCPS Part 2 · Gynae & Obs",
    "Cardiology": "FCPS Part 2 · Cardiology",
    "Medicine (IMM)": "IMM · Medicine",
    "Gynae & Obs (IMM)": "IMM · Gynae & Obs",
}
TOPIC_SUBJECT = {
    "Pharmacology": "Pharmacology", "Biochemistry": "Biochemistry", "Microbiology": "Microbiology",
    "Immunology": "Microbiology", "Public Health Sciences": "Community Medicine",
    "Head and Neck Anatomy": "Anatomy", "Abdomen": "Anatomy", "Upper Limb": "Anatomy", "Thorax": "Anatomy",
    "Lower Limb": "Anatomy", "Pelvis and Perineum": "Anatomy", "Back and Vertebral Column": "Anatomy",
    "General Anatomy": "Anatomy", "Embryology": "Anatomy", "Histology": "Anatomy",
}
SUBJECT_SUBJECT = {"General Pathology": "Pathology", "Special Pathology": "Pathology", "Physiology": "Physiology",
                   "Anatomy": "Anatomy", "Neurology and Special Senses": "Neurology & Special Senses"}
FIGURE_REF = re.compile(r"\(figure\)|\bshown (?:below|here|in the (?:figure|image))|\bimage (?:below|shown)|"
                        r"\bphotograph\b|\bX-?ray (?:is )?shown|\bECG (?:is )?shown", re.I)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def standard_subject(exam_cat: str, labels: dict[str, list[str]]) -> str | None:
    if exam_cat == "FCPS-1":
        for t in labels.get("topic", []):
            if t in TOPIC_SUBJECT:
                return TOPIC_SUBJECT[t]
        for s in labels.get("subject", []):
            return SUBJECT_SUBJECT.get(s, s)
        return None
    return (labels.get("subject") or [None])[0]


def explanation_md(row: dict) -> str:
    right = html_to_md(row.get("explanation_markdown") or "")
    wrong = html_to_md(row.get("wrong_explanation") or "")
    parts = []
    if right:
        parts.append(right)
    if wrong:
        parts.append("**Why the other options are wrong**\n\n" + wrong)
    return "\n\n".join(parts)


def download(url: str) -> tuple[bytes, str] | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "medNAMA-importer"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            mime = r.headers.get_content_type() or "image/jpeg"
        return (data, mime) if data and mime.startswith("image/") else None
    except Exception as e:
        print(f"   ! could not download {url[:80]}: {e}")
        return None


def run(args) -> None:
    d = Path(args.dir).resolve()
    pool = read_jsonl(d / "pool.jsonl")
    papers = read_jsonl(d / "papers.jsonl")
    placements = read_jsonl(d / "paper_questions.jsonl")
    tags = read_jsonl(d / "tags.jsonl")
    inline = read_jsonl(d / "inline_media.jsonl") if (d / "inline_media.jsonl").exists() else []
    source = f"pastpaper:{papers[0].get('source', 'archive') if papers else 'archive'}"
    wanted = set(EXAM_NAMES) if args.exams == "all" else {e.strip() for e in args.exams.split(",")}

    labels: dict[str, dict[str, list[str]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for t in tags:
        if t["axis"] in ("subject", "topic", "specialty"):
            labels[t["source_id"]][t["axis"]].append(t["label"])
    for sid in labels:
        for axis in labels[sid]:
            labels[sid][axis] = sorted(set(labels[sid][axis]))

    rows = [r for r in pool if r.get("main_category") in wanted]
    problems = collections.Counter()
    good = []
    for r in rows:
        opts = [str(o).strip() for o in (r.get("options") or [])]
        idx = r.get("correct_index")
        if not r.get("question_text") or not (2 <= len(opts) <= 5) or not isinstance(idx, int) or not (0 <= idx < len(opts)):
            problems["bad options/answer"] += 1
            continue
        good.append(r)
    wanted_ids = {r["source_id"] for r in good}
    paper_by_code = {p["code"]: p for p in papers if p.get("category") in wanted}
    kept_placements = [pl for pl in placements if pl["paper_code"] in paper_by_code and pl["source_id"] in wanted_ids]

    db = SessionLocal()
    existing_ref = {ref: mid for mid, ref in db.execute(
        text("SELECT id, source_ref FROM mcqs WHERE source = :s AND source_ref IS NOT NULL"), {"s": source})}
    existing_key = {}
    for mid, q, opts, c in db.execute(text("SELECT id, question_text, options, correct_option FROM mcqs "
                                           "WHERE source IS DISTINCT FROM :s"), {"s": source}):
        existing_key.setdefault((norm(q), norm(str((opts or {}).get(c, "")))), mid)

    to_insert, to_update, linked = [], [], {}
    for r in good:
        opts = [str(o).strip() for o in r["options"]]
        key = (norm(r["question_text"]), norm(opts[r["correct_index"]]))
        if r["source_id"] in existing_ref:
            to_update.append(r)
        elif key in existing_key:
            linked[r["source_id"]] = existing_key[key]
        else:
            to_insert.append(r)

    by_exam = collections.Counter(EXAM_NAMES.get(r["main_category"], r["main_category"]) for r in good)
    years = collections.Counter((EXAM_NAMES.get(paper_by_code[pl["paper_code"]]["category"]), paper_by_code[pl["paper_code"]]["year"])
                                for pl in kept_placements)
    print(f"source {source}; exams {sorted(wanted)}")
    print(f"questions: {len(good)} usable ({dict(problems) or 'no problems'}); new {len(to_insert)}, "
          f"update {len(to_update)}, already in bank (link only) {len(linked)}")
    print("by exam:", dict(by_exam))
    print("placements by exam/year:", {f"{e} {y}": n for (e, y), n in sorted(years.items(), key=lambda kv: (kv[0][0] or '', kv[0][1] or 0))})
    if args.dry_run:
        return

    def fields(r: dict) -> dict:
        opts = [str(o).strip() for o in r["options"]]
        lab = labels.get(r["source_id"], {})
        stem = r["question_text"].replace("\r\n", "\n").strip()
        if FIGURE_REF.search(stem) and not r.get("media_url"):
            stem += "\n\n[Figure not available for this question]"
        topic = (lab.get("topic") or lab.get("subject") or [r.get("topic") or r.get("sub_category") or ""])[0]
        return dict(
            question_text=stem, options={LETTERS[i]: o for i, o in enumerate(opts)},
            correct_option=LETTERS[r["correct_index"]],
            main_category=f"Past papers · {EXAM_NAMES.get(r['main_category'], r['main_category'])}",
            sub_category=standard_subject(r["main_category"], lab) or r.get("sub_category") or "Mixed",
            topic=(topic or "")[:200], tested_concept=(topic or "")[:200],
            explanation_markdown=explanation_md(r) or None, status="ready", access="restricted",
            source=source, source_ref=r["source_id"],
        )

    t0 = time.time()
    for i in range(0, len(to_insert), 1000):
        db.bulk_insert_mappings(MCQ, [fields(r) for r in to_insert[i:i + 1000]])
        db.commit()
        print(f"   inserted {min(i + 1000, len(to_insert))}/{len(to_insert)}", flush=True)
    for r in to_update:
        db.query(MCQ).filter(MCQ.id == existing_ref[r["source_id"]]).update(
            {k: v for k, v in fields(r).items() if k not in ("source", "source_ref")}, synchronize_session=False)
    db.commit()
    for sid, mid in linked.items():   # already in the bank from another import: borrow the explanation if missing
        r = next(x for x in good if x["source_id"] == sid)
        db.execute(text("UPDATE mcqs SET explanation_markdown = :e WHERE id = :i AND "
                        "(explanation_markdown IS NULL OR explanation_markdown = '')"),
                   {"e": explanation_md(r) or None, "i": mid})
    db.commit()

    mcq_of = {ref: mid for mid, ref in db.execute(
        text("SELECT id, source_ref FROM mcqs WHERE source = :s AND source_ref IS NOT NULL"), {"s": source})}
    mcq_of.update(linked)

    # papers (exam years that have questions in this import)
    used_codes = {pl["paper_code"] for pl in kept_placements}
    for code in used_codes:
        p = paper_by_code[code]
        exam = EXAM_NAMES.get(p["category"], p["category"])
        stmt = pg_insert(PastPaper).values(code=code, exam=exam, title=f"{exam} · {p.get('year') or p.get('title')}",
                                           year=p.get("year"), source=source, access="restricted")
        db.execute(stmt.on_conflict_do_update(index_elements=["code"],
                                              set_={"exam": exam, "title": stmt.excluded.title, "year": stmt.excluded.year}))
    db.commit()
    paper_id = {c: i for i, c in db.execute(text("SELECT id, code FROM past_papers"))}

    links = [{"paper_id": paper_id[pl["paper_code"]], "mcq_id": mcq_of[pl["source_id"]], "position": int(pl.get("position") or 0)}
             for pl in kept_placements if pl["source_id"] in mcq_of]
    for i in range(0, len(links), 5000):
        db.execute(pg_insert(PastPaperQuestion).values(links[i:i + 5000]).on_conflict_do_nothing())
    tag_rows = []
    for sid in wanted_ids:
        if sid not in mcq_of:
            continue
        for axis, labs in labels.get(sid, {}).items():
            tag_rows += [{"mcq_id": mcq_of[sid], "axis": axis, "label": lab} for lab in labs]
    fig_missing = [r for r in good if FIGURE_REF.search(r["question_text"]) and not r.get("media_url") and r["source_id"] in mcq_of]
    tag_rows += [{"mcq_id": mcq_of[r["source_id"]], "axis": "flag", "label": "figure-missing"} for r in fig_missing]
    for i in range(0, len(tag_rows), 5000):
        db.execute(pg_insert(MCQTag).values(tag_rows[i:i + 5000]).on_conflict_do_nothing())
    db.commit()
    print(f"   papers {len(used_codes)}, placements {len(links)}, tags {len(tag_rows)} "
          f"(figure-missing {len(fig_missing)})", flush=True)

    # images: explanation images from inline_media.jsonl, question images downloaded once
    media_added = 0
    for m in inline:
        mid = mcq_of.get(m["source_id"])
        if mid is None:
            continue
        stmt = pg_insert(MCQMedia).values(mcq_id=mid, role="explanation", mime=m.get("mime") or "image/png",
                                          data=base64.b64decode(m["base64"]), origin=f"inline:{m.get('field')}")
        res = db.execute(stmt.on_conflict_do_nothing().returning(MCQMedia.id))
        new_id = res.scalar()
        if new_id:
            db.execute(text("UPDATE mcqs SET explanation_markdown = coalesce(explanation_markdown, '') || :img WHERE id = :i"),
                       {"img": f"\n\n![Explanation figure](/api/mcq-media/{new_id})", "i": mid})
            media_added += 1
    db.commit()
    if not args.no_download:
        for r in good:
            url, mid = r.get("media_url"), mcq_of.get(r["source_id"])
            if not url or mid is None:
                continue
            if db.execute(text("SELECT 1 FROM mcq_media WHERE mcq_id = :m AND origin = :o"), {"m": mid, "o": url}).first():
                continue
            got = download(url)
            if got:
                db.add(MCQMedia(mcq_id=mid, role="question", mime=got[1], data=got[0], origin=url))
                db.commit()
                media_added += 1
    print(f"   images added {media_added}; done in {time.time() - t0:.0f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default="../mcqs/past-paper")
    parser.add_argument("--exams", default="FCPS-1", help="comma-separated archive categories, or 'all'")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-download", action="store_true", help="skip downloading question images")
    parser.add_argument("--embed", action="store_true", help="backfill stem embeddings for imported questions")
    args = parser.parse_args()
    if args.embed:
        from seed_mcqs import embed

        embed(like="pastpaper:%")
    else:
        run(args)


if __name__ == "__main__":
    main()
