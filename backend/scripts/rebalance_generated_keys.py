"""Spread the keys of AI-generated MCQs across A-E (they were 55% A, which teaches "pick A").

Generated rows (source IS NULL: AI MCQs, concept re-tests, high-yield, look-alikes, spot the diagnosis)
get the deterministic shuffle the seeder uses (seed_mcqs.maybe_shuffle): skipped when an option refers
to others ("all of the above", "A and B") or the explanation names options by letter, since those would
no longer match. New questions are asked for a random key position (llm.key_instruction), so this is a
one-off for rows written before that. Twists are left alone (they were always given a random position).

Dry run by default; --apply writes. Light (no models), so it also runs on the NAS:
    docker exec -w /app/backend mednama-backend python scripts/rebalance_generated_keys.py --apply
"""

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from seed_mcqs import LETTERS, maybe_shuffle  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    rows = db.execute(text(
        "SELECT id, question_text, options, correct_option, coalesce(explanation_markdown, '') FROM mcqs "
        "WHERE source IS NULL AND twist_of IS NULL ORDER BY id")).all()
    before, after, changed = collections.Counter(), collections.Counter(), []
    for mid, stem, options, key, expl in rows:
        before[key] += 1
        letters = sorted((options or {}).keys())
        if key not in letters or letters != list(LETTERS[:len(letters)]):
            after[key] += 1
            continue
        row = {"q": stem, "options": [options[k] for k in letters], "correct": letters.index(key), "explanation": expl}
        if maybe_shuffle(row):
            new_key = LETTERS[row["correct"]]
            after[new_key] += 1
            changed.append((mid, {LETTERS[i]: o for i, o in enumerate(row["options"])}, new_key))
        else:
            after[key] += 1
    print(f"{len(rows)} generated questions; {len(changed)} can be reshuffled "
          f"({len(rows) - len(changed)} name options by letter or refer to other options, kept)")
    print("keys before:", dict(sorted(before.items())))
    print("keys after: ", dict(sorted(after.items())))
    if not args.apply:
        print("Dry run. Re-run with --apply to write.")
        return
    for mid, opts, key in changed:
        db.execute(text("UPDATE mcqs SET options = CAST(:o AS jsonb), correct_option = :k WHERE id = :i"),
                   {"o": json.dumps(opts), "k": key, "i": mid})
    db.commit()
    print(f"{len(changed)} questions reshuffled.")


if __name__ == "__main__":
    main()
