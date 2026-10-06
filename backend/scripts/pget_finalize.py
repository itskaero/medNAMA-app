"""Post-process the enriched + generated PGET records before writing the sheet.

Three fixes the generation pass cannot make for itself:
  * Citation preference. Every verified quote is equally real, but a paediatrics
    bank should cite Nelson over Bailey's surgery or Levinson's microbiology when
    Nelson also verified the fact.
  * Region-locked wording. The books are US texts; "in the United States" is
    wrong guidance in a Pakistani paper, so those explanations get flagged.
  * Review flags. Disputed keys, AI-decided keys and flawed questions are
    collected so they can be checked by a human before import.

Usage (from backend/):
    python scripts/pget_finalize.py --enriched <e.json> [--generated <g.json>] --out <final.json>
"""

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

PREFERRED_BOOKS = ("Nelson Pediatrics",)
KEYS = ["A", "B", "C", "D"]
# An option that names another option cannot be moved without breaking the question.
SELF_REF = re.compile(r"all of the above|none of the above|both\b|\b[a-e] and [a-e]\b|option\s+[a-e]", re.I)
REGION = re.compile(r"\b(in|for) the (United States|US|UK|United Kingdom)\b|\b(American|AAP|CDC)\b", re.I)
LEAK = re.compile(r"passage|the context|textbooks? (provided|given)|referee|verdict|"
                  r"keyed answer|as worded|the books (do|did) not", re.I)


def prefer_known_book(rec):
    """Re-pick the printed citation from the verified quotes, Nelson first."""
    ev = rec.get("evidence") or []
    if len(ev) < 2 or not rec.get("book_reference"):
        return False
    if any(rec["book_reference"].startswith(p) for p in PREFERRED_BOOKS):
        return False
    for e in ev:
        title = e.get("book_title") or ""
        if any(title.startswith(p) for p in PREFERRED_BOOKS) and e.get("page_number"):
            rec["book_reference"] = title
            rec["page_number"] = e["page_number"]
            rec["chapter_number"] = None      # chapter came from the old block
            return True
    return False


def rebalance_keys(recs, seed=20261005):
    """Spread the answer keys evenly over A-D by swapping two option texts.

    The draft keys 37 of 74 questions at B and never at D, which teaches students
    to guess rather than to know. Option order carries no meaning here (no option
    refers to another), so moving the key is safe and changes no wording.
    """
    movable = [r for r in recs
               if r.get("correct_option") in KEYS
               and sorted(r.get("options") or {}) == KEYS
               and not any(SELF_REF.search(v) for v in r["options"].values())]
    if not movable:
        return 0, []
    targets = [KEYS[i % 4] for i in range(len(movable))]
    random.Random(seed).shuffle(targets)
    moved = 0
    for rec, want in zip(movable, targets):
        have = rec["correct_option"]
        if have == want:
            continue
        opts = rec["options"]
        opts[have], opts[want] = opts[want], opts[have]
        rec["correct_option"] = want
        rec["key_rebalanced"] = f"{have}->{want}"
        moved += 1
    skipped = [r.get("n") for r in recs if r not in movable]
    return moved, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enriched", required=True)
    ap.add_argument("--generated")
    ap.add_argument("--out", required=True)
    ap.add_argument("--rebalance", choices=["none", "generated", "all"], default="generated",
                    help="spread answer keys evenly over A-D (default: generated questions only)")
    args = ap.parse_args()

    recs = json.loads(Path(args.enriched).read_text(encoding="utf-8"))
    for r in recs:
        r.setdefault("origin", "mcqs.txt")
    if args.generated and Path(args.generated).exists():
        gen = json.loads(Path(args.generated).read_text(encoding="utf-8"))
        recs += gen
        print(f"merged {len(gen)} generated questions")

    before = Counter(r.get("correct_option") for r in recs)
    if args.rebalance != "none":
        pool = recs if args.rebalance == "all" else [r for r in recs if r.get("origin") == "generated"]
        moved, skipped = rebalance_keys(pool)
        print(f"rebalanced keys on {len(pool)} questions ({args.rebalance}): {moved} moved"
              + (f", {len(skipped)} left alone (options reference each other)" if skipped else ""))

    swapped = flagged = 0
    for r in recs:
        if prefer_known_book(r):
            swapped += 1
        notes = []
        expl = r.get("explanation") or ""
        if REGION.search(expl):
            notes.append("REVIEW: explanation cites US/UK-specific guidance")
        if LEAK.search(expl):
            notes.append("REVIEW: explanation mentions the retrieval machinery")
        if not expl:
            notes.append("REVIEW: no explanation")
        elif len(expl) > 340:
            notes.append(f"REVIEW: explanation {len(expl)} chars")
        if (r.get("referee") or {}).get("agrees_with_key") is False:
            notes.append(f"REVIEW: textbooks support "
                         f"{r['referee'].get('supported_option')}, source key is {r.get('correct')}")
        if r.get("key_source") == "ai":
            notes.append(f"REVIEW: key decided without textbook support "
                         f"({r.get('key_confidence') or 'n/a'} confidence)")
        if r.get("key_caveat"):
            notes.append("REVIEW: question may be flawed")
        if r.get("grounding") == "ai_unsupported_citation":
            notes.append("REVIEW: cited passage did not support the answer; citation dropped")
        if not r.get("correct_option"):
            notes.append("BLOCKER: no answer key")
        r["review_notes"] = "; ".join(notes)
        if notes:
            flagged += 1

    Path(args.out).write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{len(recs)} records -> {args.out}")
    print(f"  citations re-pointed to a preferred book: {swapped}")
    print(f"  rows carrying a review note            : {flagged}")
    print("  key spread before:", dict(sorted(before.items(), key=lambda kv: (kv[0] or ""))))
    print("  key spread after :", dict(sorted(
        Counter(r.get("correct_option") for r in recs).items(), key=lambda kv: (kv[0] or ""))))
    print("  key source:", dict(Counter(r.get("key_source") or "source" for r in recs)))
    print("  citation  :", dict(Counter(
        (r.get("book_reference") or "(none)").split(" 2")[0] for r in recs)))


if __name__ == "__main__":
    main()
