# MedNAMA reranker benchmark

Generated 2026-10-07 16:44:50 by `labs/evaluation/reranker/run.py evaluate`. Tables are generated; the prose comes from `analysis_notes.md`.

> **Status: provisional ground truth.** All 104 items are labelled by their gold predicates (the terms the answer passage must contain), checked against the corpus; none has human-verified chunk ids yet. Rankings between models are meaningful; absolute numbers will move once `run.py sheet` verification is done.

## Executive summary

**No reranker beats production clearly enough, on this data, to justify a change on quality grounds alone.
The one change the data does support is about cost: production's MedCPT stage adds no measurable quality.**

* **Retrieval is not the bottleneck.** For 66 of 67 questions a relevant passage is in the top 50, and for 64
  even in production's ~16-candidate pool. The single retrieval miss (q033, the pyloric stenosis fluid) has its
  answer in one passage in the whole 25-book library.
* **Production already finds the answer.** Current (ms-marco → MedCPT top 8) puts a relevant passage in the
  **top 3 for every answerable question** (Hit@3 = 1.000; 0.970 on its real ~16-candidate pool). The LLM
  receives 5-6 blocks, so it sees the answer. Differences between rerankers are about *which* relevant passage
  comes first, not whether the answer reaches the model.
* **MedCPT is not better for medical textbooks here.** Alone, it has the lowest MRR of the four
  cross-encoders: 0.811 vs production's 0.879 (1 question better, 11 worse; 95% interval excludes zero). As
  production's second stage it adds nothing measurable over ms-marco alone (+0.016 MRR, 7 better / 6 worse).
* **BGE v2-m3 ranks best at position 1** (Hit@1 0.894 vs 0.773; MRR +0.058 vs production, 12 better / 5
  worse); over 50 candidates the interval still touches zero (−0.006 to +0.129), but on production's real
  ~16-candidate pool it is significantly better than production (+0.075, 12 better / 3 worse, interval +0.013
  to +0.139). It needs 1.6 GB of RAM and about 12 s per question on a desktop CPU even as a second stage: it
  cannot run on the NAS.
* **The shape that works is a cheap first stage plus a better second stage.** ms-marco → BGE base top 8
  significantly beats ms-marco alone (+0.074 MRR, 12/5, interval +0.015 to +0.140) where ms-marco → MedCPT does
  not. Against production itself it is borderline (+0.057, interval −0.005 to +0.124), and on production's real
  ~16-candidate pool the gain shrinks to +0.025 (not significant).
* **Textbook metadata does not help** any model significantly; it slightly hurts production's pipeline.
* All labels are provisional (term predicates, audited once by an AI reader, not by a clinician).

Two errors were found and fixed during the run, and both are documented because they changed conclusions:
a sigmoid-saturation bug in the benchmark's own scoring (see "Performance"), and 12 gold predicates that
rejected passages which plainly answer (see "Failure analysis").

## Setup

* Corpus: the production dev database (25 ready books). Pool: top 50 parents by production's RRF over vector + keyword lists of 60 rows for the question and its recorded LLM rewrite, plus each list's top 3 (production reranks the top 16 plus those heads).
* Items: 67 questions (66 with a relevant passage in the pool), 18 confusion tests, 19 terminology tests.
* Every model scores each candidate against the question and the rewrite and keeps the max, as production does. fp32, max_length 512, batch 32, eval mode, seed 0.
* Hardware: NVIDIA GeForce RTX 2060 (CUDA 13.0); CPU timings on this PC's CPU. Python 3.14.2, torch 2.14.0+cu130, sentence-transformers 5.6.0, transformers 5.13.1.

| Model | Revision | Params | GPU peak |
|---|---|---|---|
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | 233902d25c44 | 22.7M | 367 MB |
| `ncbi/MedCPT-Cross-Encoder` | 75e855e5aaed | 109.5M | 964 MB |
| `BAAI/bge-reranker-v2-m3` | 953dc6f6f85a | 567.8M | 2887 MB |
| `BAAI/bge-reranker-base` | 2cfc18c9415c | 278.0M | 1606 MB |

## Overall metrics

Passage-only input (what production scores). **Hit@k** = a relevant passage is in the top k (what single-answer QA usually calls Recall@k); Recall@k over all relevant passages, per question, is in `results.json`. Hit@50 is the same for every reranker: it is whether retrieval found the passage at all.

### All 67 questions

| Arm | Hit@1 | Hit@5 | Hit@10 | Hit@20 | Hit@50 | MRR | nDCG@5 | nDCG@10 | GPU latency |
|---|---|---|---|---|---|---|---|---|---|
| Retrieval only (RRF) | 0.493 | 0.836 | 0.955 | 0.970 | 0.985 | 0.651 | 0.494 | 0.523 | 0 ms |
| **Current** (ms-marco → MedCPT top 8) | 0.761 | 0.985 | 0.985 | 0.985 | 0.985 | 0.866 | 0.735 | 0.722 | 581 ms |
| ms-marco-MiniLM-L-6 alone | 0.746 | 0.985 | 0.985 | 0.985 | 0.985 | 0.850 | 0.741 | 0.718 | 292 ms |
| MedCPT alone | 0.657 | 0.985 | 0.985 | 0.985 | 0.985 | 0.799 | 0.647 | 0.677 | 2014 ms |
| BGE v2-m3 | 0.881 | 0.970 | 0.970 | 0.970 | 0.985 | 0.922 | 0.764 | 0.758 | 8791 ms |
| BGE base | 0.716 | 0.940 | 0.970 | 0.985 | 0.985 | 0.818 | 0.665 | 0.676 | 2391 ms |
| ms-marco → BGE base top 8 | 0.881 | 0.985 | 0.985 | 0.985 | 0.985 | 0.922 | 0.760 | 0.741 | 627 ms |
| ms-marco → BGE v2-m3 top 8 | 0.881 | 0.970 | 0.985 | 0.985 | 0.985 | 0.924 | 0.761 | 0.740 | 1496 ms |
| MedCPT + BGE v2-m3 (RRF fusion) | 0.881 | 0.970 | 0.985 | 0.985 | 0.985 | 0.927 | 0.773 | 0.761 | 10805 ms |
| Current, production's ~16-candidate pool | 0.761 | 0.970 | 0.970 | 0.970 | 0.970 | 0.857 | 0.719 | 0.676 | – |
| ms-marco → BGE base top 8, production pool | 0.806 | 0.970 | 0.970 | 0.970 | 0.970 | 0.882 | 0.738 | 0.686 | – |
| BGE v2-m3, production pool | 0.910 | 0.955 | 0.955 | 0.970 | 0.970 | 0.930 | 0.763 | 0.711 | – |

### The 66 questions whose answer was retrieved

Only these can separate rerankers: on the others every reranker scores 0.

| Arm | Hit@1 | Hit@5 | Hit@10 | Hit@20 | Hit@50 | MRR | nDCG@5 | nDCG@10 | GPU latency |
|---|---|---|---|---|---|---|---|---|---|
| Retrieval only (RRF) | 0.500 | 0.848 | 0.970 | 0.985 | 1.000 | 0.661 | 0.502 | 0.531 | 0 ms |
| **Current** (ms-marco → MedCPT top 8) | 0.773 | 1.000 | 1.000 | 1.000 | 1.000 | 0.879 | 0.746 | 0.732 | 581 ms |
| ms-marco-MiniLM-L-6 alone | 0.758 | 1.000 | 1.000 | 1.000 | 1.000 | 0.862 | 0.752 | 0.729 | 292 ms |
| MedCPT alone | 0.667 | 1.000 | 1.000 | 1.000 | 1.000 | 0.811 | 0.657 | 0.687 | 2014 ms |
| BGE v2-m3 | 0.894 | 0.985 | 0.985 | 0.985 | 1.000 | 0.936 | 0.776 | 0.769 | 8791 ms |
| BGE base | 0.727 | 0.955 | 0.985 | 1.000 | 1.000 | 0.830 | 0.675 | 0.686 | 2391 ms |
| ms-marco → BGE base top 8 | 0.894 | 1.000 | 1.000 | 1.000 | 1.000 | 0.936 | 0.772 | 0.752 | 627 ms |
| ms-marco → BGE v2-m3 top 8 | 0.894 | 0.985 | 1.000 | 1.000 | 1.000 | 0.938 | 0.772 | 0.751 | 1496 ms |
| MedCPT + BGE v2-m3 (RRF fusion) | 0.894 | 0.985 | 1.000 | 1.000 | 1.000 | 0.941 | 0.785 | 0.773 | 10805 ms |
| Current, production's ~16-candidate pool | 0.773 | 0.985 | 0.985 | 0.985 | 0.985 | 0.870 | 0.729 | 0.687 | – |
| ms-marco → BGE base top 8, production pool | 0.818 | 0.985 | 0.985 | 0.985 | 0.985 | 0.895 | 0.749 | 0.696 | – |
| BGE v2-m3, production pool | 0.924 | 0.970 | 0.970 | 0.985 | 0.985 | 0.945 | 0.774 | 0.722 | – |

### The 17 strict questions (answerable, at most 5 relevant passages in the pool)

Where many passages satisfy a predicate ("lateral rectus" + "abducens" matches 20), any model finds *a* relevant one. Here a reranker has to find the needle.

| Arm | Hit@1 | Hit@5 | Hit@10 | Hit@20 | Hit@50 | MRR | nDCG@5 | nDCG@10 | GPU latency |
|---|---|---|---|---|---|---|---|---|---|
| Retrieval only (RRF) | 0.176 | 0.706 | 0.882 | 0.941 | 1.000 | 0.426 | 0.301 | 0.429 | 0 ms |
| **Current** (ms-marco → MedCPT top 8) | 0.765 | 1.000 | 1.000 | 1.000 | 1.000 | 0.863 | 0.767 | 0.824 | 581 ms |
| ms-marco-MiniLM-L-6 alone | 0.765 | 1.000 | 1.000 | 1.000 | 1.000 | 0.843 | 0.735 | 0.800 | 292 ms |
| MedCPT alone | 0.706 | 1.000 | 1.000 | 1.000 | 1.000 | 0.814 | 0.711 | 0.806 | 2014 ms |
| BGE v2-m3 | 0.824 | 0.941 | 0.941 | 0.941 | 1.000 | 0.885 | 0.754 | 0.821 | 8791 ms |
| BGE base | 0.647 | 0.882 | 1.000 | 1.000 | 1.000 | 0.758 | 0.638 | 0.747 | 2391 ms |
| ms-marco → BGE base top 8 | 0.882 | 1.000 | 1.000 | 1.000 | 1.000 | 0.909 | 0.764 | 0.841 | 627 ms |
| ms-marco → BGE v2-m3 top 8 | 0.824 | 0.941 | 1.000 | 1.000 | 1.000 | 0.890 | 0.726 | 0.820 | 1496 ms |
| MedCPT + BGE v2-m3 (RRF fusion) | 0.824 | 0.941 | 1.000 | 1.000 | 1.000 | 0.888 | 0.757 | 0.840 | 10805 ms |
| Current, production's ~16-candidate pool | 0.765 | 0.941 | 0.941 | 0.941 | 0.941 | 0.843 | 0.724 | 0.747 | – |
| ms-marco → BGE base top 8, production pool | 0.765 | 0.941 | 0.941 | 0.941 | 0.941 | 0.828 | 0.709 | 0.740 | – |
| BGE v2-m3, production pool | 0.882 | 0.882 | 0.882 | 0.941 | 0.941 | 0.888 | 0.729 | 0.756 | – |

### Is the difference from production real?

Paired bootstrap over the answerable questions (2,000 resamples): MRR of the arm minus MRR of **current**, with a 95% interval. Wins / losses / ties count questions.

| Arm | ΔMRR vs current | 95% interval | Excludes 0? | W / L / T |
|---|---|---|---|---|
| Retrieval only (RRF) | -0.218 | [-0.315, -0.127] | yes | 8 / 29 / 29 |
| ms-marco-MiniLM-L-6 alone | -0.016 | [-0.074, +0.044] | no | 6 / 7 / 53 |
| MedCPT alone | -0.068 | [-0.117, -0.024] | yes | 1 / 11 / 54 |
| BGE v2-m3 | +0.058 | [-0.006, +0.129] | no | 12 / 5 / 49 |
| BGE base | -0.048 | [-0.130, +0.039] | no | 10 / 16 / 40 |
| ms-marco → BGE base top 8 | +0.057 | [-0.005, +0.124] | no | 12 / 6 / 48 |
| ms-marco → BGE v2-m3 top 8 | +0.059 | [-0.003, +0.125] | no | 11 / 4 / 51 |
| MedCPT + BGE v2-m3 (RRF fusion) | +0.062 | [+0.002, +0.129] | yes | 11 / 3 / 52 |
| Current, production's ~16-candidate pool | -0.009 | [-0.074, +0.054] | no | 7 / 6 / 53 |
| ms-marco → BGE base top 8, production pool | +0.016 | [-0.064, +0.096] | no | 12 / 9 / 45 |
| BGE v2-m3, production pool | +0.066 | [-0.007, +0.139] | no | 13 / 4 / 49 |

Strict subset only:

| Arm | ΔMRR vs current | 95% interval | Excludes 0? | W / L / T |
|---|---|---|---|---|
| Retrieval only (RRF) | -0.437 | [-0.588, -0.275] | yes | 1 / 13 / 3 |
| ms-marco-MiniLM-L-6 alone | -0.020 | [-0.162, +0.132] | no | 2 / 3 / 12 |
| MedCPT alone | -0.049 | [-0.118, +0.000] | no | 0 / 3 / 14 |
| BGE v2-m3 | +0.022 | [-0.144, +0.186] | no | 3 / 2 / 12 |
| BGE base | -0.104 | [-0.268, +0.074] | no | 2 / 6 / 9 |
| ms-marco → BGE base top 8 | +0.046 | [-0.096, +0.188] | no | 3 / 2 / 12 |
| ms-marco → BGE v2-m3 top 8 | +0.027 | [-0.137, +0.186] | no | 3 / 2 / 12 |
| MedCPT + BGE v2-m3 (RRF fusion) | +0.025 | [-0.155, +0.186] | no | 3 / 2 / 12 |
| Current, production's ~16-candidate pool | -0.020 | [-0.176, +0.127] | no | 3 / 2 / 12 |
| ms-marco → BGE base top 8, production pool | -0.034 | [-0.221, +0.157] | no | 3 / 3 / 11 |
| BGE v2-m3, production pool | +0.025 | [-0.185, +0.216] | no | 4 / 2 / 11 |

Read the **answerable** and **strict** tables, not "all 67": one question (q033) was never retrieved, so it
scores zero for every arm and only dilutes the comparison.

The paired bootstrap is the honest test. On 66 questions, a difference of about 0.06 MRR sits right at the
edge of what this set can resolve; on the 17 strict questions nothing is resolvable. Treat every positive
difference below as "worth a verified rerun", not as a result.

Production's MedCPT stage was also checked for the saturation bug that affected the benchmark (sentence-
transformers 5.6.0 applies a sigmoid to MedCPT). Production keeps full float32 scores, and MedCPT's largest
logit on this data is 15.96, below the ~16.6 where float32 rounds sigmoid to exactly 1.0: reproducing
production's computation on every top-8 head gave 0 saturated candidates and 0 ties out of 832. Production is
not affected.

## Subject performance

MRR / Hit@5 per subject, passage input. Four to six questions per subject: read these as signals, not verdicts; one question moves a cell by 0.2.

| Subject | n | Current (ms-marco → MedCPT top 8) | ms-marco-MiniLM-L-6 alone | MedCPT alone | BGE v2-m3 | BGE base |
|---|---|---|---|---|---|---|
| anatomy | 6 | 1.00 / 1.00 | 1.00 / 1.00 | 0.92 / 1.00 | 1.00 / 1.00 | 0.89 / 1.00 |
| physiology | 5 | 0.77 / 1.00 | 0.77 / 1.00 | 0.85 / 1.00 | 1.00 / 1.00 | 0.53 / 0.80 |
| biochemistry | 5 | 1.00 / 1.00 | 0.87 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| pathology | 5 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| pharmacology | 6 | 0.83 / 1.00 | 0.83 / 1.00 | 0.78 / 1.00 | 1.00 / 1.00 | 0.78 / 1.00 |
| microbiology | 5 | 0.90 / 1.00 | 1.00 / 1.00 | 0.80 / 1.00 | 1.00 / 1.00 | 0.80 / 1.00 |
| paediatrics | 5 | 0.80 / 0.80 | 0.80 / 0.80 | 0.70 / 0.80 | 0.80 / 0.80 | 0.80 / 0.80 |
| medicine | 5 | 0.80 / 1.00 | 0.65 / 1.00 | 0.75 / 1.00 | 0.90 / 1.00 | 0.75 / 1.00 |
| surgery | 4 | 0.88 / 1.00 | 1.00 / 1.00 | 0.88 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| ent | 4 | 0.88 / 1.00 | 0.88 / 1.00 | 0.58 / 1.00 | 0.81 / 1.00 | 0.75 / 1.00 |
| neurology | 4 | 0.71 / 1.00 | 0.88 / 1.00 | 0.54 / 1.00 | 1.00 / 1.00 | 0.77 / 0.75 |
| haematology | 5 | 0.90 / 1.00 | 0.75 / 1.00 | 0.80 / 1.00 | 0.71 / 0.80 | 0.70 / 0.80 |
| genetics | 4 | 0.71 / 1.00 | 0.75 / 1.00 | 0.71 / 1.00 | 0.88 / 1.00 | 1.00 / 1.00 |
| emergency medicine | 4 | 0.88 / 1.00 | 0.69 / 1.00 | 0.75 / 1.00 | 0.75 / 1.00 | 0.71 / 1.00 |

Four to six questions per subject: one question moves a cell by 0.17-0.25. The only patterns consistent with
the per-question evidence:

* **BGE v2-m3** is strongest where the right passage is a specific fact inside a topic-dense area
  (physiology, medicine, neurology): q008 sodium reabsorption, q052 the lateral medullary syndrome (First Aid's
  PICA lesion table, where production ranks a symptom list first), q060 Duchenne inheritance.
* **Production (ms-marco → MedCPT)** is strongest in haematology, entirely because of q056 (G6PD inclusions),
  where BGE v2-m3 sinks the Heinz-body passage to rank 21 under general G6PD overviews.
* **MedCPT alone** is weakest in neurology and ENT. Its losses are not domain-specific: see "Failure analysis".

No subject justifies routing by domain.

### MRR by difficulty

| Difficulty | n | Current (ms-marco → MedCPT top 8) | ms-marco-MiniLM-L-6 alone | MedCPT alone | BGE v2-m3 | BGE base |
|---|---|---|---|---|---|---|
| easy | 25 | 0.89 | 0.92 | 0.83 | 0.98 | 0.88 |
| hard | 6 | 0.89 | 0.92 | 0.89 | 1.00 | 0.83 |
| medium | 36 | 0.84 | 0.79 | 0.76 | 0.87 | 0.77 |

### MRR by question type

| Question type | n | Current (ms-marco → MedCPT top 8) | ms-marco-MiniLM-L-6 alone | MedCPT alone | BGE v2-m3 | BGE base |
|---|---|---|---|---|---|---|
| anatomy localization | 12 | 0.90 | 0.96 | 0.76 | 1.00 | 0.90 |
| buzzword | 17 | 0.91 | 0.97 | 0.91 | 1.00 | 0.97 |
| clinical scenario | 11 | 0.76 | 0.77 | 0.64 | 0.77 | 0.67 |
| confusable | 12 | 0.96 | 0.81 | 0.90 | 0.92 | 0.75 |
| drug mechanism | 4 | 0.88 | 0.88 | 0.88 | 1.00 | 0.83 |
| factual | 33 | 0.90 | 0.89 | 0.80 | 0.98 | 0.85 |
| fcps style | 18 | 0.77 | 0.75 | 0.65 | 0.76 | 0.68 |
| lab findings | 7 | 0.79 | 0.75 | 0.75 | 0.75 | 0.80 |
| mechanism | 14 | 0.85 | 0.83 | 0.88 | 1.00 | 0.71 |
| pathology association | 8 | 0.94 | 0.91 | 0.88 | 0.88 | 0.90 |

## Medical confusion performance

Each test pits the correct concept against named competitors (`medical_confusion_tests.json`). **Controlled** adds the nearest real textbook passage for any concept retrieval did not surface, so every contest actually happens; **natural** uses the retrieval pool as it came.

| Arm | Correct above every distractor: controlled (n contested) | …natural pool (n) | Top-1 correct (controlled) |
|---|---|---|---|
| Retrieval only (RRF) | 0.78 (18) | 0.73 (15) | 0.44 |
| **Current** (ms-marco → MedCPT top 8) | 1.00 (18) | 1.00 (15) | 0.78 |
| ms-marco-MiniLM-L-6 alone | 1.00 (18) | 1.00 (15) | 0.83 |
| MedCPT alone | 1.00 (18) | 1.00 (15) | 0.72 |
| BGE v2-m3 | 1.00 (18) | 1.00 (15) | 0.89 |
| BGE base | 0.94 (18) | 0.93 (15) | 0.78 |
| ms-marco → BGE base top 8 | 1.00 (18) | 1.00 (15) | 0.83 |
| ms-marco → BGE v2-m3 top 8 | 1.00 (18) | 1.00 (15) | 0.89 |
| MedCPT + BGE v2-m3 (RRF fusion) | 1.00 (18) | 1.00 (15) | 0.83 |

Per test (controlled pool; ✗ (n) = correct concept's best rank n):

| Test | Query | Current (ms-marco → MedCPT top 8) | ms-marco-MiniLM-L-6 alone | MedCPT alone | BGE v2-m3 | BGE base |
|---|---|---|---|---|---|---|
| c01 | What causes Howell-Jolly bodies? | ✓ | ✓ | ✓ | ✓ | ✓ |
| c02 | Which red cell inclusions are made of denatured  | ✓ | ✓ | ✓ | ✓ | ✓ |
| c03 | What is the mechanism of action of pioglitazone? | ✓ | ✓ | ✓ | ✓ | ✓ |
| c04 | If lacrimation is intact but all other facial ne | ✓ | ✓ | ✓ | ✓ | ✓ |
| c05 | A fracture of the shaft of the humerus causes wr | ✓ | ✓ | ✓ | ✓ | ✓ |
| c06 | Which nerve passes through the foramen ovale of  | ✓ | ✓ | ✓ | ✓ | ✓ |
| c07 | Which laryngeal muscle tenses the vocal cords? | ✓ | ✓ | ✓ | ✓ | ✓ |
| c08 | What are Councilman bodies and in which disease  | ✓ | ✓ | ✓ | ✓ | ✓ |
| c09 | Which type of necrosis is typical of acute pancr | ✓ | ✓ | ✓ | ✓ | ✓ |
| c10 | A steeple sign on a neck X-ray is seen in which  | ✓ | ✓ | ✓ | ✓ | ✓ |
| c11 | What is the most common cause of bronchiolitis i | ✓ | ✓ | ✓ | ✓ | ✓ |
| c12 | Which bacterial toxin acts by ADP-ribosylation o | ✓ | ✓ | ✓ | ✓ | ✓ |
| c13 | Which toxin of Clostridium tetani causes spastic | ✓ | ✓ | ✓ | ✓ | ✓ |
| c14 | Which hormone is released from the juxtaglomerul | ✓ | ✓ | ✓ | ✓ | ✓ |
| c15 | Where are the baroreceptors that buffer changes  | ✓ | ✓ | ✓ | ✓ | ✓ |
| c16 | What is the mechanism of action of clopidogrel? | ✓ | ✓ | ✓ | ✓ | ✗ (6) |
| c17 | What is the mechanism of action of furosemide? | ✓ | ✓ | ✓ | ✓ | ✓ |
| c18 | Occlusion of which artery causes the lateral med | ✓ | ✓ | ✓ | ✓ | ✓ |

Every reranker except BGE base keeps the correct concept above every distractor in all 18 contests, in both
the natural and the controlled pools. The contests turned out easier than intended: a basophilic-stippling
passage rarely contains the words "Howell-Jolly", so once retrieval has found both, any cross-encoder separates
them. Real confusion in this corpus happens one level down, inside comparison tables that mention all the
competing entities, and those count as correct here.

The stricter signal is **whether the top-1 passage is the correct concept** (controlled pools): BGE v2-m3 0.89,
ms-marco 0.83, production 0.78, BGE base 0.78, MedCPT 0.72. BGE base is the only model that let a distractor
win a contest (c16 clopidogrel: an aspirin/COX passage at rank 4, above the P2Y12 passage at 6).

## Exact terminology performance

Bare-term queries (`terminology_tests.json`): does the top of the ranking contain the exact term, or a conceptually similar passage without it? Controlled pools as above.

| Arm | Top-1 has the exact term | Exact-term share of top 5 | Near-miss ranked above exact | Top-1 exact (natural) |
|---|---|---|---|---|
| Retrieval only (RRF) | 0.89 | 0.82 | 0.00 | 0.89 |
| **Current** (ms-marco → MedCPT top 8) | 1.00 | 0.89 | 0.00 | 1.00 |
| ms-marco-MiniLM-L-6 alone | 1.00 | 0.87 | 0.00 | 1.00 |
| MedCPT alone | 1.00 | 0.92 | 0.00 | 1.00 |
| BGE v2-m3 | 1.00 | 0.91 | 0.00 | 1.00 |
| BGE base | 0.95 | 0.88 | 0.00 | 0.95 |
| ms-marco → BGE base top 8 | 1.00 | 0.89 | 0.00 | 1.00 |
| ms-marco → BGE v2-m3 top 8 | 1.00 | 0.88 | 0.00 | 1.00 |
| MedCPT + BGE v2-m3 (RRF fusion) | 1.00 | 0.93 | 0.00 | 1.00 |

Per test (controlled pool; ✓/✗ = top-1 exact, then exact share of top 5):

| Test | Query | Current (ms-marco → MedCPT top 8) | ms-marco-MiniLM-L-6 alone | MedCPT alone | BGE v2-m3 | BGE base |
|---|---|---|---|---|---|---|
| t01 | GALT | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t02 | G6PD | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t03 | HLA-B27 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 0.8 | ✓ 1.0 |
| t04 | p-ANCA | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t05 | CD4 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 0.8 |
| t06 | TNF-alpha | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t07 | MODY | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t08 | Howell-Jolly bodies | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t09 | Pappenheimer bodies | ✓ 0.4 | ✓ 0.4 | ✓ 0.4 | ✓ 0.4 | ✓ 0.4 |
| t10 | Basophilic stippling | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 0.8 | ✓ 0.8 |
| t11 | Heinz bodies | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t12 | PPAR-gamma | ✓ 0.8 | ✓ 0.6 | ✓ 0.8 | ✓ 1.0 | ✓ 0.8 |
| t13 | Carnitine shuttle | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t14 | AICA | ✓ 0.6 | ✓ 0.4 | ✓ 0.6 | ✓ 0.6 | ✓ 0.6 |
| t15 | PCA infarct | ✓ 0.2 | ✓ 0.2 | ✓ 0.6 | ✓ 0.6 | ✗ 0.4 |
| t16 | thyroarytenoid | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t17 | cricothyroid | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t18 | conus medullaris | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |
| t19 | cauda equina | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 | ✓ 1.0 |

No evidence that semantic reranking pushes exact terms down. Every model except BGE base puts a passage with
the exact term first for all 19 bare-term queries, and no near-miss passage outranks the best exact-term
passage for any model. These queries are easy because production's keyword search already fills the pool with
exact-term passages. The weaker cells (Pappenheimer bodies, AICA, PCA infarct; exact-term share of the top 5
0.2-0.6) reflect a corpus with few passages containing those terms, not reranker drift.

## Structured metadata experiment

The same candidates, scored as passage only versus `[BOOK] / [CHAPTER] / [SECTION] / [PAGE] / [PASSAGE]` built from the metadata the corpus really has (chapter path split on ` > `; nothing invented). Answerable questions; confusion = controlled pass rate.

| Arm | MRR passage | MRR structured | Δ | Hit@1 passage | Hit@1 structured | Confusion passage | Confusion structured |
|---|---|---|---|---|---|---|---|
| **Current** (ms-marco → MedCPT top 8) | 0.879 | 0.863 | -0.016 | 0.773 | 0.758 | 1.00 | 0.94 |
| ms-marco-MiniLM-L-6 alone | 0.862 | 0.888 | +0.026 | 0.758 | 0.803 | 1.00 | 0.89 |
| MedCPT alone | 0.811 | 0.822 | +0.011 | 0.667 | 0.697 | 1.00 | 1.00 |
| BGE v2-m3 | 0.936 | 0.955 | +0.019 | 0.894 | 0.924 | 1.00 | 1.00 |
| BGE base | 0.830 | 0.895 | +0.065 | 0.727 | 0.833 | 0.94 | 1.00 |
| ms-marco → BGE base top 8 | 0.936 | 0.918 | -0.018 | 0.894 | 0.864 | 1.00 | 1.00 |
| ms-marco → BGE v2-m3 top 8 | 0.938 | 0.947 | +0.009 | 0.894 | 0.909 | 1.00 | 1.00 |
| MedCPT + BGE v2-m3 (RRF fusion) | 0.941 | 0.924 | -0.017 | 0.894 | 0.864 | 1.00 | 1.00 |
| Current, production's ~16-candidate pool | 0.870 | 0.881 | +0.011 | 0.773 | 0.803 | – | – |
| ms-marco → BGE base top 8, production pool | 0.895 | 0.899 | +0.004 | 0.818 | 0.833 | – | – |
| BGE v2-m3, production pool | 0.945 | 0.953 | +0.009 | 0.924 | 0.939 | – | – |

Prefixing `[BOOK] / [CHAPTER] / [SECTION] / [PAGE]` (from the chapter paths the corpus really has) changed no
model significantly (structured minus passage, MRR): ms-marco +0.026, MedCPT +0.011, BGE v2-m3 +0.019, BGE base
+0.065 (interval −0.005 to +0.136), production pipeline −0.016. Production's pipeline gets slightly worse, and
its confusion pass rate drops from 1.00 to 0.94 (ms-marco alone: 0.89). Why is not established here; the
point is that there is no gain to buy. Do not add metadata to the reranker input.

## Failure analysis

A miss = no relevant passage in the top 5 (production gives the LLM 5-6 blocks). Questions and confusion tests, natural pool. **Retriever**: never in the top 50. **Reranker**: retrieved, ranked below 5. **Metadata**: the relevant passage lacks chapter or page. **Chunking**: the answer is split across neighbouring chunks, or lies outside the 1,500-character window the reranker reads. **Ground truth**: the predicate matches nothing in the corpus, or verified ids are gone.

| Arm | Retriever | Reranker | Metadata | Chunking | Ground truth | Total |
|---|---|---|---|---|---|---|
| Retrieval only (RRF) | 1 | 10 | 1 | 0 | 0 | 12 |
| **Current** (ms-marco → MedCPT top 8) | 1 | 1 | 0 | 0 | 0 | 2 |
| ms-marco-MiniLM-L-6 alone | 1 | 1 | 0 | 0 | 0 | 2 |
| MedCPT alone | 1 | 1 | 0 | 0 | 0 | 2 |
| BGE v2-m3 | 1 | 1 | 0 | 0 | 0 | 2 |
| BGE base | 1 | 4 | 0 | 0 | 0 | 5 |
| ms-marco → BGE base top 8 | 1 | 1 | 0 | 0 | 0 | 2 |
| ms-marco → BGE v2-m3 top 8 | 1 | 2 | 0 | 0 | 0 | 3 |
| MedCPT + BGE v2-m3 (RRF fusion) | 1 | 2 | 0 | 0 | 0 | 3 |
| Current, production's ~16-candidate pool | 2 | 1 | 0 | 0 | 0 | 3 |
| ms-marco → BGE base top 8, production pool | 2 | 0 | 0 | 0 | 0 | 2 |
| BGE v2-m3, production pool | 2 | 1 | 0 | 0 | 0 | 3 |

**MedCPT clearly ahead** (0):


**BGE v2-m3 clearly ahead** (1):

* **c15** Where are the baroreceptors that buffer changes in arterial blood pressure? — rank of first relevant: current 9, medcpt 13, bge_v2_m3 1, bge_base 1

**Current (two-stage) clearly ahead** (0):


**Every reranker misses the top 5** (1):

* **q033** A 5-week-old boy has projectile non-bilious vomiting and a palpable olive in the epigastrium. Blood gas shows  — rank of first relevant: current —, medcpt —, bge_v2_m3 —, bge_base — — class: retriever

**After the audit, production misses the top 5 on 2 of 85 questions and confusion tests:**

* **q033 (retriever)**: the fluid for pyloric stenosis (KCl in glucose-saline) is in one passage in the corpus,
  and neither query brings it into the top 50. No reranker can fix this; it is a retrieval or coverage gap.
* **c15 (reranker)**: "Where are the baroreceptors…": production ranks Guyton p226, which says where
  baroreceptors *are*, 9th, under passages about what they *do* (the pressure-buffer function, posture). BGE
  v2-m3 and BGE base both rank p226 first; MedCPT alone ranks it 13th.

**Where BGE v2-m3 wins (rank 1 vs production's 2-3), with the reason:**

* q024 "antidote for heparin overdose": production's top passage is the one-line case note "Treatment
  (Immediate): Heparin is administered for anticoagulation" (Lippincott p1446), which matches the words but
  answers nothing; BGE v2-m3 ranks Nelson's antidote table first.
* q052 lateral medullary syndrome: production ranks a symptom list (Snell p501) above First Aid's PICA lesion
  table (p548), which names the artery.
* q060 Duchenne: production ranks Robbins' general classification of muscular dystrophies (p816) first; BGE
  v2-m3 ranks Nelson's Duchenne passage.

**Where production wins:**

* q056 G6PD inclusions: BGE v2-m3 ranks Heinz bodies at 21 (general G6PD overviews outrank it); production
  ranks Lippincott's oxidative-damage passage first.
* q065 organophosphates: BGE v2-m3 (and BGE base) rank a chunk that is only the heading "Organophosphate
  poisoning" first. **BGE promotes heading-only chunks**, which carry the query's words and nothing else.

**Where MedCPT loses (11 questions, 1 win):** in every case MedCPT's top passage is on the right topic but
does not contain the answer: an epistaxis definition instead of Little's area (q049), "antidotes exist for only
a few poisons" instead of protamine (q024), a general pesticides passage instead of atropine and pralidoxime
(q065), the clinical picture of gas gangrene with no organism named (q030), the vocalis muscle instead of the
cricothyroid (q050). MedCPT was trained to match PubMed search queries to the articles users clicked, which
rewards "about this topic". A study question needs "states the answer".

**The ground-truth audit.** After the first run, the top-1 passage of every model was read wherever the
predicate called it irrelevant (43 questions). Twelve predicates were rejecting passages that plainly answer,
because of abbreviations (CCK, HL, CN VI, DKA), plurals ("loops of Henle"), synonyms ("caseating"), or correct
answers the predicate was too narrow for (psammoma bodies in meningioma and mesothelioma). They were broadened,
identically for every model, and each change is recorded on its item (`"audit"` in `questions.json`;
`tools/audit_2026_10_07.py`). The audit raised every arm's Hit@1, and BGE v2-m3's lead over production at
rank 1 was similar before and after it. It was done by an AI reader, so these items remain `unverified`.

No **metadata** or **chunking** failures were detected. The chunking check only covers an answer split across
neighbouring paragraphs on the same page, and an answer outside the 1,500-character window the reranker reads.

## Performance

### GPU (RTX 2060), every item, passage input

| Arm | Mean | Median | p95 | Total |
|---|---|---|---|---|
| Retrieval only (RRF) | 0 ms | 0 ms | 0 ms | 0.0 s |
| **Current** (ms-marco → MedCPT top 8) | 581 ms | 611 ms | 816 ms | 60.4 s |
| ms-marco-MiniLM-L-6 alone | 292 ms | 331 ms | 381 ms | 30.3 s |
| MedCPT alone | 2014 ms | 2238 ms | 2831 ms | 209.5 s |
| BGE v2-m3 | 8791 ms | 9954 ms | 11634 ms | 914.3 s |
| BGE base | 2391 ms | 2767 ms | 3216 ms | 248.7 s |
| ms-marco → BGE base top 8 | 627 ms | 665 ms | 908 ms | 65.3 s |
| ms-marco → BGE v2-m3 top 8 | 1496 ms | 1506 ms | 2254 ms | 155.6 s |
| MedCPT + BGE v2-m3 (RRF fusion) | 10805 ms | 12222 ms | 14248 ms | 1123.8 s |

### CPU (this PC, a subset of items): the closer guide to the NAS

| Model | Mean | Median | p95 | RSS increase | Threads |
|---|---|---|---|---|---|
| `cross-encoder/ms-marco-MiniLM-L-6-v2` (full pool) | 4210 ms | 4424 ms | 4845 ms | 431.4 MB | 4 |
| `ncbi/MedCPT-Cross-Encoder` (full pool) | 22914 ms | 24152 ms | 26598 ms | 366.8 MB | 4 |
| `BAAI/bge-reranker-v2-m3` (full pool) | 71347 ms | 75087 ms | 82418 ms | 1577.8 MB | 4 |
| `BAAI/bge-reranker-base` (full pool) | 20191 ms | 21194 ms | 23526 ms | 719.8 MB | 4 |
| ms-marco → medcpt top 8 (production shape) | 6526 ms | 6708 ms | 8169 ms | – | – |
| ms-marco → bge_base top 8 (production shape) | 7379 ms | 7318 ms | 9550 ms | – | – |
| ms-marco → bge_v2_m3 top 8 (production shape) | 15407 ms | 15106 ms | 21544 ms | – | – |

**The benchmark's first scoring run was wrong for MedCPT.** sentence-transformers 5.6.0 applies a sigmoid to
models whose config names no activation (MedCPT, both BGE rerankers), and the harness then rounded scores to 6
decimals. MedCPT's large logits saturated: 7% of its scores became exactly 1.0, tied at the top of the ranking,
so its order fell back to retrieval order. Every model was re-scored with raw logits (`activation_fn=Identity`,
no rounding). MedCPT's apparent quality fell from roughly production's level to clearly below it; BGE was barely
affected (its top scores never saturated).

Latency on the PC (i7-8700, 4 threads), per question, scaled to production's real pool of ~16 candidates and
two queries (stage 1 measured over 50 candidates and scaled by 15.8/50.3; stage 2 measured directly on 8):

| Pipeline | CPU per question | Reranker RAM (process growth) |
|---|---|---|
| ms-marco alone | ≈1.3 s | ~430 MB (shared with the neighbour gate) |
| **production**: ms-marco → MedCPT top 8 | ≈3.6 s | + ~370 MB |
| ms-marco → BGE base top 8 | ≈4.5 s | + ~720 MB |
| ms-marco → BGE v2-m3 top 8 | ≈12.5 s | + ~1.6 GB |
| BGE v2-m3 over 50 candidates | ≈71 s | ~1.6 GB |

The NAS CPU is slower than this PC, and it has about 1.3 GB free with MedCPT loaded (a second MedCPT process is
OOM-killed). BGE v2-m3 does not fit at all; BGE base in MedCPT's place would need roughly 350 MB more.

## Recommendation

See RECOMMENDATION.md.

## Reproducing

See `README.md`. Per-question rankings, scores and failure classes: `results.json` and `results.csv`.
