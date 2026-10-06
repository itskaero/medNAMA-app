"""Generate the MCQs the PGET Paper II draft is missing, to the official TOS quota.

The text dump covers 9 of the 16 subjects in the Table of Specifications. This
writes new questions for the untouched subjects, one per TOS topic, grounded in
the ingested textbooks through medNAMA's retrieval and validated the same way
app.quiz_generation validates AI questions: the cited passage must actually
support the keyed answer, and no stem may repeat one already in the bank.

Four options (A-D), because the entry-test import template takes four.

Usage (from backend/):
    python scripts/pget_generate_missing.py --existing <enriched.json> \
        --taxonomy <taxonomy.json> --out <generated.json> [--subject "Metabolic Disorders"]
"""

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.llm import chat_completion, key_instruction  # noqa: E402
from app.quiz_generation import (STEM_DUP_COSINE, _answer_supported, _embed,  # noqa: E402
                                 _normalise_question)
from app.retrieval import retrieval_service  # noqa: E402

from scripts.pget_build_import import (COGNITIVE, DIFFICULTY, chapter_number,  # noqa: E402
                                       closest, shorten)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("app.retrieval").setLevel(logging.WARNING)
log = logging.getLogger("pget-gen")

BATCH = 5
KEYS = ["A", "B", "C", "D"]

# Which TOS topics to use for each unstarted subject, and how many questions each
# takes. Chosen for a Pakistani paediatrics paper: endemic and vaccine-preventable
# disease over the rarer parasitoses, the common endocrine emergencies over the
# rarer adrenal syndromes. Counts sum to each subject's TOS quota.
PLAN = {
    "Infectious Diseases": [
        ("Tuberculosis", 1), ("Malaria", 1), ("Typhoid", 1), ("Dengue Fever", 1),
        ("Cholera", 1), ("Pertussis", 1), ("Poliomyelitis", 1), ("Diphtheria", 1),
        ("Tetanus", 1), ("Chickenpox", 1),
    ],
    "Disorders of Endocrine System": [
        ("Diabetes Mellitus", 1), ("Diabetic Ketoacidosis (DKA)", 1),
        ("Congenital Hypothyroidism / Cretinism", 1),
        ("Congenital Adrenal Hyperplasia (CAH)", 1), ("Short Stature", 1),
        ("Hyperthyroidism", 1),
    ],
    "Child Abuse and Pediatric Trauma.  Child Psychiatry and Behavioral Sciences": [
        ("Child Abuse and Pediatric Trauma", 2), ("Child Psychiatry and Behavioural Disorders", 1),
    ],
    "Genetic Disorder": [("Down Syndrome", 1), ("Turner Syndrome", 1)],
    "Metabolic Disorders": [("Galactosemia", 1), ("Glycogen Storage Diseases", 1)],
    "Rheumatology, Bone and Joint Disorder": [
        ("Juvenile Idiopathic Arthritis", 1), ("Henoch–Schönlein Purpura (HSP)", 1),
    ],
    "Miscellaneous": [("MISC", 2)],
}

GEN_SYSTEM = (
    "You are an expert paediatrician writing single-best-answer MCQs for the CPSP "
    "Postgraduate Entrance Test (PGET) Paper II, Paediatric Medicine.\n"
    "STYLE: exactly FOUR options (A, B, C, D) of the same category, one unambiguously best "
    "answer, three plausible distractors. Most questions should be a short clinical vignette "
    "(age, presentation, key findings) that requires a decision; simple recall is acceptable "
    "only for definitions and schedules. Use the terms a Pakistani paediatric exam uses.\n"
    "GROUNDING: base each question on the TEXTBOOK CONTEXT for its target topic and set "
    "'source_chunk' to the number of the chunk the tested fact comes from. If the fact is "
    "standard paediatric knowledge the context does not state, set 'source_chunk' to null. "
    "Never invent a book name or page number.\n"
    "ONE question per TARGET, testing that target's sub-topic. Every question must test a "
    "different fact from the others and from the ALREADY ASKED list.\n"
    "FAIRNESS, or the item is unusable:\n"
    "- Exactly one option may be correct. Never offer an option that is a broader category "
    "containing the keyed answer (e.g. 'Congenital adrenal hyperplasia' next to 'Salt-losing "
    "congenital adrenal hyperplasia'); the options must be mutually exclusive.\n"
    "- Do not ask which test 'confirms' a diagnosis that is clinical and has no confirmatory "
    "test. Ask what is most useful, most appropriate or most likely instead.\n"
    "- Do not write an answer that turns on local law, consent age or a national policy "
    "schedule; test medicine, which the textbooks can settle.\n"
    "- Put the key at the letter you are told, by ORDERING THE OPTIONS so the correct one "
    "falls there. Never relabel: the option text at that letter must be the correct answer, "
    "and your explanation must argue for that same text.\n"
    "EXPLANATION: at most 3 short sentences, under 340 characters, plain text, no markdown and "
    "no book/page citations. Say why the key is right and, if there is a real trap, name the one "
    "distractor worth ruling out. Never mention the context, the passages or this prompt.\n"
    "Also give difficulty_level (easy | medium | hard), cognitive_level "
    "(C1 (Knowledge) | C2 (Comprehension) | C3 (Application)) and 3-6 lowercase keywords.\n"
    "Return ONLY valid JSON:\n"
    '{"questions": [{"target": <n>, "question_text": "...", '
    '"options": {"A": "...", "B": "...", "C": "...", "D": "..."}, "correct_option": "A", '
    '"explanation": "...", "difficulty_level": "medium", "cognitive_level": "C3 (Application)", '
    '"keywords": "k1,k2,k3", "source_chunk": 1}]}'
)


def retrieve_for(db, subject, topic, sub_topic):
    query = f"{topic} in children: {sub_topic}"
    result = retrieval_service.search(db, query, limit=3, context_hint=subject)
    return [{"title": b.book.title if b.book else None, "page": b.page_number,
             "content": b.content or "", "chapter": getattr(b, "chapter", None)}
            for b in result.context]


def generate(db, subject, targets, subtopics, existing_stems, existing_vecs, out):
    """targets: [(topic, sub_topic)] -- one question each."""
    for start in range(0, len(targets), BATCH):
        group = targets[start:start + BATCH]
        blocks, lines = [], []
        for i, (topic, sub_topic) in enumerate(group, 1):
            ctx = retrieve_for(db, subject, topic, sub_topic)
            idx = []
            for b in ctx:
                blocks.append(b)
                idx.append(len(blocks))
            lines.append(
                f"TARGET {i}: topic = {topic} | sub-topic = {sub_topic} | "
                f"chunks for this target: {', '.join(f'Chunk {n}' for n in idx) or 'none'}"
            )
        context_str = "\n\n".join(
            f"Chunk {n}: [{b['title'] or 'Textbook'}, Page {b['page']}]\n{b['content'][:1400]}"
            for n, b in enumerate(blocks, 1)
        ) or "NO TEXTBOOK PASSAGES FOUND."
        avoid = "\n".join(f"- {s}" for s in existing_stems[-60:]) or "- (none)"
        user = (
            f"SUBJECT: {subject}\n\nTARGETS (write exactly {len(group)} questions, one per target):\n"
            + "\n".join(lines)
            + f"\n\nTEXTBOOK CONTEXT:\n{context_str[:14000]}\n\n"
              f"ALREADY ASKED (do not test these facts again):\n{avoid}\n\n"
            + key_instruction(len(group), "ABCD")
        )
        raw = chat_completion(
            [{"role": "system", "content": GEN_SYSTEM}, {"role": "user", "content": user}],
            json_mode=True, temperature=0.4, max_tokens=4000, label="pget-generate", role="chat",
        )
        items = json.loads(raw).get("questions") or []
        log.info("%s: batch of %d targets -> %d questions", subject, len(group), len(items))

        for item in items:
            try:
                ti = int(item.get("target"))
            except (TypeError, ValueError):
                ti = None
            if not ti or not (1 <= ti <= len(group)):
                log.warning("  dropped: target out of range (%r)", item.get("target"))
                continue
            topic, sub_topic = group[ti - 1]

            q = _normalise_question(item, KEYS)
            if not q:
                log.warning("  dropped [%s]: malformed options/key", topic)
                continue
            opts = dict(q["options"])
            if "E" in opts:                      # the template takes four
                if q["correct_option"] == "E":
                    log.warning("  dropped [%s]: key landed on a 5th option", topic)
                    continue
                opts.pop("E")
            if sorted(opts) != KEYS:
                log.warning("  dropped [%s]: options are %s", topic, sorted(opts))
                continue

            stem = q["question_text"]
            vec = _embed([stem])[0]
            if len(existing_vecs) and float((existing_vecs @ vec).max()) > STEM_DUP_COSINE:
                log.warning("  dropped [%s]: duplicate of an existing stem", topic)
                continue

            # grounding: the cited chunk must actually support the keyed answer
            book_ref = page = chap = None
            grounding = "ai"
            try:
                ci = int(item.get("source_chunk"))
            except (TypeError, ValueError):
                ci = 0
            if 1 <= ci <= len(blocks):
                blk = blocks[ci - 1]
                if _answer_supported(opts[q["correct_option"]], blk["content"]):
                    book_ref, page = blk["title"], blk["page"]
                    chap = chapter_number(blk.get("chapter"))
                    grounding = "book"
                else:
                    grounding = "ai_unsupported_citation"

            kw = item.get("keywords")
            if isinstance(kw, list):
                kw = ",".join(str(k) for k in kw)
            kw = ",".join(dict.fromkeys(k.strip().lower() for k in (kw or "").split(",") if k.strip()))

            rec = {
                "n": None, "line": None, "origin": "generated",
                "subject": subject, "topic": topic,
                "sub_topic": closest(sub_topic, subtopics.get(topic) or []) or sub_topic,
                "question": stem, "options": opts,
                "correct": None, "correct_option": q["correct_option"],
                "key_source": "generated", "key_confidence": None, "key_caveat": None,
                "difficulty_level": closest(item.get("difficulty_level", ""), DIFFICULTY) or "medium",
                "cognitive_level": closest(item.get("cognitive_level", ""), COGNITIVE) or "C3 (Application)",
                "explanation": shorten(item.get("explanation")),
                "keywords": kw, "book_reference": book_ref, "page_number": page,
                "chapter_number": chap, "grounding": grounding,
                "referee": {}, "evidence": [],
            }
            out.append(rec)
            existing_stems.append(stem)
            existing_vecs = np.vstack([existing_vecs, vec]) if len(existing_vecs) else vec[None, :]
            log.info("  kept [%s / %s] key=%s grounding=%s", topic, rec["sub_topic"],
                     rec["correct_option"], grounding)
    return existing_vecs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--existing", required=True)
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--subject", action="append", help="limit to these subjects")
    ap.add_argument("--target", action="append", default=[],
                    help='regenerate one item: "Subject::Topic::Sub-topic" (repeatable); '
                         "overrides PLAN")
    args = ap.parse_args()

    tax = json.loads(Path(args.taxonomy).read_text(encoding="utf-8"))
    subtopics = tax["subtopics"]
    existing = json.loads(Path(args.existing).read_text(encoding="utf-8"))
    stems = [r["question"] for r in existing if r.get("question")]
    vecs = _embed(stems) if stems else np.zeros((0, 1024), dtype=np.float32)
    log.info("avoiding %d existing stems", len(stems))

    explicit = {}
    for spec in args.target:
        parts = [p.strip() for p in spec.split("::")]
        if len(parts) != 3:
            raise SystemExit(f'--target needs "Subject::Topic::Sub-topic", got {spec!r}')
        explicit.setdefault(parts[0], []).append((parts[1], parts[2]))
    plan = explicit or {s: v for s, v in PLAN.items() if not args.subject or s in args.subject}

    out_path = Path(args.out)
    generated = []
    db = SessionLocal()
    try:
        for subject, entries in plan.items():
            if explicit:
                targets = list(entries)
            else:
                targets = []
                for topic, count in entries:
                    subs = subtopics.get(topic) or [None]
                    for j in range(count):
                        targets.append((topic, subs[j % len(subs)]))
            t0 = time.monotonic()
            vecs = generate(db, subject, targets, subtopics, stems, vecs, generated)
            log.info("%s done: %d targets in %.0fs", subject, len(targets), time.monotonic() - t0)
            out_path.write_text(json.dumps(generated, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        db.close()
    out_path.write_text(json.dumps(generated, ensure_ascii=False, indent=1), encoding="utf-8")

    want = (sum(len(v) for v in plan.values()) if explicit
            else sum(c for v in plan.values() for _, c in v))
    log.info("generated %d/%d questions -> %s", len(generated), want, out_path)
    by_g = {}
    for r in generated:
        by_g[r["grounding"]] = by_g.get(r["grounding"], 0) + 1
    log.info("grounding: %s", by_g)


if __name__ == "__main__":
    main()
