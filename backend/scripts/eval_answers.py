"""Score the full answer pipeline on FCPS-style questions with required key facts.

Each question lists key facts; a fact counts as present when any of its
alternative phrasings appears in the answer (case-insensitive). The script
reports key-fact recall, grounding, how many books were cited, and latency, so
a different answer model (e.g. MedGemma behind an OpenAI-compatible server) can
be compared with the default DeepSeek model on the same questions.

Usage (from backend/):
    python scripts/eval_answers.py                                   # default (DEEPSEEK_* settings)
    python scripts/eval_answers.py --model medgemma-27b --base-url http://gpu-box:8000/v1 --api-key-env MEDGEMMA_KEY
    python scripts/eval_answers.py --limit 10 --out results.json

The retrieval side is identical for every model; only the answer model changes.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# (question, [fact alternatives, ...])
QUESTIONS: list[tuple[str, list[list[str]]]] = [
    ("fluid of choice in hypertrophic pyloric stenosis", [["0.9%", "normal saline", "sodium chloride"], ["kcl", "potassium"], ["alkalosis"]]),
    ("why does paradoxical aciduria occur in gastric outlet obstruction", [["h+", "hydrogen"], ["potassium", "k+", "hypokal"], ["alkalosis"]]),
    ("pathology of juvenile nasopharyngeal angiofibroma", [["adolescent", "young male", "males"], ["vascular", "vessels"], ["fibrous", "stroma"]]),
    ("drug of choice for absence seizures", [["ethosuximide"], ["valproate"]]),
    ("antidote for heparin overdose", [["protamine"]]),
    ("reversal of warfarin over-anticoagulation with bleeding", [["vitamin k"], ["prothrombin complex", "pcc", "fresh frozen plasma", "ffp"]]),
    ("investigation of choice for cholesteatoma", [["hrct", "ct scan", "computed tomography"], ["diffusion", "mri"]]),
    ("Gradenigo syndrome triad", [["abducens", "sixth", " vi "], ["retro-orbital", "trigeminal"], ["otorrh", "discharge"]]),
    ("nerve injured in fracture of surgical neck of humerus", [["axillary"]]),
    ("most common site of carcinoid tumour", [["appendix", "small intestine", "ileum"]]),
    ("Frank-Starling law of the heart", [["stretch", "end-diastolic", "preload"], ["force", "stroke volume"]]),
    ("mechanism of action of cholera toxin", [["camp", "cyclic amp"], ["adenylate", "adenylyl"]]),
    ("initial management of diabetic ketoacidosis", [["0.9%", "saline", "fluid"], ["insulin"], ["potassium"]]),
    ("most common cause of community acquired pneumonia", [["streptococcus pneumoniae", "pneumococcus"]]),
    ("Virchow triad", [["stasis"], ["endothelial", "vessel wall", "vascular injury"], ["hypercoagul"]]),
    ("Beck triad of cardiac tamponade", [["hypotension"], ["jugular", "jvp", "neck vein"], ["muffled", "heart sounds"]]),
    ("Charcot triad of ascending cholangitis", [["fever"], ["jaundice"], ["pain", "right upper quadrant"]]),
    ("drug of choice for trigeminal neuralgia", [["carbamazepine"]]),
    ("first-line treatment of anaphylaxis", [["adrenaline", "epinephrine"], ["intramuscular", " im "]]),
    ("most common site of ectopic pregnancy", [["ampulla"], ["fallopian tube", "tubal"]]),
    ("Cushing triad of raised intracranial pressure", [["hypertension"], ["bradycardia"], ["respiration", "breathing"]]),
    ("Reed-Sternberg cells are characteristic of which disease", [["hodgkin"]]),
    ("first-line eradication therapy for Helicobacter pylori", [["proton pump", "ppi", "omeprazole"], ["clarithromycin"], ["amoxicillin", "metronidazole"]]),
    ("mechanism of action of aspirin", [["cyclooxygenase", "cox"], ["irreversib", "acetylat"]]),
    ("features of Horner syndrome", [["ptosis"], ["miosis"], ["anhidrosis"]]),
    ("karyotype of Klinefelter syndrome", [["47", "xxy"]]),
    ("most common benign tumour of the liver", [["haemangioma", "hemangioma"]]),
    ("Brown-Sequard syndrome findings", [["ipsilateral"], ["contralateral"], ["pain", "temperature"]]),
    ("drug of choice for syphilis", [["penicillin"]]),
    ("Trotter triad in nasopharyngeal carcinoma", [["deafness", "hearing"], ["palate"], ["neuralgia", "pain", "trigeminal"]]),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="answer model name (overrides LLM_CHAT_MODEL)")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL for the answer model")
    parser.add_argument("--api-key-env", help="environment variable holding the answer model's API key")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", help="write per-question results as JSON")
    args = parser.parse_args()

    from app.config import settings

    if args.model:
        settings.llm_chat_model = args.model
    if args.base_url:
        settings.llm_chat_base_url = args.base_url
    if args.api_key_env:
        settings.llm_chat_api_key = os.environ.get(args.api_key_env, "")

    from app.database import SessionLocal
    from app.generation import generate_answer
    from app.llm import endpoint

    ep = endpoint("chat")
    print(f"Answer model: {ep.model} @ {ep.base_url}\n")
    session = SessionLocal()
    rows, total_recall, total_time = [], 0.0, 0.0
    questions = QUESTIONS[: args.limit] if args.limit else QUESTIONS
    for q, facts in questions:
        t0 = time.monotonic()
        out = generate_answer(session, q)
        elapsed = time.monotonic() - t0
        text = f" {out.get('answer_markdown', '').lower()} "
        hits = [any(alt in text for alt in group) for group in facts]
        recall = sum(hits) / len(facts)
        books = sorted({c["book_title"] for c in out.get("citations", [])})
        total_recall += recall
        total_time += elapsed
        missing = [group[0] for group, hit in zip(facts, hits) if not hit]
        print(f"{recall:4.0%}  {elapsed:5.1f}s  {out.get('grounding', '?'):8} books={len(books)}  {q}"
              + (f"   missing: {missing}" if missing else ""))
        rows.append({"question": q, "recall": recall, "missing": missing, "seconds": round(elapsed, 1),
                     "grounding": out.get("grounding"), "status": out.get("status"), "books_cited": books})
        session.rollback()
    n = len(questions)
    multi = sum(1 for r in rows if len(r["books_cited"]) >= 2)
    print(f"\nKey-fact recall {total_recall / n:.1%} | mean latency {total_time / n:.1f}s | "
          f"answers citing >= 2 books: {multi}/{n} | errors: {sum(r['status'] != 'ok' for r in rows)}")
    if args.out:
        Path(args.out).write_text(json.dumps({"model": ep.model, "results": rows}, indent=2), encoding="utf-8")
        print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
