"""Link past-paper questions that recall the same exam question (across Radiant and MediVerse).

Nothing is merged: each archive keeps its own row, wording and key. Rows believed to be the same
question share mcqs.recall_group, so a session shows one of them, "seen" covers all of them, and
rank_past_papers.py counts every year any of them was asked in.

Similar stems alone do not make two questions the same: recalls often share a generic stem
("Most common site of ...") with a different answer. A pair is the same question when the stem
embeddings are close and either
  - the correct answers agree (past_papers.answers_agree: spelling, plural, initials and acronyms
    tolerated; opposites such as hypo-/hyper- never), with cosine >= 0.80 when the answer is rare in
    the archives, 0.85 when it keys up to 10 questions and 0.89 beyond (a common key like
    "Testosterone" answers many different questions on one topic), or
  - the option sets overlap (>= 60% of the smaller set) and cosine >= --strict-cos (0.91; at 0.89-0.90
    the same vignette often asks a different thing, e.g. troponin vs "within 2 hours" myoglobin).
The second kind with different keys is a key conflict: both rows get tag flag=key-conflict, and the
quiz offers "Check with textbooks" (Answer-Key Referee).

Groups use complete linkage, strongest pair first: two groups merge only when every cross pair is a
match, and never past --max-group. (Plain union-find chained A~B~C, putting eight different
testosterone questions in one group.) Numpy only (no model), so it runs on the NAS:
    docker exec -w /app/backend mednama-backend python scripts/link_recalls.py [--dry-run]
Then re-run rank_past_papers.py for each exam.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.past_papers import answer_norm as norm, answers_agree  # noqa: E402

CATEGORIES = ("Past papers · FCPS Part 1", "Past papers · FCPS Part 1 (Dentistry)")


def fetch_vectors(db, sql: str, params: dict, dim: int = 1024) -> tuple[list[tuple], np.ndarray]:
    """Stream rows whose last column is stem_embedding::text; returns (the other columns per row, an
    L2-normalised float32 matrix). Streaming keeps the NAS (~1.3 GB free next to the app) out of trouble:
    ~140 MB for 34k questions instead of every vector as Python floats at once."""
    # Statement-level option: set on the connection it would also turn later UPDATEs into cursors.
    res = db.execute(text(sql).execution_options(stream_results=True, max_row_buffer=1000), params)
    meta: list[tuple] = []
    chunks, buf = [], []
    for row in res:
        meta.append(tuple(row[:-1]))
        buf.append(np.asarray(json.loads(row[-1]), dtype=np.float32))
        if len(buf) == 2000:
            chunks.append(np.stack(buf))
            buf = []
    if buf:
        chunks.append(np.stack(buf))
    E = np.concatenate(chunks) if chunks else np.zeros((0, dim), dtype=np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    return meta, E


def option_overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-cos", type=float, default=0.80)
    parser.add_argument("--strict-cos", type=float, default=0.91)
    parser.add_argument("--max-group", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    missing = db.execute(text("SELECT count(*) FROM mcqs WHERE source LIKE 'pastpaper:%' AND main_category = ANY(:c) "
                              "AND stem_embedding IS NULL"), {"c": list(CATEGORIES)}).scalar()
    rows, E = fetch_vectors(db, (
        "SELECT id, source, options, correct_option, stem_embedding::text FROM mcqs "
        "WHERE source LIKE 'pastpaper:%' AND main_category = ANY(:c) AND stem_embedding IS NOT NULL ORDER BY id"),
        {"c": list(CATEGORIES)})
    print(f"{len(rows)} past-paper questions with embeddings ({missing} without, left out)")
    ids = [r[0] for r in rows]
    src = [r[1].split(":", 1)[1] for r in rows]
    ans = [norm((r[2] or {}).get(r[3])) for r in rows]
    opts = [{norm(v) for v in (r[2] or {}).values() if norm(v)} for r in rows]

    # A common key ("Testosterone", "Thiamine") answers many different questions on one topic, so the
    # same-answer rule needs closer stems the more often the answer occurs.
    freq = collections.Counter(a for a in ans if a)

    def match(i: int, j: int, c: float) -> str | None:
        if answers_agree(ans[i], ans[j]):
            n = max(freq[ans[i]], freq[ans[j]])
            need = args.min_cos if n <= 3 else 0.85 if n <= 10 else 0.89
            return "same" if c >= need else None
        if c >= args.strict_cos and option_overlap(opts[i], opts[j]) >= 0.6:
            return "conflict"
        return None

    pairs = []   # (cos, i, j, conflict)
    for start in range(0, len(ids), 500):
        S = E[start:start + 500] @ E.T
        for a in range(S.shape[0]):
            i = start + a
            for j in np.nonzero(S[a] >= args.min_cos)[0]:
                j = int(j)
                if j <= i:
                    continue
                kind = match(i, j, float(S[a, j]))
                if kind:
                    pairs.append((float(S[a, j]), i, j, kind == "conflict"))
    pairs.sort(key=lambda p: -p[0])

    # Complete linkage: two groups merge only if EVERY cross pair is itself a match. Plain union-find
    # chained A~B~C into one "question" (eight different testosterone questions in one group).
    group_of = list(range(len(ids)))
    members: dict[int, list[int]] = {i: [i] for i in range(len(ids))}
    kept, capped, chained = [], 0, 0
    for p in pairs:
        _, i, j, _ = p
        gi, gj = group_of[i], group_of[j]
        if gi == gj:
            kept.append(p)
            continue
        A, B = members[gi], members[gj]
        if len(A) + len(B) > args.max_group:
            capped += 1
            continue
        if not all(match(x, y, float(E[x] @ E[y])) for x in A for y in B):
            chained += 1
            continue
        if len(A) < len(B):
            gi, gj, A, B = gj, gi, B, A
        A.extend(B)
        for y in B:
            group_of[y] = gi
        del members[gj]
        kept.append(p)
    groups = [m for m in members.values() if len(m) > 1]
    conflict = {i for c, i, j, bad in kept if bad for i in (i, j)}
    cross = sum(1 for m in groups if len({src[i] for i in m}) > 1)
    by_pair = collections.Counter("+".join(sorted((src[i], src[j]))) for _, i, j, _ in kept)

    print(f"linked pairs {len(kept)} (by source {dict(by_pair)}); refused: group cap {capped}, "
          f"would chain unrelated questions {chained}")
    print(f"groups {len(groups)} covering {sum(len(m) for m in groups)} questions; spanning both archives {cross}")
    print("group sizes:", dict(sorted(collections.Counter(len(m) for m in groups).items())))
    print(f"key conflicts: {sum(1 for p in kept if p[3])} pairs, {len(conflict)} questions")
    rng = np.random.default_rng(7)
    cross_pairs = [p for p in kept if src[p[1]] != src[p[2]]]
    samples = {label: [sample[int(k)] for k in rng.permutation(len(sample))[:6]] if sample else []
               for label, sample in (("same question, two archives", cross_pairs),
                                     ("keys disagree", [p for p in kept if p[3]]))}
    shown = [ids[x] for s in samples.values() for _, i, j, _ in s for x in (i, j)]
    stems = {i: " ".join(q.split()) for i, q in db.execute(
        text("SELECT id, question_text FROM mcqs WHERE id = ANY(:i)"), {"i": shown or [-1]})}
    for label, sample in samples.items():
        print(f"\n--- sample: {label}")
        for c, i, j, _ in sample:
            print(f"  cos {c:.2f}\n    [{src[i]}] {stems[ids[i]][:110]}  => {ans[i][:40]}\n"
                  f"    [{src[j]}] {stems[ids[j]][:110]}  => {ans[j][:40]}")
    if args.dry_run:
        return

    db.execute(text("UPDATE mcqs SET recall_group = NULL WHERE id = ANY(:ids) AND recall_group IS NOT NULL"), {"ids": ids})
    gid, gids = [], []
    for m in groups:
        root = min(ids[i] for i in m)   # stable id: the oldest member
        gid += [ids[i] for i in m]
        gids += [root] * len(m)
    for k in range(0, len(gid), 5000):
        db.execute(text("UPDATE mcqs SET recall_group = v.g FROM (SELECT unnest(CAST(:ids AS int[])) AS id, "
                        "unnest(CAST(:gs AS int[])) AS g) v WHERE mcqs.id = v.id"),
                   {"ids": gid[k:k + 5000], "gs": gids[k:k + 5000]})
    db.execute(text("DELETE FROM mcq_tags WHERE axis = 'flag' AND label = 'key-conflict' AND mcq_id = ANY(:ids)"),
               {"ids": ids})
    if conflict:
        db.execute(text("INSERT INTO mcq_tags (mcq_id, axis, label) SELECT unnest(CAST(:ids AS int[])), 'flag', "
                        "'key-conflict' ON CONFLICT DO NOTHING"), {"ids": [ids[i] for i in conflict]})
    db.commit()
    print("\nrecall groups written. Now re-run rank_past_papers.py for each exam.")


if __name__ == "__main__":
    main()
