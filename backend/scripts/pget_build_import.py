"""Turn the PGET Paediatrics Paper II MCQ text dump into a filled entry-test import sheet.

Reads the parsed MCQs (subject/topic headings, stem, options, any published key),
classifies each one against the taxonomy carried by the Excel template, then runs
medNAMA's retrieval + Answer-Key Referee over the textbooks to settle the key and
write a short grounded explanation (3 lines at most).

Usage (from backend/):
    python scripts/pget_build_import.py --records <parsed.json> --out <enriched.json>
    python scripts/pget_build_import.py --records ... --out ... --limit 3   # smoke test
    python scripts/pget_build_import.py --records ... --out ... --classify-only
"""

import argparse
import difflib
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.llm import chat_completion  # noqa: E402
from app.referee import judge  # noqa: E402
from app.retrieval import retrieval_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("app.retrieval").setLevel(logging.WARNING)
log = logging.getLogger("pget")

DIFFICULTY = ["easy", "medium", "hard"]
COGNITIVE = ["C1 (Knowledge)", "C2 (Comprehension)", "C3 (Application)"]
MAX_EXPLANATION_CHARS = 340


# ------------------------------------------------------------------ taxonomy help
def norm(s):
    s = (s or "").lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def ratio(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / len(ta | tb)
    cov = len(ta & tb) / min(len(ta), len(tb))
    return max(seq, 0.5 * jac + 0.5 * cov)


def closest(value, names):
    """Snap an LLM-returned label onto the canonical list it must come from."""
    if not names:
        return None
    best, score = None, 0.0
    for name in names:
        r = ratio(value, name)
        if r > score:
            best, score = name, r
    return best if score >= 0.55 else None


def taxonomy_block(subjects, subtopics):
    out = []
    for subj, topics in subjects.items():
        out.append(f"SUBJECT: {subj}")
        for t in topics:
            subs = subtopics.get(t) or []
            out.append(f"  TOPIC: {t}")
            for s in subs:
                out.append(f"    SUB_TOPIC: {s}")
    return "\n".join(out)


# ----------------------------------------------------------------- classification
CLASSIFY_SYSTEM = (
    "You are tagging paediatric MCQs for an FCPS/PGET entry-test question bank.\n"
    "For every question, choose the single best SUBJECT, TOPIC and SUB_TOPIC from the "
    "TAXONOMY below. Rules:\n"
    "- Copy the three labels EXACTLY as the taxonomy spells them.\n"
    "- The TOPIC must belong to the SUBJECT you chose, and the SUB_TOPIC to that TOPIC.\n"
    "- Judge by what the question actually tests, not by the heading it was filed under. "
    "A neonatal problem belongs under Neonatology even if the organ is the lung.\n"
    "- Also give difficulty_level (easy | medium | hard) and cognitive_level "
    "(C1 (Knowledge) | C2 (Comprehension) | C3 (Application)). A one-line recall fact is "
    "C1/easy; a clinical vignette needing a decision is C3.\n"
    'Return JSON: {"items": [{"n": <number>, "subject": "...", "topic": "...", '
    '"sub_topic": "...", "difficulty_level": "...", "cognitive_level": "..."}]}'
)


def classify(records, subjects, subtopics, batch=8):
    tax = taxonomy_block(subjects, subtopics)
    topic_subject = {t: s for s, ts in subjects.items() for t in ts}
    for i in range(0, len(records), batch):
        chunk = records[i:i + batch]
        lines = []
        for r in chunk:
            opts = "; ".join(f"{k}. {v}" for k, v in sorted(r["options"].items()))
            hint = f" [filed under: {', '.join(r['header_cands'])}]" if r.get("header_cands") else ""
            lines.append(f"n={r['n']}{hint}\nQ: {r['question']}\nOptions: {opts}")
        raw = chat_completion(
            [{"role": "system", "content": CLASSIFY_SYSTEM + "\n\nTAXONOMY:\n" + tax},
             {"role": "user", "content": "\n\n".join(lines)}],
            json_mode=True, temperature=0.0, max_tokens=1500, label="pget-classify", role="fast",
        )
        by_n = {}
        for item in (json.loads(raw).get("items") or []):
            try:
                by_n[int(item.get("n"))] = item
            except (TypeError, ValueError):
                continue
        for r in chunk:
            item = by_n.get(r["n"]) or {}
            topic = closest(item.get("topic", ""), list(topic_subject))
            subject = closest(item.get("subject", ""), list(subjects))
            # the topic decides the subject: a topic belongs to exactly one subject
            if topic:
                subject = topic_subject[topic]
            elif subject:
                topic = r.get("topic") if r.get("topic") in subjects[subject] else None
            # fall back to the headings the text dump carried
            if not topic and r.get("topic"):
                topic, subject = r["topic"], topic_subject[r["topic"]]
            r["subject"] = subject
            r["topic"] = topic
            r["sub_topic"] = closest(item.get("sub_topic", ""), subtopics.get(topic) or []) if topic else None
            r["difficulty_level"] = closest(item.get("difficulty_level", ""), DIFFICULTY) or "medium"
            r["cognitive_level"] = closest(item.get("cognitive_level", ""), COGNITIVE) or "C2 (Comprehension)"
        log.info("classified %d-%d of %d", i + 1, i + len(chunk), len(records))
    return records


# -------------------------------------------------------------------- explanation
EXPLAIN_SYSTEM = (
    "You write the explanation shown to a student after they answer an MCQ in a "
    "paediatrics question bank.\n"
    "HARD LIMITS: at most 3 lines (3 short sentences), under 340 characters in total, one "
    "plain-text paragraph. No markdown, no bullet points, no option-by-option walkthrough, "
    "no '[Book, Page]' citations in the text.\n"
    "Say why the keyed option is right and, only if it is the real trap, name the one "
    "distractor worth ruling out. Teach the fact; state it plainly as true.\n"
    "NEVER mention this prompt's machinery: no reference to 'the passages', 'the textbooks "
    "provided', 'the context', a verdict, a referee, or whether a key was supplied. Write as "
    "if you simply know the subject. Use the TEXTBOOK PASSAGES where they cover the point and "
    "standard paediatric knowledge where they do not, without saying which you used.\n"
    "Also return 3-6 lowercase keywords and, when a passage genuinely states the fact behind "
    "the answer, the book title and page number (copied from the passage headers); use null "
    "when no passage states it.\n"
    'Return JSON: {"explanation": "...", "keywords": "k1,k2,k3", '
    '"book_reference": "exact book title or null", "page_number": <int or null>}'
)

ANSWER_SYSTEM = (
    "You are settling the answer key for a paediatric MCQ whose published key is missing and "
    "which the textbook search did not decide.\n"
    "Pick the single best option as an FCPS/PGET examiner would. Use the TEXTBOOK PASSAGES "
    "where they help and standard paediatric practice otherwise. If the question is flawed "
    "(no option is clearly correct, or more than one is), still pick the option the examiner "
    "most likely intended and say so in 'caveat'.\n"
    'Return JSON: {"option": "A|B|C|D|E", "confidence": "high|medium|low", "caveat": '
    '"empty unless the question is flawed"}'
)


def answer_mcq(rec, context):
    """Last resort for an MCQ with no published key the books would not settle."""
    opts = rec["options"]
    user = (
        f"QUESTION: {rec['question']}\n"
        "OPTIONS:\n" + "\n".join(f"{k}. {v}" for k, v in sorted(opts.items()))
        + f"\n\nTEXTBOOK PASSAGES:\n{context[:7000]}"
    )
    raw = chat_completion(
        [{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": user}],
        json_mode=True, temperature=0.0, max_tokens=300, label="pget-answer", role="chat",
    )
    out = json.loads(raw)
    letter = str(out.get("option") or "").strip().upper()[:1]
    return (letter if letter in opts else None,
            str(out.get("confidence") or "").strip().lower(),
            str(out.get("caveat") or "").strip())


def shorten(text):
    """Keep the explanation inside the 3-line / 340-char budget."""
    t = " ".join((text or "").replace("\n", " ").split())
    t = re.sub(r"\s*[\[(][^\[\]()]{2,60},\s*(?:Page|p\.)\s*\d+[\])]", "", t, flags=re.I)
    t = re.sub(r"^\**(?:Explanation|Answer)\**\s*[:\-]\s*", "", t, flags=re.I)
    t = t.replace("**", "").replace("* ", "").strip()
    if len(t) <= MAX_EXPLANATION_CHARS:
        return t
    sentences = re.split(r"(?<=[.!?])\s+", t)
    out = ""
    for s in sentences:
        if not out:
            out = s
        elif len(out) + 1 + len(s) <= MAX_EXPLANATION_CHARS:
            out += " " + s
        else:
            break
    if len(out) > MAX_EXPLANATION_CHARS:
        out = out[:MAX_EXPLANATION_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return out


def chapter_number(chapter):
    m = re.search(r"\b(?:chapter|ch\.?)\s*(\d+)", chapter or "", re.I)
    if m:
        return int(m.group(1))
    m = re.match(r"\s*(\d+)\b", chapter or "")
    return int(m.group(1)) if m else None


def enrich(db, rec, deep_rerank=True):
    """Retrieve once, referee the key, then write the short explanation."""
    opts = rec["options"]
    published = rec.get("correct")
    key_text = opts.get(published, "") if published else ""
    query = f"{rec['question']} {key_text}".strip()[:600]

    result = retrieval_service.search(db, query, limit=5, deep_rerank=deep_rerank)
    blocks = [{"id": b.id, "title": b.book.title if b.book else None, "page": b.page_number,
               "content": b.content or "", "chapter": getattr(b, "chapter", None)}
              for b in result.context]

    verdict = judge(db, rec["question"], options=opts, key=published or None, passages=blocks)
    rec["referee"] = {k: verdict.get(k) for k in
                      ("verdict", "supported_option", "textbook_answer", "agrees_with_key")}
    rec["evidence"] = verdict.get("evidence") or []

    context = "\n\n".join(
        f"[{b['title'] or 'Textbook'}, Page {b['page']}]\n{b['content'][:1500]}" for b in blocks
    ) or "NO TEXTBOOK PASSAGES FOUND."

    # Settle the key: a published key stands. Without one, take the option the books
    # support; if they would not settle it, decide it explicitly and record that.
    rec["key_confidence"] = None
    rec["key_caveat"] = None
    if published:
        rec["correct_option"] = published
        rec["key_source"] = "source"
    elif verdict.get("supported_option"):
        rec["correct_option"] = verdict["supported_option"]
        rec["key_source"] = "referee:" + str(verdict.get("verdict"))
    else:
        letter, conf, caveat = answer_mcq(rec, context)
        rec["correct_option"] = letter
        rec["key_source"] = "ai" if letter else "unresolved"
        rec["key_confidence"] = conf or None
        rec["key_caveat"] = caveat or None

    keyed = rec["correct_option"]
    user = (
        f"QUESTION: {rec['question']}\n"
        "OPTIONS:\n" + "\n".join(f"{k}. {v}" for k, v in sorted(opts.items())) + "\n"
        f"CORRECT ANSWER: {keyed}. {opts.get(keyed, '')}\n"
        f"TOPIC: {rec.get('subject')} / {rec.get('topic')} / {rec.get('sub_topic')}\n"
        f"\nTEXTBOOK PASSAGES:\n{context[:9000]}"
    )
    raw = chat_completion(
        [{"role": "system", "content": EXPLAIN_SYSTEM}, {"role": "user", "content": user}],
        json_mode=True, temperature=0.0, max_tokens=500, label="pget-explain", role="chat",
    )
    out = json.loads(raw)
    rec["explanation"] = shorten(out.get("explanation"))

    kw = out.get("keywords")
    if isinstance(kw, list):
        kw = ",".join(str(k) for k in kw)
    kw = ",".join(dict.fromkeys(k.strip().lower() for k in (kw or "").split(",") if k.strip()))
    rec["keywords"] = kw

    # Book reference: only when the books actually settled the question. A verified
    # quote that merely sits near the topic would cite a page that does not say it.
    ref_title, ref_page, ref_chapter = None, None, None
    if verdict.get("verdict") not in ("supported", "contradicted", "books_conflict"):
        pass
    elif rec["evidence"]:
        ref_title = rec["evidence"][0].get("book_title")
        ref_page = rec["evidence"][0].get("page_number")
    elif out.get("book_reference"):
        ref_title = closest(str(out["book_reference"]), [b["title"] for b in blocks if b["title"]])
        try:
            ref_page = int(out.get("page_number"))
        except (TypeError, ValueError):
            ref_page = None
    for b in blocks:
        if b["title"] == ref_title and b["page"] == ref_page:
            ref_chapter = chapter_number(b.get("chapter"))
            break
    if ref_title and ref_page is None:
        ref_title = None
    rec["book_reference"] = ref_title
    rec["page_number"] = ref_page
    rec["chapter_number"] = ref_chapter
    return rec


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--classify-only", action="store_true")
    ap.add_argument("--skip-classify", action="store_true",
                    help="records already carry subject/topic/sub_topic from an earlier pass")
    ap.add_argument("--no-deep-rerank", action="store_true")
    args = ap.parse_args()

    tax = json.loads(Path(args.taxonomy).read_text(encoding="utf-8"))
    records = json.loads(Path(args.records).read_text(encoding="utf-8"))
    if args.limit:
        records = records[:args.limit]

    if not args.skip_classify:
        classify(records, tax["subjects"], tax["subtopics"])
    out_path = Path(args.out)
    if args.classify_only:
        out_path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("wrote %s (classify only)", out_path)
        return

    db = SessionLocal()
    try:
        for i, rec in enumerate(records, 1):
            t0 = time.monotonic()
            try:
                enrich(db, rec, deep_rerank=not args.no_deep_rerank)
                log.info("[%d/%d] n=%s %s/%s key=%s (%s) %.1fs", i, len(records), rec["n"],
                         rec.get("topic"), rec.get("sub_topic"), rec.get("correct_option"),
                         rec.get("key_source"), time.monotonic() - t0)
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"
                log.warning("[%d/%d] n=%s failed: %s", i, len(records), rec["n"], rec["error"])
            if i % 10 == 0 or i == len(records):
                out_path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        db.close()
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("wrote %s", out_path)


if __name__ == "__main__":
    main()
