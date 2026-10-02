"""Compare cross-encoder rerankers on exam-style questions with known answer passages.

For each question the candidate pool is built once (same RRF pipeline as
retrieval.search: original + rewritten query, vector + keyword), then every
reranker orders the SAME pool. A candidate counts as correct when it matches the
question's gold predicate (keywords the answer passage must contain).

Reports per model: MRR, hit@1, hit@5 (rank of the first correct passage) and
mean rerank latency per question.

Usage (from backend/):
    python scripts/eval_rerankers.py
    python scripts/eval_rerankers.py --models cross-encoder/ms-marco-MiniLM-L-6-v2 ncbi/MedCPT-Cross-Encoder
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import joinedload  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Chunk  # noqa: E402
from app.retrieval import _focus_text, looks_like_index_page, retrieval_service, rewrite_query  # noqa: E402


def has(*words):
    return lambda t: all(w.lower() in t for w in words)


def any_of(*preds):
    return lambda t: any(p(t) for p in preds)


GOLD = [
    ("fluid of choice in hypertrophic pyloric stenosis", has("kcl", "glucose", "pylor")),
    ("paradoxical aciduria", has("paradoxical aciduria")),
    ("drug of choice for absence seizures", has("ethosuximide", "absence")),
    ("antidote for heparin overdose", has("protamine")),
    ("investigation of choice for cholesteatoma", any_of(has("cholesteatoma", " ct "), has("cholesteatoma", "hrct"),
                                                          has("cholesteatoma", "diffusion"))),
    ("Gradenigo syndrome triad", has("gradenigo", "triad")),
    ("nerve injured in fracture of surgical neck of humerus", has("axillary nerve", "surgical neck")),
    ("most common site of carcinoid tumour", any_of(has("carcinoid", "most common site"), has("carcinoid", "appendix"))),
    ("Frank-Starling law of the heart", has("frank-starling")),
    ("mechanism of action of cholera toxin", any_of(has("cholera", "camp"), has("cholera", "cyclic amp"),
                                                    has("cholera", "adenyl"))),
    ("management of DKA", has("ketoacidosis", "insulin")),
    ("most common cause of community acquired pneumonia", has("pneumoniae", "community")),
]

DEFAULT_MODELS = [
    "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "ncbi/MedCPT-Cross-Encoder",
    "BAAI/bge-reranker-base",
]


def candidate_pool(session, query: str):
    queries = [query]
    rw = rewrite_query(query)
    if rw and rw.lower() != query.lower():
        queries.append(rw)
    rrf, best_child = {}, {}
    for q in queries:
        emb = retrieval_service._embed_query(q)
        for results in (retrieval_service.vector_search(session, emb, limit=30),
                        retrieval_service.keyword_search(session, q, limit=30)):
            for rank, (child, _) in enumerate(results, 1):
                pid = child.parent_id or child.id
                rrf[pid] = rrf.get(pid, 0.0) + 1.0 / (60 + rank)
                best_child.setdefault(pid, (child.extra_metadata or {}).get("original_text") or child.content)
    top = sorted(rrf, key=rrf.get, reverse=True)[:24]
    parents = session.query(Chunk).filter(Chunk.id.in_(top)).options(joinedload(Chunk.book)).all()
    pmap = {p.id: p for p in parents if not looks_like_index_page(p.content)}
    cands = [pmap[i] for i in top if i in pmap]
    return queries, [(c, _focus_text(c.content, best_child.get(c.id))) for c in cands]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    parser.add_argument("--two-stage", nargs=2, metavar=("FIRST", "SECOND"),
                        help="also evaluate FIRST over the pool, then SECOND re-ordering FIRST's top --top-n")
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()

    from sentence_transformers import CrossEncoder

    session = SessionLocal()
    pools = []
    for q, pred in GOLD:
        queries, cands = candidate_pool(session, q)
        in_pool = any(pred(f" {t.lower()} ") for _, t in cands)
        pools.append((q, pred, queries, cands, in_pool))
        print(f"pool {len(cands):2d} cands, gold in pool: {in_pool!s:5}  {q}")

    print()
    if args.two_stage:
        first = CrossEncoder(args.two_stage[0], max_length=512)
        second = CrossEncoder(args.two_stage[1], max_length=512)
        first.predict([("warm up", "warm up")]); second.predict([("warm up", "warm up")])
        rr, h1, h5, times, ranks = 0.0, 0, 0, [], []
        for q, pred, queries, cands, in_pool in pools:
            t0 = time.monotonic()
            s1 = [float("-inf")] * len(cands)
            for qq in queries:
                s1 = [max(b, float(x)) for b, x in zip(s1, first.predict([(qq, t) for _, t in cands]))]
            order = sorted(range(len(cands)), key=lambda i: s1[i], reverse=True)
            head, tail = order[:args.top_n], order[args.top_n:]
            s2 = {i: float("-inf") for i in head}
            for qq in queries:
                for i, x in zip(head, second.predict([(qq, cands[i][1]) for i in head])):
                    s2[i] = max(s2[i], float(x))
            order = sorted(head, key=lambda i: s2[i], reverse=True) + tail
            times.append(time.monotonic() - t0)
            rank = next((r for r, i in enumerate(order, 1) if pred(f" {cands[i][1].lower()} ")), None)
            ranks.append(rank)
            if rank:
                rr += 1.0 / rank
                h1 += rank == 1
                h5 += rank <= 5
        n = len(pools)
        print(f"{'two-stage top' + str(args.top_n):40} MRR {rr / n:.3f}  hit@1 {h1}/{n}  hit@5 {h5}/{n}  "
              f"rerank {sum(times) / n:.2f}s/question  ranks={ranks}")
    for name in args.models:
        model = CrossEncoder(name, max_length=512)
        model.predict([("warm up", "warm up")])
        rr, h1, h5, times, ranks = 0.0, 0, 0, [], []
        for q, pred, queries, cands, in_pool in pools:
            t0 = time.monotonic()
            best = [float("-inf")] * len(cands)
            for qq in queries:
                scores = model.predict([(qq, t) for _, t in cands])
                best = [max(b, float(s)) for b, s in zip(best, scores)]
            times.append(time.monotonic() - t0)
            order = sorted(range(len(cands)), key=lambda i: best[i], reverse=True)
            rank = next((r for r, i in enumerate(order, 1) if pred(f" {cands[i][1].lower()} ")), None)
            ranks.append(rank)
            if rank:
                rr += 1.0 / rank
                h1 += rank == 1
                h5 += rank <= 5
        n = len(pools)
        print(f"{name:40} MRR {rr / n:.3f}  hit@1 {h1}/{n}  hit@5 {h5}/{n}  "
              f"rerank {sum(times) / n:.2f}s/question  ranks={ranks}")
    session.close()


if __name__ == "__main__":
    main()
