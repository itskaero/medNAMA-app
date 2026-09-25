"""Seed the MCQ bank from the mcqs/ folder of question files (window.MM.* / NTS_* JS data).

The files come in several shapes; each is normalised to one question with
options A-E, a correct letter and (when present) an explanation:

  {t, q, a, b, c, d, ans:"B", e|exp|explanation}   P1, English, some P2 (e = explanation, not option E)
  {q, o:[...], a:<index>, e}                          compact P2
  {q, options:[...], answer:<index>, explanation}     P2 radiology
  {question, options:[...], correct:<index>, ...}     NTS MCQ bank (JS literals: evaluated by Node)
  {num, q, opts:[...], ans:<index>, exp, passage_id}  NTS mocks (passage text is put in the stem)

Files are evaluated by scripts/dump_mcq_js.js in a Node sandbox (no fs/network).

Categories (main_category / sub_category):
  FCPS Part 1 / <subject>      FCPS Part 2 / <specialty>     NTS MCQ bank / Mixed
  NTS mocks / NTS Mock N       English / <Grammar|Sentence correction|Vocabulary>

Answer keys in some files are heavily skewed (P1: ~90% B/C), which teaches "pick B".
Option order is shuffled deterministically unless an option refers to others
("all of the above", "both A and B") or the explanation names options by letter.

Duplicates (same stem and correct answer, within the import or already in the
bank) are skipped, so re-running is safe. Every row gets source='seed:<file>'.

Usage (from backend/):
    python scripts/seed_mcqs.py --root ../mcqs --dry-run
    python scripts/seed_mcqs.py --root ../mcqs
    python scripts/seed_mcqs.py --embed            # backfill stem embeddings (slow; resumable)
    python scripts/seed_mcqs.py --remove seed:p1/  # delete seeded rows by source prefix (unanswered only)
"""

import argparse
import collections
import hashlib
import html
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import MCQ  # noqa: E402

LETTERS = "ABCDE"
P1_SUBJECT = {"behavioral": "Behavioural Sciences", "biochem": "Biochemistry", "community": "Community Medicine",
              "micro": "Microbiology", "patho": "Pathology", "pharma": "Pharmacology", "physio": "Physiology"}
P2_SPECIALTY = {"anaes": "Anaesthesia", "ent": "ENT", "gynae": "Obstetrics & Gynaecology", "med": "Medicine",
                "oph": "Ophthalmology", "psych": "Psychiatry", "radio": "Radiology", "surg": "Surgery"}
ENGLISH = {"english_grammar": "Grammar", "english_sentence": "Sentence correction", "english_vocab": "Vocabulary"}
TOPIC_WORDS = {"patho": "Pathology", "pharma": "Pharmacology", "physio": "Physiology", "biochem": "Biochemistry",
               "biostats": "Biostatistics", "anatomy": "Anatomy", "cns": "CNS", "cvs": "CVS", "git": "GIT",
               "derma": "Dermatology", "endo": "Endocrinology", "gastro": "Gastroenterology", "haemo": "Haematology",
               "hepato": "Hepatology", "infect": "Infectious diseases", "nephro": "Nephrology", "paeds": "Paediatrics",
               "pulmo": "Pulmonology", "rheuma": "Rheumatology", "ext": "External", "mid": "Middle",
               "neurooph": "Neuro-ophthalmology", "behav": "Behavioural", "neurosurg": "Neurosurgery",
               "cardiothor": "Cardiothoracic", "gen": "General", "genito": "Genitourinary", "ortho": "Orthopaedics",
               "resp": "Respiratory", "vitreo": "Vitreo", "psych": "Psychiatry", "med": "Medicine"}

GENERIC_OPTION = re.compile(r"\b(all|none|both|neither)\b|\b[a-e]\s*(?:and|&|,)\s*[a-e]\b|\bonly\s+[a-e]\b", re.I)
LETTER_REF = re.compile(   # an explanation that names options by letter: keep the file's option order
    r"\b(?:option|choice|answer)s?\s*[-(]?\s*[A-Ea-e]\b|\([A-E]\)|\b[A-E]\)\s|\b[A-E]\s*[-–:]\s|\bOption[A-E]\b"
    r"|(?:^|[\s(,;])[A-E](?:\)|\s+(?:is|are|was|lacks|contradicts|incorrectly|has|does|refers|would|only|"
    r"describes|suggests|represents|fits|shows|and|or)\b)", re.M)


def html_to_md(s: str) -> str:
    s = s or ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</?(strong|b)>", "**", s, flags=re.I)
    s = re.sub(r"</?(em|i)>", "*", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = re.sub(r"</(li|p|div|ul|ol)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"\*\*\s*\*\*", "", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def topic_from_stem(stem: str, spec: str) -> str:
    rest = re.sub(rf"^{re.escape(spec)}_(p2_)?", "", stem)
    if rest.startswith("golden"):
        return "Golden questions"
    words = [TOPIC_WORDS.get(w, w.upper() if len(w) <= 3 else w.capitalize()) for w in rest.split("_") if w]
    return " ".join(words) or P2_SPECIALTY.get(spec, spec)


def dump(root: Path, node: str) -> list[dict]:
    helper = Path(__file__).with_name("dump_mcq_js.js")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dump.json"
        subprocess.run([node, str(helper), str(root), str(out)], check=True)
        return json.loads(out.read_text(encoding="utf-8"))


def _row(q, options, correct_letter, explanation, main, sub, topic, source):
    return {"q": q, "options": options, "correct": correct_letter, "explanation": explanation,
            "main": main, "sub": sub, "topic": topic, "source": source}


def normalise(dumped: list[dict]) -> tuple[list[dict], collections.Counter]:
    rows: list[dict] = []
    problems: collections.Counter = collections.Counter()
    for entry in dumped:
        f = entry["file"]
        if entry.get("error"):
            problems[f"{f}: evaluation error"] += 1
            continue
        top, stem = f.split("/")[0], f.rsplit("/", 1)[-1][:-3]
        source = f"seed:{f}"
        win, loc = entry.get("window") or {}, entry.get("locals") or {}
        lists = []
        passages = {}
        for scope in (win, loc):
            for k, v in scope.items():
                if k == "MM" and isinstance(v, dict):
                    lists += [x for x in v.values() if isinstance(x, list)]
                elif isinstance(v, list):
                    lists.append(v)
                elif isinstance(v, dict) and k.endswith("_PASSAGES"):
                    passages = v
        if top == "p1":
            main, sub = "FCPS Part 1", P1_SUBJECT.get(stem, stem.capitalize())
        elif top == "p2":
            spec = f.split("/")[1]
            main, sub = "FCPS Part 2", P2_SPECIALTY.get(spec, spec.capitalize())
        elif top == "english":
            main, sub = "English", ENGLISH.get(stem, stem)
        elif top == "nts_range":
            main, sub = "NTS MCQ bank", "Mixed"
        elif top == "nts":
            n = re.sub(r"\D", "", stem) or stem
            main, sub = "NTS mocks", f"NTS Mock {n}"
        else:
            main, sub = top, stem
        default_topic = topic_from_stem(stem, f.split("/")[1]) if top == "p2" else sub
        for L in lists:
            for r in L:
                if not isinstance(r, dict):
                    continue
                q = str(r.get("q") or r.get("question") or "").strip()
                # letter-keyed options a..d; note 'e' is the explanation in these files, never option E
                if all(k in r for k in ("a", "b", "c", "d")) and isinstance(r.get("ans"), str):
                    opts = [r.get(k) for k in ("a", "b", "c", "d")]
                    correct = r["ans"].strip().upper()[:1]
                    idx = LETTERS.index(correct) if correct in LETTERS[:4] else -1
                    expl = r.get("e") or r.get("exp") or r.get("explanation") or ""
                else:
                    opts = r.get("o") or r.get("options") or r.get("opts")
                    ans = r.get("correct", r.get("answer", r.get("ans", r.get("a"))))
                    idx = ans if isinstance(ans, int) else (int(ans) if str(ans).isdigit() else -1)
                    expl = r.get("e") or r.get("exp") or r.get("explanation") or ""
                if not isinstance(opts, list) or not q:
                    problems[f"{f}: malformed row"] += 1
                    continue
                opts = [html.unescape(str(o)).strip() for o in opts if o is not None and str(o).strip()]
                if not (3 <= len(opts) <= 5) or not (0 <= idx < len(opts)):
                    problems[f"{f}: bad options/answer"] += 1
                    continue
                pid = r.get("passage_id")
                if pid and pid in passages:
                    q = f"Read the passage:\n\n{passages[pid].get('text', '').strip()}\n\n{q}"
                t = str(r.get("t") or "").strip()
                topic = t if t and not t.isdigit() else default_topic   # some files number their topics
                rows.append(_row(q, opts, idx, html_to_md(str(expl)), main, sub, topic, source))
    return rows, problems


def maybe_shuffle(row: dict) -> bool:
    opts, idx = row["options"], row["correct"]
    if any(GENERIC_OPTION.search(o) for o in opts) or LETTER_REF.search(row["explanation"] or ""):
        return False
    rng = random.Random(hashlib.sha1(norm(row["q"]).encode()).hexdigest())
    order = list(range(len(opts)))
    rng.shuffle(order)
    row["options"] = [opts[i] for i in order]
    row["correct"] = order.index(idx)
    return True


def seed(args) -> None:
    dumped = dump(Path(args.root).resolve(), args.node)
    rows, problems = normalise(dumped)
    db = SessionLocal()
    existing = set()
    for q, opts, c in db.execute(text("SELECT question_text, options, correct_option FROM mcqs")):
        existing.add((norm(q), norm(str((opts or {}).get(c, "")))))
    seen, keep, dup_by = set(existing), [], collections.Counter()
    shuffled = 0
    for r in rows:
        key = (norm(r["q"]), norm(r["options"][r["correct"]]))
        if key in seen:
            dup_by[r["main"]] += 1
            continue
        seen.add(key)
        shuffled += maybe_shuffle(r)
        keep.append(r)

    by_cat = collections.Counter((r["main"], r["sub"]) for r in keep)
    before = collections.Counter(LETTERS[r["correct"]] for r in rows)
    after = collections.Counter(LETTERS[r["correct"]] for r in keep)
    print(f"parsed {len(rows)} questions from {len(dumped)} files; skipped {sum(problems.values())} malformed")
    for p, n in problems.most_common(10):
        print(f"   {n:5}  {p}")
    print(f"duplicates skipped: {sum(dup_by.values())} {dict(dup_by)}")
    print(f"to insert: {len(keep)} (options shuffled for {shuffled})")
    print(f"answer key spread before {dict(sorted(before.items()))} -> after {dict(sorted(after.items()))}")
    for (main, sub), n in sorted(by_cat.items()):
        print(f"   {n:6}  {main} / {sub}")
    if args.dry_run:
        return
    t0 = time.time()
    for i in range(0, len(keep), 1000):
        db.bulk_save_objects([
            MCQ(question_text=r["q"], options={LETTERS[j]: o for j, o in enumerate(r["options"])},
                correct_option=LETTERS[r["correct"]], topic=r["topic"][:200], main_category=r["main"],
                sub_category=r["sub"], explanation_markdown=r["explanation"] or None, status="ready",
                tested_concept=r["topic"][:200], source=r["source"])
            for r in keep[i:i + 1000]
        ])
        db.commit()
        print(f"   inserted {min(i + 1000, len(keep))}/{len(keep)}", flush=True)
    print(f"done in {time.time() - t0:.0f}s")


def embed(batch: int = 128) -> None:
    from app.retention import _embed

    db = SessionLocal()
    total = db.execute(text("SELECT count(*) FROM mcqs WHERE source LIKE 'seed:%' AND stem_embedding IS NULL")).scalar()
    print(f"{total} seeded questions without an embedding", flush=True)
    done, t0 = 0, time.time()
    while True:
        rows = db.execute(text("SELECT id, question_text FROM mcqs WHERE source LIKE 'seed:%' AND stem_embedding IS NULL "
                               "ORDER BY id LIMIT :n"), {"n": batch}).all()
        if not rows:
            break
        vecs = _embed([q[:2000] for _, q in rows])
        for (mid, _), v in zip(rows, vecs):
            db.execute(text("UPDATE mcqs SET stem_embedding = CAST(:v AS vector) WHERE id = :id"),
                       {"v": str(v.tolist()), "id": mid})
        db.commit()
        done += len(rows)
        rate = done / max(1e-6, time.time() - t0)
        print(f"   {done}/{total}  ({rate:.1f}/s, ~{(total - done) / max(rate, 1e-6) / 60:.0f} min left)", flush=True)


def remove(prefix: str) -> None:
    db = SessionLocal()
    n = db.execute(text(
        "DELETE FROM mcqs m WHERE m.source LIKE :p AND NOT EXISTS (SELECT 1 FROM attempt_answers a WHERE a.mcq_id = m.id) "
        "AND NOT EXISTS (SELECT 1 FROM answer_events e WHERE e.mcq_id = m.id)"), {"p": prefix + "%"}).rowcount
    db.commit()
    print(f"removed {n} unanswered questions with source {prefix}*")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="../mcqs")
    parser.add_argument("--node", default="node")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--embed", action="store_true", help="backfill stem embeddings for seeded questions")
    parser.add_argument("--remove", metavar="SOURCE_PREFIX")
    args = parser.parse_args()
    if args.remove:
        remove(args.remove)
    elif args.embed:
        embed()
    else:
        seed(args)


if __name__ == "__main__":
    main()
