"""Verify AI-generated PGET MCQs: re-derive every key and audit the question itself.

Two independent checks, because generation gets both wrong in different ways:

1. Key. The generator is told which letter to key (to stop every answer landing on
   A) and sometimes complies by relabelling instead of reordering, leaving a key
   that contradicts its own explanation. So the generated key is thrown away and
   re-derived through the same path the human-written questions used: retrieval ->
   Answer-Key Referee -> explicit decision. The explanation is rewritten to match
   whatever key that yields, so the two can never disagree.

2. The question. A key can be right while the item is still unusable: two
   defensible options (one a superset of another), a stem that asks for something
   its own answer says is impossible, or a claim that depends on local law. Those
   need a human, so they are flagged rather than silently patched.

Usage (from backend/):
    python scripts/pget_verify_generated.py --records <generated.json> --out <verified.json>
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.llm import chat_completion  # noqa: E402

from scripts.pget_build_import import enrich  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("app.retrieval").setLevel(logging.WARNING)
log = logging.getLogger("pget-verify")

AUDIT_SYSTEM = (
    "You are vetting a single-best-answer MCQ for a paediatric postgraduate entrance exam. "
    "Report only defects that would make the item unfair or unusable:\n"
    "- two_defensible: more than one option is correct, or one option is a broader category "
    "that contains the keyed option (e.g. 'Congenital adrenal hyperplasia' alongside "
    "'Salt-losing congenital adrenal hyperplasia').\n"
    "- stem_mismatch: the stem asks for something the keyed answer cannot be, or the "
    "explanation's reasoning contradicts the stem (e.g. asking which test 'confirms' a "
    "diagnosis that is clinical and has no confirmatory test).\n"
    "- jurisdiction: the answer depends on local law, policy or a national schedule rather "
    "than on medicine, so no textbook settles it.\n"
    "- no_single_best: no option is clearly correct.\n"
    "If the item is sound, return an empty defects list. Do not invent defects and do not "
    "comment on style, difficulty or wording you merely dislike.\n"
    'Return JSON: {"defects": ["two_defensible", ...], "note": "one short sentence naming the '
    'specific problem, empty if none", "better_option": "letter that should be keyed, or null"}'
)


def audit(rec):
    opts = "\n".join(f"{k}. {v}" for k, v in sorted(rec["options"].items()))
    user = (
        f"QUESTION: {rec['question']}\nOPTIONS:\n{opts}\n"
        f"KEYED: {rec.get('correct_option')}\n"
        f"EXPLANATION: {rec.get('explanation')}"
    )
    raw = chat_completion(
        [{"role": "system", "content": AUDIT_SYSTEM}, {"role": "user", "content": user}],
        json_mode=True, temperature=0.0, max_tokens=400, label="pget-audit", role="chat",
    )
    out = json.loads(raw)
    defects = [str(d).strip() for d in (out.get("defects") or []) if str(d).strip()]
    better = str(out.get("better_option") or "").strip().upper()[:1]
    return defects, str(out.get("note") or "").strip(), (better if better in rec["options"] else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    recs = json.loads(Path(args.records).read_text(encoding="utf-8"))
    out_path = Path(args.out)
    db = SessionLocal()
    flipped = flagged = 0
    try:
        for i, rec in enumerate(recs, 1):
            rec["generated_key"] = rec.get("correct_option")
            rec["correct"] = None          # force the key to be re-derived, not trusted
            try:
                enrich(db, rec)
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"
                log.warning("[%d/%d] %s failed: %s", i, len(recs), rec.get("topic"), rec["error"])
                continue

            notes = []
            if rec.get("correct_option") != rec["generated_key"]:
                flipped += 1
                notes.append(f"KEY CORRECTED: generated {rec['generated_key']}, "
                             f"verified {rec.get('correct_option')} ({rec.get('key_source')})")
            try:
                defects, note, better = audit(rec)
            except Exception as e:
                defects, note, better = [], f"audit failed: {e}", None
            if defects:
                flagged += 1
                notes.append("REWRITE (" + ", ".join(defects) + ")" + (f": {note}" if note else ""))
                if better and better != rec.get("correct_option"):
                    notes.append(f"audit suggests key {better}")
            rec["audit_defects"] = defects
            rec["audit_note"] = note
            rec["verify_notes"] = "; ".join(notes)
            log.info("[%d/%d] %s | key %s->%s | %s", i, len(recs), rec.get("topic"),
                     rec["generated_key"], rec.get("correct_option"), defects or "sound")
            if i % 5 == 0 or i == len(recs):
                out_path.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        db.close()
    out_path.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("verified %d: %d keys corrected, %d flagged for rewrite -> %s",
             len(recs), flipped, flagged, out_path)


if __name__ == "__main__":
    main()
