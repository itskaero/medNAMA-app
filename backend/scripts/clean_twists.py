"""Delete Past-paper Twists that give away their original question's answer.

Twists used to include a "Reverse" type that put the original answer in the statement ("Glucose is reabsorbed
by secondary active transport. What drives it?"), and a mechanism/complication twist could name it too. A student
who meets the original question later has already been told its answer, so those twists go:

  - every twist tagged twist=reverse
  - every other twist whose statement contains its seed's answer (app.twists.reveals_answer)
  - "next step" twists that ask for a management decision ("first step in management", tourniquet, surgery):
    clinical-exam material, not FCPS Part 1 basic science, and labelled with the seed's basic-science subject
    (Pharmacology seeds are kept: "drug of choice" is Part 1 material)

It also strips the line older twists ended their explanation with, "Twist of a past-paper question (type):
<original question> (answer: <original answer>)", down to "Twist of a past-paper question (type)".

The seeds become eligible again: prewarm_twists.py (or "Twist it") writes them anew under the current rules.
A twist that someone already answered in a practice session or bookmarked is retired instead of deleted
(attempt_answers and mcq_bookmarks reference it): detached from its seed and made private, so no practice
pool serves it while old quiz reviews and bookmarks still open it.

Dry run by default; --apply deletes. No models, so it runs on the NAS too:
    docker exec -w /app/backend mednama-backend python scripts/clean_twists.py [--apply]
"""

import argparse
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.twists import reveals_answer  # noqa: E402

MANAGEMENT = re.compile(
    r"\b(management|manag(e|ed|ing)|first step|next step|initial step|immediate step|first[- ]line (management|step)"
    r"|most appropriate (initial |next |immediate )?(step|action|management|intervention)|resuscitat\w*"
    r"|surgical(ly)?|tourniquet|laparotomy|emergency (surgery|treatment))\b", re.I)
# The old explanation ending: "**Twist of a past-paper question** (next step): <original stem> (answer: X)"
LEAK_SQL = r"(\*\*Twist of a past-paper question\*\* \([^)]*\)):[^\n]*"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--management", action="store_true",
                        help="also retire 'next step' twists that ask for a management decision")
    args = parser.parse_args()

    db = SessionLocal()
    rows = db.execute(text(
        "SELECT t.id, t.question_text, s.options ->> s.correct_option AS seed_answer, "
        "       (SELECT label FROM mcq_tags g WHERE g.mcq_id = t.id AND g.axis = 'twist' LIMIT 1) AS kind, "
        "       s.sub_category AS seed_subject "
        "FROM mcqs t JOIN mcqs s ON s.id = t.twist_of")).all()
    doomed, why = [], collections.Counter()
    for tid, stem, seed_answer, kind, seed_subject in rows:
        if args.management and kind == "next-step" and seed_subject != "Pharmacology" and MANAGEMENT.search(stem or ""):
            doomed.append(tid)
            why["next step asks for management"] += 1
        elif kind == "reverse":
            doomed.append(tid)
            why["reverse type"] += 1
        elif reveals_answer(stem or "", seed_answer or ""):
            doomed.append(tid)
            why["statement contains the original answer"] += 1
    seeds = db.execute(text("SELECT count(DISTINCT twist_of) FROM mcqs WHERE id = ANY(:i)"), {"i": doomed or [-1]}).scalar()
    print(f"{len(rows)} twists; {len(doomed)} to remove ({dict(why) or 'none'}), "
          f"from {seeds} questions")
    for tid, stem, seed_answer, kind, _ in [r for r in rows if r[0] in set(doomed)][:6]:
        print(f"   [{kind}] {' '.join((stem or '').split())[:110]}  (original answer: {seed_answer})")
    leaks = db.execute(text("SELECT count(*) FROM mcqs WHERE twist_of IS NOT NULL AND explanation_markdown ~ :p"),
                       {"p": LEAK_SQL}).scalar()
    print(f"{leaks} explanations end with the original question and its answer")
    if not args.apply:
        print("Dry run. Re-run with --apply to delete / strip.")
        return
    db.execute(text(r"UPDATE mcqs SET explanation_markdown = regexp_replace(explanation_markdown, :p, '\1', 'g') "
                    "WHERE explanation_markdown ~ :p"), {"p": LEAK_SQL})
    kept_for_history = {i for (i,) in db.execute(text(
        "SELECT mcq_id FROM attempt_answers WHERE mcq_id = ANY(:i) UNION SELECT mcq_id FROM mcq_bookmarks WHERE mcq_id = ANY(:i)"),
        {"i": doomed or [-1]})}
    delete = [i for i in doomed if i not in kept_for_history]
    db.execute(text("UPDATE mcqs SET twist_of = NULL, status = 'private', main_category = 'Retired twists' "
                    "WHERE id = ANY(:i)"), {"i": list(kept_for_history) or [-1]})
    db.execute(text("DELETE FROM mcqs WHERE id = ANY(:i)"), {"i": delete or [-1]})   # tags cascade
    db.commit()
    left = db.execute(text("SELECT count(*) FROM mcqs WHERE twist_of IS NOT NULL")).scalar()
    print(f"deleted {len(delete)}, retired {len(kept_for_history)} (answered or bookmarked); {left} twists remain.")


if __name__ == "__main__":
    main()
