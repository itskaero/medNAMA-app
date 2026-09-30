"""Tag every FCPS Part 1 practice question with the shared Subject -> Topic list (app/taxonomy.py).

Questions: the Paper 1 bank and the FCPS Part 1 past papers (twists and hardened versions inherit from
their original). For each question:

  subject  its sub_category when that is a subject; otherwise ("Mixed", "Minor Subjects") chosen by the AI
  topic    the canonical topic its source labels mean (past-paper topic tags, then the bank's topic);
           otherwise chosen by the AI from that subject's topic list (single-topic subjects need no choice)

The AI sees the statement and the correct answer and must pick from the list (or "none"). Its choices are
cached by question text in data/taxonomy_ai.json, so a rerun only asks about new questions and the NAS can
apply the PC's choices without AI calls (question ids differ between the two databases):

    python scripts/tag_taxonomy.py                  # PC: ask the AI, report coverage, samples (dry run)
    python scripts/tag_taxonomy.py --apply
    python scripts/tag_taxonomy.py --no-ai --apply  # NAS: source labels + the cached AI choices only

--method knn places by the 15 most similar questions instead (no AI; less accurate: vignettes that look
alike are often different topics). Tags: axes fcps_subject / fcps_topic; AI- or neighbour-placed ones also
get fcps_auto = subject|topic.
"""

import argparse
import collections
import hashlib
import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import MCQ  # noqa: E402
from app.taxonomy import SUBJECTS, canonical_topic, topics_of  # noqa: E402

SOURCES = ("Paper 1 · Basic sciences", "Past papers · FCPS Part 1")
CACHE = BACKEND_DIR.parent / "data" / "taxonomy_ai.json"
BATCH = 25
K = 15


def key_of(stem: str) -> str:
    return hashlib.sha1(" ".join((stem or "").lower().split()).encode()).hexdigest()


# ─── AI placement ───────────────────────────────────────────────────────────

def _ask(batch: list[dict], subject: str | None) -> dict[str, dict]:
    from app.llm import chat_completion, llm_configured

    role = "fast" if llm_configured("fast") else "chat"
    if subject:
        menu = f"Subject: {subject}. Topics: {json.dumps(topics_of(subject))}."
        want = '{"items": [{"id": "q1", "topic": "<one topic from the list, or none>"}]}'
    else:
        menu = "Subjects and their topics:\n" + "\n".join(f"- {s}: {json.dumps(topics_of(s))}" for s in SUBJECTS)
        want = '{"items": [{"id": "q1", "subject": "<one subject>", "topic": "<one of its topics, or none>"}]}'
    lines = "\n".join(f"[q{i}] {q['stem'][:420]} (answer: {q['answer'][:80]})" for i, q in enumerate(batch, 1))
    messages = [
        {"role": "system", "content": "You sort FCPS Part 1 (basic medical sciences) exam questions into a fixed "
                                      "list of topics. Judge by what the question tests (the answer), not by the "
                                      "clinical setting of the vignette. Use only names from the list. JSON only."},
        {"role": "user", "content": f"{menu}\n\nQuestions:\n{lines}\n\nReturn {want} with one item per question."},
    ]
    raw = chat_completion(messages, json_mode=True, temperature=0.0, max_tokens=1600, label="taxonomy", role=role)
    out = {}
    for item in (json.loads(raw) or {}).get("items", []):
        qid = str(item.get("id", ""))
        if not qid.startswith("q") or not qid[1:].isdigit() or not 1 <= int(qid[1:]) <= len(batch):
            continue
        q = batch[int(qid[1:]) - 1]
        s = subject or item.get("subject")
        t = item.get("topic")
        if s not in SUBJECTS:
            continue
        out[q["key"]] = {"subject": s, "topic": t if t in topics_of(s) else None}
    return out


def ai_place(todo: list[dict], subject: str | None, cache: dict, workers: int = 4) -> None:
    """Fill cache[key] = {subject, topic} for questions not cached yet."""
    pending = [q for q in todo if q["key"] not in cache]
    batches = [pending[i:i + BATCH] for i in range(0, len(pending), BATCH)]
    lock = threading.Lock()
    done = [0]

    def run(b):
        try:
            got = _ask(b, subject)
        except Exception as e:
            print(f"    batch failed: {str(e)[:100]}")
            return
        with lock:
            cache.update(got)
            done[0] += 1
            if done[0] % 20 == 0:
                CACHE.write_text(json.dumps(cache), encoding="utf-8")
                print(f"    {subject or 'subject+topic'}: {done[0]}/{len(batches)} batches")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(run, batches))
    CACHE.write_text(json.dumps(cache), encoding="utf-8")


# ─── neighbour placement (fallback) ─────────────────────────────────────────

def knn_place(rows, subject, topic, auto) -> None:
    import numpy as np
    import torch
    emb = {r.id: np.asarray(r.stem_embedding, dtype=np.float32) for r in rows if r.stem_embedding is not None}
    for s in SUBJECTS:
        base = [m for m in emb if subject.get(m) == s and topic.get(m)]
        todo = [m for m in emb if subject.get(m) == s and not topic.get(m)]
        if not todo or len(base) < K:
            continue
        B = torch.nn.functional.normalize(torch.tensor(np.stack([emb[m] for m in base])), dim=1)
        Q = torch.nn.functional.normalize(torch.tensor(np.stack([emb[m] for m in todo])), dim=1)
        sims, idx = torch.topk(Q @ B.T, K, dim=1)
        for row, m in enumerate(todo):
            w = collections.Counter()
            for s_, j in zip(sims[row].tolist(), idx[row].tolist()):
                w[topic[base[j]]] += max(s_, 0.0)
            lab, top = w.most_common(1)[0]
            if top / max(sum(w.values()), 1e-9) >= 0.5 and sims[row][0] >= 0.6:
                topic[m] = lab
                auto[m].add("topic")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-ai", action="store_true", help="use only source labels and cached AI choices")
    ap.add_argument("--method", choices=("ai", "knn"), default="ai")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    db = SessionLocal()
    cols = [MCQ.id, MCQ.sub_category, MCQ.topic, MCQ.question_text, MCQ.options, MCQ.correct_option]
    if args.method == "knn":
        cols.append(MCQ.stem_embedding)
    rows = db.query(*cols).filter(MCQ.main_category.in_(SOURCES), MCQ.twist_of.is_(None),
                                  MCQ.status == "ready").all()
    tags = collections.defaultdict(list)
    for mid, label in db.execute(text("SELECT mcq_id, label FROM mcq_tags WHERE axis = 'topic'")):
        tags[mid].append(label)
    print(f"{len(rows)} questions from {', '.join(SOURCES)}")
    q = {r.id: {"key": key_of(r.question_text), "stem": " ".join((r.question_text or "").split()),
                "answer": str((r.options or {}).get(r.correct_option) or "")} for r in rows}
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    subject = {r.id: (r.sub_category if r.sub_category in SUBJECTS else None) for r in rows}
    topic: dict[int, str | None] = {}
    auto: dict[int, set[str]] = collections.defaultdict(set)

    # Source labels first.
    for r in rows:
        s = subject[r.id]
        if not s:
            topic[r.id] = None
        elif len(topics_of(s)) == 1:
            topic[r.id] = topics_of(s)[0]
        else:
            t = next((canonical_topic(s, lab) for lab in tags.get(r.id, []) if canonical_topic(s, lab)), None)
            topic[r.id] = t or canonical_topic(s, r.topic)

    if args.method == "knn":
        knn_place(rows, subject, topic, auto)
    else:
        no_subject = [q[m] for m in q if not subject[m]]
        by_subject = {s: [q[m] for m in q if subject[m] == s and not topic[m]] for s in SUBJECTS}
        if not args.no_ai:
            print(f"asking the AI: {len(no_subject)} without a subject, "
                  f"{sum(len(v) for v in by_subject.values())} without a topic")
            if no_subject:
                ai_place(no_subject, None, cache, args.workers)
            for s, todo in by_subject.items():
                if todo and len(topics_of(s)) > 1:
                    ai_place(todo, s, cache, args.workers)
        for m in q:
            hit = cache.get(q[m]["key"])
            if not hit:
                continue
            if not subject[m] and hit.get("subject") in SUBJECTS:
                subject[m] = hit["subject"]
                auto[m].add("subject")
            if subject[m] and not topic[m]:
                t = hit.get("topic") if hit.get("subject") == subject[m] or not hit.get("subject") else None
                if len(topics_of(subject[m])) == 1:
                    t = topics_of(subject[m])[0]
                if t in topics_of(subject[m]):
                    topic[m] = t
                    auto[m].add("topic")

    ids = list(q)
    placed = sum(1 for m in ids if subject[m] and topic[m])
    print(f"\nplaced {placed}/{len(ids)} ({placed / max(1, len(ids)):.0%}); "
          f"subject placed {sum(1 for a in auto.values() if 'subject' in a)}, "
          f"topic placed {sum(1 for a in auto.values() if 'topic' in a)}; "
          f"no subject {sum(1 for m in ids if not subject[m])}, "
          f"subject but no topic {sum(1 for m in ids if subject[m] and not topic[m])}")
    by = collections.Counter((subject[m], topic[m]) for m in ids if subject[m])
    for s in SUBJECTS:
        parts = [f"{t} {by[(s, t)]}" for t in topics_of(s)]
        print(f"  {s:28} {sum(v for (ss, _), v in by.items() if ss == s):>6}  " + " · ".join(parts)
              + (f" · (none) {by[(s, None)]}" if by[(s, None)] else ""))
    placed_auto = [m for m in ids if "topic" in auto[m]]
    print("\nSamples placed by the AI:" if args.method == "ai" else "\nSamples placed by neighbours:")
    for m in random.Random(1).sample(placed_auto, min(15, len(placed_auto))):
        print(f"  [{subject[m]} > {topic[m]}] {q[m]['stem'][:110]} (answer: {q[m]['answer'][:40]})")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write fcps_subject / fcps_topic tags.")
        return
    db.execute(text("DELETE FROM mcq_tags WHERE axis IN ('fcps_subject', 'fcps_topic', 'fcps_auto') "
                    "AND mcq_id = ANY(:i)"), {"i": ids})
    values = []
    for m in ids:
        if subject[m]:
            values.append({"m": m, "a": "fcps_subject", "l": subject[m]})
        if topic[m]:
            values.append({"m": m, "a": "fcps_topic", "l": topic[m]})
        for what in auto[m]:
            values.append({"m": m, "a": "fcps_auto", "l": what})
    for i in range(0, len(values), 5000):
        db.execute(text("INSERT INTO mcq_tags (mcq_id, axis, label) VALUES (:m, :a, :l) ON CONFLICT DO NOTHING"),
                   values[i:i + 5000])
    db.commit()
    print(f"\nwrote {len(values)} tags")


if __name__ == "__main__":
    main()
