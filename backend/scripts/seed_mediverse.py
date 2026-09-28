"""Import the MediVerse FCPS Part 1 archive (mcqs/mediverse/fcps1.jsonl) as past papers, next to Radiant.

The two archives recall the same exams in different words (and sometimes with a different key), so
nothing is merged: every MediVerse question is its own row, and scripts/link_recalls.py links rows of
either source that recall the same question.

  fcps1.jsonl row        -> mcqs (source 'pastpaper:mediverse', source_ref = MediVerse id, restricted)
  papers[] dated sitting -> past_papers ('Surg 18 Sep 2019 (M+E)' -> FCPS Part 1, 2019, Surgery sitting)
  papers[] collection    -> past_papers without a year ('FCPS Old Pool', 'MediVerse Eye Special')
  paper faculty          -> mcq_tags specialty (same labels as Radiant: Medicine, Surgery, Gynae & Obs ...)
  _exam_label            -> sub_category when it names a subject (Gen. Pathology -> Pathology);
                            mcq_tags system (Cardiology ...) / topic (unit) otherwise
  references             -> "Reference: Snell, Ed. 9, Pg. 136" line in the explanation (text only)
  crowd answer %         -> difficulty 1-5 (none while the source still marks it 'Waiting')

Questions only ever asked in Dentistry sittings go to their own exam, 'FCPS Part 1 (Dentistry)', and
category, which retention.NON_FCPS_CATEGORIES keeps out of FCPS practice and Daily Dose.

Rows without a subject label get sub_category 'Mixed' and tag flag=subject-inferred; --classify then
votes a subject from the nearest labelled FCPS Part 1 questions (stem embeddings, numpy only).
Option order is kept (the keys are balanced and explanations name options by letter).

Embeddings need the MedCPT model, which the NAS cannot load next to the running backend. So embed on
the PC and carry the vectors across:
    python scripts/seed_mediverse.py --dry-run
    python scripts/seed_mediverse.py                              # PC
    python scripts/seed_mediverse.py --embed                      # PC, loads MedCPT
    python scripts/seed_mediverse.py --export-vectors ../mcqs/mediverse/vectors.npz
    python scripts/seed_mediverse.py --classify
  NAS (inside mednama-backend, numpy only):
    python scripts/seed_mediverse.py --vectors /app/mcqs/mediverse/vectors.npz
    python scripts/seed_mediverse.py --classify
Re-running updates rows in place (keyed on source + source_ref) and keeps the classified subjects.
"""

import argparse
import collections
import html
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import MCQ, MCQTag, PastPaper, PastPaperQuestion  # noqa: E402
from seed_mcqs import html_to_md  # noqa: E402
from seed_past_papers import FIGURE_REF  # noqa: E402

SOURCE = "pastpaper:mediverse"
LETTERS = "ABCDE"
EXAM, DENTAL_EXAM = "FCPS Part 1", "FCPS Part 1 (Dentistry)"
MAIN, DENTAL_MAIN = f"Past papers · {EXAM}", f"Past papers · {DENTAL_EXAM}"
SUBJECTS = {"Anatomy", "Physiology", "Biochemistry", "Pathology", "Pharmacology", "Microbiology",
            "Community Medicine", "Behavioural Sciences", "Neurology & Special Senses"}
MANAGED_AXES = ("specialty", "system", "topic", "collection", "flag")

# Paper-name prefix -> faculty, spelled as Radiant's specialty tags so one filter covers both archives.
FACULTY = {"med": "Medicine", "medicine": "Medicine", "surg": "Surgery", "surgery": "Surgery",
           "gynae": "Gynae & Obs", "gyn": "Gynae & Obs", "gyne": "Gynae & Obs", "radio": "Radiology",
           "radiology": "Radiology", "anesth": "Anesthesia", "anaesth": "Anesthesia", "anesthesia": "Anesthesia",
           "anaesthesia": "Anesthesia", "dent": "Dentistry", "dental": "Dentistry", "dentistry": "Dentistry",
           "patho": "Pathology", "path": "Pathology", "ent": "ENT", "eye": "Ophthalmology", "ophth": "Ophthalmology",
           "psych": "Psychiatry", "psyc": "Psychiatry", "psychiatry": "Psychiatry"}
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun",
                                          "jul", "aug", "sep", "oct", "nov", "dec"))}
SITTING_DATE = re.compile(r"(?:\b(\d{1,2})(?:st|nd|rd|th)?\s+)?\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                          r"[a-z]*\.?,?\s+((?:19|20)\d\d)\b", re.I)
SESSION = re.compile(r"\(\s*([^)]*?)\s*\)")

# MediVerse system / unit heads that name an FCPS Part 1 subject.
LABEL_SUBJECT = {
    "Basic Anatomy": "Anatomy", "Gross Anatomy": "Anatomy", "Gen. Anatomy": "Anatomy", "Embryology": "Anatomy",
    "Biochemistry": "Biochemistry", "Biostat": "Community Medicine", "Ethics": "Behavioural Sciences",
    "Psychiatry": "Behavioural Sciences", "Gen. Pathology": "Pathology", "Special Pathology": "Pathology",
    "Gen. Pharma": "Pharmacology", "Gen. Pharmacology": "Pharmacology", "Toxicology": "Pharmacology",
    "Microbiology": "Microbiology", "Immunology": "Microbiology", "Physiology": "Physiology",
    "Neuroscience": "Neurology & Special Senses", "Neurology": "Neurology & Special Senses",
    "EYE": "Neurology & Special Senses", "E.N.T": "Neurology & Special Senses",
}
# The rest are body systems or clinical units: kept as a tag, spelled consistently.
SYSTEM_NAME = {"Pulmonolgy": "Pulmonology", "Hematology": "Haematology", "Gen. Paediatrics": "Paediatrics",
               "Sp. Surgery": "Surgery", "Gen. Surgery": "Surgery", "Gynae": "Gynae & Obs",
               "Anaesthesiology": "Anesthesia", "Dentistry": "Dentistry"}
UNSORTED = re.compile(r"^unsorted", re.I)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def parse_paper(name: str) -> dict:
    """'Surg 18 Sep 2019 (M+E)' -> faculty Surgery, year 2019, dated; 'FCPS Old Pool' -> a collection."""
    clean = re.sub(r"^\s*MediVerse\s+", "", name or "", flags=re.I).strip(" !")
    head = re.split(r"[\s.]+", clean, maxsplit=1)[0].lower().strip(".,")
    faculty = FACULTY.get(head)
    m = SITTING_DATE.search(clean)
    if not m:
        return {"faculty": faculty, "year": None, "dated": False, "label": clean}
    day, mon, year = m.group(1), m.group(2)[:3].title(), int(m.group(3))
    session = SESSION.search(clean[m.end():])
    when = f"{int(day)} {mon} {year}" if day else f"{mon} {year}"
    label = f"{faculty or clean.split()[0]} · {when}" + (f" ({session.group(1)})" if session and session.group(1) else "")
    return {"faculty": faculty, "year": year, "month": MONTHS[mon.lower()], "day": int(day) if day else None,
            "dated": True, "label": label}


def exam_label(row: dict) -> tuple[str | None, str | None, str | None]:
    """_exam_label -> (subject, system tag, topic tag)."""
    raw = (row.get("_exam_label") or "").split(" (")[0].strip()
    if not raw:
        return None, None, None
    head, _, tail = (p.strip() for p in raw.partition(" / "))
    tail = None if not tail or UNSORTED.match(tail) else tail.rstrip(".")
    subject = LABEL_SUBJECT.get(head)
    system = None if subject else SYSTEM_NAME.get(head, head)
    return subject, system, tail


def difficulty(stats: dict, severity: str | None) -> int | None:
    pct = (stats or {}).get("answer_percentage")
    if severity == "Waiting" or not isinstance(pct, (int, float)):
        return None
    return 1 if pct >= 85 else 2 if pct >= 70 else 3 if pct >= 55 else 4 if pct >= 35 else 5


def explanation_md(row: dict) -> str | None:
    body = html_to_md(row.get("explanation") or "")
    refs = list(dict.fromkeys(str(r.get("full_name") or r.get("name") or "").strip()
                              for r in (row.get("references") or [])))
    refs = [r for r in refs if r]
    if refs:
        body += ("\n\n" if body else "") + "**Reference:** " + "; ".join(refs)
    return body or None


def usable(row: dict) -> str | None:
    """None if the row can be imported, else why not."""
    opts = row.get("options") or []
    if not (row.get("question") or "").strip():
        return "no stem"
    if not 2 <= len(opts) <= 5:
        return f"{len(opts)} options"
    if sum(bool(o.get("is_correct")) for o in opts) != 1:
        return "not exactly one key"
    if any(not (o.get("text") or "").strip() for o in opts):
        return "empty option"
    return None


def plan(rows: list[dict]) -> dict:
    """Everything the import writes, derived from the file alone (so --dry-run shows it all)."""
    problems, good = collections.Counter(), []
    for r in rows:
        why = usable(r)
        if why:
            problems[why] += 1
        else:
            good.append(r)

    papers: dict[int, dict] = {}
    members: dict[int, list[str]] = collections.defaultdict(list)
    for r in good:
        for p in r.get("papers") or []:
            papers.setdefault(p["id"], {"name": p.get("name") or "", **parse_paper(p.get("name") or "")})
            members[p["id"]].append(str(r["id"]))

    q = {}
    for r in good:
        ref = str(r["id"])
        ps = [papers[p["id"]] for p in (r.get("papers") or [])]
        subject, system, topic = exam_label(r)
        faculties = {p["faculty"] for p in ps if p["faculty"]}
        if system == "Dentistry":
            faculties.add("Dentistry")
        dental_only = faculties == {"Dentistry"}
        stem = html.unescape(r["question"]).replace("\r\n", "\n").strip()
        fig = bool(FIGURE_REF.search(stem))
        if fig:
            stem += "\n\n[Figure not available for this question]"
        key = next(i for i, o in enumerate(r["options"]) if o.get("is_correct"))
        tags = {("specialty", f) for f in faculties}
        tags |= {("collection", p["label"]) for p in ps if not p["dated"]}
        if system:
            tags.add(("system", system))
        if topic:
            tags.add(("topic", topic))
        if fig:
            tags.add(("flag", "figure-missing"))
        if not subject:
            tags.add(("flag", "subject-inferred"))
        q[ref] = dict(
            fields=dict(
                question_text=stem,
                options={LETTERS[i]: html.unescape(o["text"]).strip() for i, o in enumerate(r["options"])},
                correct_option=LETTERS[key],
                main_category=DENTAL_MAIN if dental_only else MAIN,
                topic=(topic or system or subject or "")[:200] or None,
                tested_concept=(topic or "")[:200] or None,
                explanation_markdown=explanation_md(r),
                difficulty=difficulty(r.get("statistics"), r.get("severity")),
                status="ready", access="restricted", source=SOURCE, source_ref=ref,
            ),
            subject=subject, tags=tags, dental_only=dental_only,
        )

    # A paper's exam: Dentistry sittings are their own exam; everything else is FCPS Part 1.
    for p in papers.values():
        p["exam"] = DENTAL_EXAM if p["faculty"] == "Dentistry" else EXAM
    return {"good": good, "problems": problems, "papers": papers, "members": members, "q": q}


def report(p: dict) -> None:
    q, papers = p["q"], p["papers"]
    dated = [x for x in papers.values() if x["dated"]]
    print(f"source {SOURCE}: {len(p['good'])} usable, skipped {dict(p['problems']) or 'none'}")
    print(f"papers: {len(dated)} dated sittings, {len(papers) - len(dated)} collections")
    print("  sittings by year:", dict(sorted(collections.Counter(x['year'] for x in dated).items())))
    print("  sittings by faculty:", dict(collections.Counter(x['faculty'] or '?' for x in dated).most_common()))
    unknown = collections.Counter(x["name"].split()[0] for x in papers.values() if not x["faculty"])
    print("  papers without a faculty (name head):", dict(unknown.most_common(12)))
    in_sitting = sum(1 for r in p["good"] if any(papers[x["id"]]["dated"] for x in (r.get("papers") or [])))
    print(f"questions in >=1 dated sitting: {in_sitting}; only in collections: {len(p['good']) - in_sitting}")
    print(f"Dentistry-only (own exam/category): {sum(v['dental_only'] for v in q.values())}")
    subj = collections.Counter(v["subject"] or "(to classify)" for v in q.values())
    print("subject from label:", dict(subj.most_common()))
    print("difficulty:", dict(sorted(collections.Counter(v['fields']['difficulty'] for v in q.values()).items(),
                                     key=lambda kv: (kv[0] is None, kv[0] or 0))))
    axes = collections.Counter(a for v in q.values() for a, _ in v["tags"])
    print("tags by axis:", dict(axes))
    print("with a reference line:", sum('**Reference:**' in (v['fields']['explanation_markdown'] or '') for v in q.values()),
          "| figure-missing:", sum(('flag', 'figure-missing') in v['tags'] for v in q.values()))


class Vectors:
    """Stem vectors from --export-vectors, kept as one float32 matrix (the NAS has ~1.3 GB to spare);
    a row becomes a Python list only when it is written."""

    def __init__(self, path: str | None):
        self.index: dict[str, int] = {}
        self.matrix = np.zeros((0, 1024), dtype=np.float32)
        if path:
            z = np.load(path)
            self.matrix = z["vecs"].astype(np.float32, copy=False)
            self.index = {str(r): i for i, r in enumerate(z["refs"])}

    def __contains__(self, ref: str) -> bool:
        return ref in self.index

    def __len__(self) -> int:
        return len(self.index)

    def __getitem__(self, ref: str) -> list[float]:
        return self.matrix[self.index[ref]].tolist()


def run(args) -> None:
    rows = read_jsonl(Path(args.file).resolve())
    p = plan(rows)
    report(p)
    q = p["q"]

    db = SessionLocal()
    existing = {ref: (mid, has_vec) for mid, ref, has_vec in db.execute(text(
        "SELECT id, source_ref, stem_embedding IS NOT NULL FROM mcqs WHERE source = :s AND source_ref IS NOT NULL"),
        {"s": SOURCE})}
    new = [ref for ref in q if ref not in existing]
    print(f"new {len(new)}, update {len(q) - len(new)}")
    if args.dry_run:
        return

    vectors = Vectors(args.vectors)
    if args.vectors:
        print(f"vectors: {len(vectors)} loaded; {sum(r in vectors for r in q)} match this import")
    t0 = time.time()
    for i in range(0, len(new), 1000):
        batch = []
        for ref in new[i:i + 1000]:
            f = dict(q[ref]["fields"], sub_category=q[ref]["subject"] or "Mixed")
            if ref in vectors:
                f["stem_embedding"] = vectors[ref]
            batch.append(f)
        db.bulk_insert_mappings(MCQ, batch)
        db.commit()
        print(f"   inserted {min(i + 1000, len(new))}/{len(new)}", flush=True)
    updates = 0
    for ref, (mid, has_vec) in existing.items():
        if ref not in q:
            continue
        f = {k: v for k, v in q[ref]["fields"].items() if k not in ("source", "source_ref")}
        if q[ref]["subject"]:   # a labelled subject; an inferred one (--classify) is kept
            f["sub_category"] = q[ref]["subject"]
        db.query(MCQ).filter(MCQ.id == mid).update(f, synchronize_session=False)
        if not has_vec and ref in vectors:
            db.execute(text("UPDATE mcqs SET stem_embedding = CAST(:v AS vector) WHERE id = :i"),
                       {"v": str(vectors[ref]), "i": mid})
        updates += 1
        if updates % 2000 == 0:
            db.commit()
    db.commit()

    mcq_of = {ref: mid for mid, ref in db.execute(
        text("SELECT id, source_ref FROM mcqs WHERE source = :s AND source_ref IS NOT NULL"), {"s": SOURCE})}

    for pid, pp in p["papers"].items():
        title = f"{pp['exam']} · {pp['label']}"
        stmt = pg_insert(PastPaper).values(code=f"mv:{pid}", exam=pp["exam"], title=title, year=pp["year"],
                                           source=SOURCE, access="restricted")
        db.execute(stmt.on_conflict_do_update(index_elements=["code"], set_={
            "exam": stmt.excluded.exam, "title": stmt.excluded.title, "year": stmt.excluded.year,
            "source": stmt.excluded.source}))
    db.commit()
    paper_id = {c: i for i, c in db.execute(text("SELECT id, code FROM past_papers WHERE code LIKE 'mv:%'"))}
    links = []
    for pid, refs in p["members"].items():   # the archive has no in-paper order: keep MediVerse id order
        for pos, ref in enumerate(sorted(set(refs), key=int)):
            if ref in mcq_of:
                links.append({"paper_id": paper_id[f"mv:{pid}"], "mcq_id": mcq_of[ref], "position": pos})
    for i in range(0, len(links), 5000):
        db.execute(pg_insert(PastPaperQuestion).values(links[i:i + 5000]).on_conflict_do_nothing())
    db.commit()

    ids = [mcq_of[ref] for ref in q if ref in mcq_of]
    db.execute(text("DELETE FROM mcq_tags WHERE mcq_id = ANY(:ids) AND axis = ANY(:axes) "
                    "AND NOT (axis = 'flag' AND label NOT IN ('figure-missing', 'subject-inferred'))"),
               {"ids": ids, "axes": list(MANAGED_AXES)})
    tag_rows = [{"mcq_id": mcq_of[ref], "axis": a, "label": lab[:200]}
                for ref, v in q.items() if ref in mcq_of for a, lab in v["tags"]]
    for i in range(0, len(tag_rows), 5000):
        db.execute(pg_insert(MCQTag).values(tag_rows[i:i + 5000]).on_conflict_do_nothing())
    db.commit()
    print(f"   papers {len(p['papers'])}, placements {len(links)}, tags {len(tag_rows)}; "
          f"done in {time.time() - t0:.0f}s")
    missing = db.execute(text("SELECT count(*) FROM mcqs WHERE source = :s AND stem_embedding IS NULL"),
                         {"s": SOURCE}).scalar()
    if missing:
        print(f"{missing} rows have no stem embedding: run --embed (PC) or --vectors <file> (NAS)")


def export_vectors(path: str) -> None:
    db = SessionLocal()
    rows = db.execute(text("SELECT source_ref, stem_embedding::text FROM mcqs WHERE source = :s "
                           "AND stem_embedding IS NOT NULL ORDER BY id"), {"s": SOURCE}).all()
    refs = np.array([r[0] for r in rows])
    vecs = np.asarray([json.loads(r[1]) for r in rows], dtype=np.float32)
    np.savez_compressed(path, refs=refs, vecs=vecs)
    print(f"wrote {len(rows)} vectors to {path}")


def classify(k: int = 15, min_share: float = 0.5) -> None:
    """Vote a subject for rows tagged flag=subject-inferred from the nearest labelled FCPS Part 1 rows."""
    from link_recalls import fetch_vectors

    db = SessionLocal()
    inferred = "EXISTS (SELECT 1 FROM mcq_tags t WHERE t.mcq_id = mcqs.id AND t.axis = 'flag' AND t.label = 'subject-inferred')"
    lab, L = fetch_vectors(db, (
        "SELECT sub_category, stem_embedding::text FROM mcqs WHERE stem_embedding IS NOT NULL "
        f"AND sub_category = ANY(:subjects) AND NOT {inferred} AND ("
        "  source LIKE 'seed:p1/%' OR (source LIKE 'pastpaper:%' AND main_category = :m))"),
        {"m": MAIN, "subjects": sorted(SUBJECTS)})
    tgt, T_all = fetch_vectors(db, (
        f"SELECT id, stem_embedding::text FROM mcqs WHERE source = :s AND stem_embedding IS NOT NULL AND {inferred}"),
        {"s": SOURCE})
    print(f"labelled {len(lab)}, to classify {len(tgt)}")
    if not lab or not tgt:
        return
    subj = [str(s) for (s,) in lab]   # plain str: psycopg2 cannot bind numpy strings
    out: dict[int, str] = {}
    shares = []
    for start in range(0, len(tgt), 1000):
        S = T_all[start:start + 1000] @ L.T
        top = np.argpartition(-S, k, axis=1)[:, :k]
        for a in range(S.shape[0]):
            votes: dict[str, float] = collections.defaultdict(float)
            for j in top[a]:
                votes[subj[j]] += max(float(S[a, j]), 0.0)
            best, w = max(votes.items(), key=lambda kv: kv[1])
            share = w / (sum(votes.values()) or 1)
            shares.append(share)
            out[tgt[start + a][0]] = best if share >= min_share else "Mixed"
    counts = collections.Counter(out.values())
    print("inferred subjects:", dict(counts.most_common()))
    print("vote share quartiles:", [round(float(x), 2) for x in np.quantile(shares, [0.25, 0.5, 0.75])])
    for s in set(out.values()):
        ids = [i for i, v in out.items() if v == s]
        db.execute(text("UPDATE mcqs SET sub_category = :s WHERE id = ANY(:ids)"), {"s": s, "ids": ids})
    db.commit()
    print("subjects written.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default="../mcqs/mediverse/fcps1.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--vectors", help="npz from --export-vectors: set stem embeddings without loading MedCPT")
    parser.add_argument("--embed", action="store_true", help="compute missing stem embeddings (loads MedCPT)")
    parser.add_argument("--export-vectors", metavar="NPZ")
    parser.add_argument("--classify", action="store_true", help="vote subjects for rows without a subject label")
    args = parser.parse_args()
    if args.embed:
        from seed_mcqs import embed

        embed(like=SOURCE)
    elif args.export_vectors:
        export_vectors(args.export_vectors)
    elif args.classify:
        classify()
    else:
        run(args)


if __name__ == "__main__":
    main()
