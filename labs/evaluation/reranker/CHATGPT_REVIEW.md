# Independent review request: medical-textbook reranker benchmark (MedNAMA)

You are asked to critically review a reranker benchmark and its conclusions. Look for methodological flaws,
statistical over-claiming, and conclusions the data does not support. Be specific; disagree where warranted.
Everything you need is in this file. (If `results.csv` is attached, it has one row per question × arm × input
format, with the rank of the first relevant passage, MRR, nDCG, latency and failure class.)

---

## 1. The system being evaluated

MedNAMA is a study app for medical postgraduate exams (FCPS, Pakistan). It answers questions from 25 ingested
textbooks (Guyton, Robbins, Katzung, Davidson, Nelson, Bailey & Love, Dhingra, Moore, Snell, BD Chaurasia,
Lippincott, Levinson, First Aid Step 1 and others): about 200k parent chunks (layout paragraphs and tables) and
224k child chunks.

**Production retrieval pipeline**

1. An LLM rewrites the question into ~30 textbook keywords (skipped if the question already looks like keywords).
2. For the original question and the rewrite: vector search (bge-large-en-v1.5, pgvector HNSW, 60 rows) and
   Postgres full-text search (60 rows), both over child chunks.
3. Reciprocal rank fusion (k = 60) onto parent chunks.
4. The rerank pool is the top 16 parents by RRF plus the top 3 of each individual list: about 16 candidates
   (measured mean 15.8). Index pages are dropped.
5. **Stage 1:** `cross-encoder/ms-marco-MiniLM-L-6-v2` scores every candidate. The text scored is a
   1,500-character window of the parent around the matched child. Each candidate takes the max score over the
   two queries.
6. **Stage 2:** `ncbi/MedCPT-Cross-Encoder` re-orders stage 1's top 8 (max over queries).
7. A book-diversity step chooses 5–6 passages, which are expanded with neighbouring paragraphs and sent to the
   LLM.

**Production hardware:** a home NAS. CPU only, 3.8 GB RAM, about 1.3 GB free with MedCPT loaded (a second MedCPT
process gets OOM-killed). The benchmark ran on a PC: i7-8700 and an RTX 2060 (6 GB).

---

## 2. Benchmark design

**Isolation.** Production code was not modified. The harness rebuilds the candidate set with production's own
functions (rewrite, vector search, keyword search, RRF, focus window) but keeps the **top 50** by RRF plus the
list heads. A check runs the real production search with its reranker call intercepted. On 20 questions, 19
matched exactly. The 20th differed by one borderline candidate (keyword rank 11), because production retrieval
is itself not deterministic: Postgres full-text score ties and approximate HNSW search. The same function gave a
different pool on 3 consecutive runs. So **pools were frozen once and every reranker scores the identical
candidates**, and the LLM rewrite was recorded once and replayed.

**Arms** (all ranking the same frozen pool):

| Arm | Description |
|---|---|
| retrieval | RRF order, no cross-encoder |
| current | production: ms-marco orders all, MedCPT re-orders the top 8 |
| msmarco | stage 1 alone |
| medcpt | MedCPT over the whole pool |
| bge_v2_m3 | `BAAI/bge-reranker-v2-m3` (568M params) over the whole pool |
| bge_base | `BAAI/bge-reranker-base` (278M) over the whole pool |
| msmarco>bge_base@8 | production's shape with BGE base as stage 2 |
| msmarco>bge_v2_m3@8 | production's shape with BGE v2-m3 as stage 2 |
| medcpt+bge_v2_m3 | reciprocal-rank fusion of the two full rankings |
| …@prod_pool | the same ranking restricted to production's real ~16-candidate pool (exact: cross-encoder scores are per passage) |

Every model scores against both the question and the rewrite and keeps the max, as production does. All runs:
fp32, `max_length` 512, batch 32, eval mode, raw logits.

**Datasets**

- **67 questions**, 14 subjects (anatomy, physiology, biochemistry, pathology, pharmacology, microbiology,
  paediatrics, medicine, surgery, ENT, neurology, haematology, genetics, emergency medicine). Easy / medium /
  hard. A mix of direct factual questions, mechanisms, FCPS-style clinical vignettes, buzzwords and confusable
  entities. 12 are reused from an older internal harness.
- **18 confusion tests.** The correct concept against 3–4 named competitors, e.g. Howell-Jolly vs basophilic
  stippling / Heinz / Pappenheimer; pioglitazone (PPAR-γ) vs GLP-1 / sulfonylurea / DPP-4; facial nerve lesion
  localisation; foramen ovale of the skull vs of the heart.
- **19 exact-terminology tests.** Bare-term queries (GALT, G6PD, HLA-B27, p-ANCA, CD4, TNF-alpha, MODY, AICA,
  thyroarytenoid, conus medullaris…) with near-miss distractors that lack the term.

**Ground truth: the main weakness.** No chunk IDs were hand-assigned. Each item has a **gold predicate**: terms
the answer passage must contain, e.g. `all: [howell jolly], any: [[splen, asplen, hyposplen]]`. Matching is
case-insensitive, tolerant of hyphens and spaces, Greek-letter aware, and tolerant of PDF ligature splits. Every
predicate was checked to match at least one passage in the corpus; two that matched nothing were replaced. A
candidate is "relevant" if its parent text satisfies the predicate. An annotation tool exists to replace
predicates with clinician-verified chunk IDs, but **none has been verified yet**.

**Contest injection.** For confusion and terminology tests, if retrieval did not surface a passage for the
correct concept or for a distractor, the 2 nearest real corpus passages for that concept were added
("controlled" pool). "Natural" results use the retrieval pool only.

**Statistics.** Paired bootstrap (2,000 resamples over questions) of the MRR difference against production,
with 95% percentile intervals, plus win / loss / tie counts.

**Strict subset.** 17 answerable questions with at most 5 relevant passages in the pool. Loose predicates match
up to ~25 passages, which makes a hit easy for every model.

---

## 3. Two problems found and fixed mid-run (please scrutinise)

**A. Scoring bug in the harness.** sentence-transformers 5.6.0 applies a sigmoid to models whose config names no
activation (MedCPT and both BGE rerankers; ms-marco uses identity). The harness also rounded stored scores to 6
decimals. MedCPT's logits reach ~16, so 7% of its scores became exactly 1.0 and tied at the top of the ranking.
Ties fell back to candidate index, which is RRF order. **First-run MedCPT results were therefore largely
retrieval order.** All models were re-scored with raw logits, unrounded.

Production uses the same library version but keeps full float32 and never rounds. Reproducing production's
exact computation on all 832 stage-2 head candidates gave 0 saturated scores and 0 ties, because the max logit
(15.96) is below float32 sigmoid saturation (~16.6). The conclusion was that production is not affected.

**B. Predicate audit.** After the first run, the top-1 passage of each of the 5 headline arms was read wherever
the predicate called it irrelevant (43 questions). **12 predicates were broadened** because they rejected
passages that plainly answer:
- abbreviations: CCK, HL, CN VI, DKA;
- plurals: "loops of Henle";
- synonyms: "caseating" vs "caseous";
- answers the predicate was too narrow for: psammoma bodies in meningioma and mesothelioma, not only papillary
  tumours.

Rules followed: broaden only; decide by passage content, never by which model ranked it; record every change on
the item. The audit was done by an AI reader, not a clinician. **Question for you:** does auditing only
top-1 passages labelled irrelevant, after seeing model outputs, introduce a bias toward any arm?

---

## 4. Results (final run: raw logits, post-audit labels; passage-only input)

### 4.1 The 66 questions whose answer was retrieved (1 of 67 never was)

| Arm | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|
| retrieval | 0.500 | 0.758 | 0.848 | 0.970 | 0.661 | 0.531 |
| **current** | 0.773 | 1.000 | 1.000 | 1.000 | 0.879 | 0.732 |
| current@prod_pool | 0.773 | 0.970 | 0.985 | 0.985 | 0.870 | 0.687 |
| msmarco | 0.758 | 0.955 | 1.000 | 1.000 | 0.862 | 0.729 |
| medcpt | 0.667 | 0.955 | 1.000 | 1.000 | 0.811 | 0.687 |
| bge_v2_m3 | 0.894 | 0.970 | 0.985 | 0.985 | 0.936 | 0.769 |
| bge_v2_m3@prod_pool | 0.924 | 0.955 | 0.970 | 0.970 | 0.945 | 0.722 |
| bge_base | 0.727 | 0.939 | 0.955 | 0.985 | 0.830 | 0.686 |
| msmarco>bge_base@8 | 0.894 | 0.970 | 1.000 | 1.000 | 0.936 | 0.752 |
| msmarco>bge_base@8@prod_pool | 0.818 | 0.970 | 0.985 | 0.985 | 0.895 | 0.696 |
| msmarco>bge_v2_m3@8 | 0.894 | 0.970 | 0.985 | 1.000 | 0.938 | 0.751 |
| medcpt+bge_v2_m3 | 0.894 | 0.985 | 0.985 | 1.000 | 0.941 | 0.773 |

Hit@k = a relevant passage is in the top k.

### 4.2 Paired bootstrap of the MRR difference (66 answerable questions)

| Comparison | ΔMRR | 95% interval | Better / worse / tie |
|---|---|---|---|
| msmarco − current | −0.016 | [−0.074, +0.044] | 6 / 7 / 53 |
| medcpt − current | −0.068 | [−0.117, −0.024] | 1 / 11 / 54 |
| bge_v2_m3 − current | +0.058 | [−0.006, +0.129] | 12 / 5 / 49 |
| bge_base − current | −0.048 | [−0.130, +0.039] | 10 / 16 / 40 |
| msmarco>bge_base@8 − current | +0.057 | [−0.005, +0.124] | 12 / 6 / 48 |
| msmarco>bge_v2_m3@8 − current | +0.059 | [−0.003, +0.125] | 11 / 4 / 51 |
| medcpt+bge_v2_m3 − current | +0.062 | [+0.002, +0.129] | 11 / 3 / 52 |
| bge_v2_m3 − medcpt | +0.125 | [+0.057, +0.195] | 18 / 4 |
| msmarco>bge_base@8 − msmarco | +0.074 | [+0.015, +0.140] | 12 / 5 |
| bge_v2_m3 − msmarco | +0.074 | [+0.020, +0.129] | 13 / 4 |
| **On production's ~16-candidate pool:** | | | |
| bge_v2_m3@prod − current@prod | +0.075 | [+0.013, +0.139] | 12 / 3 |
| msmarco>bge_base@8@prod − current@prod | +0.025 | [−0.039, +0.091] | 11 / 7 |

On the 17-question strict subset, no arm except retrieval-only differs significantly from current.

### 4.3 Confusion tests (18) and terminology tests (19), controlled pools

| Arm | Confusion: correct above all distractors | Confusion: top-1 is the correct concept | Terminology: top-1 has the exact term | Terminology: exact-term share of top 5 |
|---|---|---|---|---|
| current | 1.00 | 0.78 | 1.00 | 0.89 |
| msmarco | 1.00 | 0.83 | 1.00 | 0.87 |
| medcpt | 1.00 | 0.72 | 1.00 | 0.92 |
| bge_v2_m3 | 1.00 | 0.89 | 1.00 | 0.91 |
| bge_base | 0.94 | 0.78 | 0.95 | 0.88 |

No near-miss (term-lacking) passage outranked the best exact-term passage for any model.

### 4.4 Structured metadata input

Prefixing `[BOOK] [CHAPTER] [SECTION] [PAGE] [PASSAGE]`, built from real metadata only. Structured minus
passage-only MRR, same model:

| Model | ΔMRR | 95% interval |
|---|---|---|
| msmarco | +0.026 | [−0.012, +0.069] |
| medcpt | +0.011 | [−0.063, +0.084] |
| bge_v2_m3 | +0.019 | [−0.023, +0.065] |
| bge_base | +0.065 | [−0.005, +0.136] |
| current pipeline | −0.016 | [−0.079, +0.043] |

The current pipeline's confusion pass rate drops from 1.00 to 0.94 with structured input.

### 4.5 Latency and memory

GPU (RTX 2060), mean per question over ~50 candidates × 2 queries:

| Arm | Mean |
|---|---|
| msmarco | 0.29 s |
| current | 0.58 s |
| medcpt | 2.0 s |
| bge_base | 2.4 s |
| msmarco>bge_base@8 | 0.63 s |
| msmarco>bge_v2_m3@8 | 1.5 s |
| bge_v2_m3 | 8.8 s |

CPU (i7-8700, 4 threads), scaled to production's ~16-candidate pool:

| Pipeline | Per question |
|---|---|
| msmarco alone | ≈1.3 s |
| current | ≈3.6 s |
| msmarco>bge_base@8 | ≈4.5 s |
| msmarco>bge_v2_m3@8 | ≈12.5 s |
| bge_v2_m3 over 50 candidates | ≈71 s |

Process RAM growth: msmarco +430 MB, MedCPT +370 MB, BGE base +720 MB, BGE v2-m3 +1,580 MB.

### 4.6 Failure analysis

A "miss" means no relevant passage in the top 5. Production misses on 2 of 85 items:

- **q033 (retriever miss).** The pyloric-stenosis fluid answer (KCl in glucose-saline) appears in one passage
  in the corpus and never enters the top 50.
- **c15 (reranker miss).** "Where are the baroreceptors?" The passage stating their location is ranked 9th,
  under passages about what baroreceptors do. Both BGE models rank it 1st.

**MedCPT's 11 losses share one pattern.** The top passage is on topic but doesn't state the answer:
- an epistaxis definition, for "most common site of epistaxis";
- "antidotes exist for only a few poisons", for the heparin antidote;
- the clinical picture of gas gangrene, with no organism named.

**BGE wins over production:**
- q024: production's #1 is a one-line case note, "Heparin is administered for anticoagulation"; BGE ranks an
  antidote table first.
- q052: production ranks a symptom list above First Aid's PICA lesion table.

**BGE losses:**
- q056: the Heinz-body passage is at rank 21, under general G6PD overviews.
- q065: BGE ranks a heading-only chunk ("Organophosphate poisoning") first.

---

## 5. Conclusions drawn (please challenge)

1. **Drop MedCPT from production.** As stage 2 it adds no measurable quality over ms-marco alone (−0.016 vs
   current, 6 better / 7 worse). It costs ~2.3 s per question and ~370 MB on a memory-starved NAS.
2. **MedCPT is not the better medical reranker here.** Alone it is significantly worse than production. Proposed
   reason: it was trained on PubMed search logs (query → clicked article), which rewards topicality, not
   answer-bearing passages.
3. **BGE v2-m3 is the best reranker.** It is significant vs MedCPT and vs ms-marco, and vs production on
   production's real pool (+0.075). But it can't run on the NAS.
4. **Whether BGE base as stage 2 beats production on the NAS is unresolved** (+0.025 on the real pool, not
   significant).
5. **Metadata in the reranker input doesn't help;** keep passage-only.
6. **Keep the ~16-candidate rerank pool and 5–6 passages to the LLM.** Reranking 50 changed MRR by 0.009 and
   rescued one question. Production already has a relevant passage in its top 3 for 97–100% of answerable
   questions.
7. **Next step:** clinician verification of ~33 questions (the strict subset plus the 16 where BGE v2-m3 and
   production disagree at rank 1) before any quality-driven change.

---

## 6. Specific questions for the reviewer

1. Is n = 66 (17 strict) adequate for these claims? Are any conclusions above stronger than the bootstrap
   intervals allow? Should a multiple-comparisons correction apply, given about 12 comparisons?
2. Is "drop MedCPT" justified by a non-significant difference (−0.016, interval [−0.074, +0.044])? Absence of
   evidence of benefit is not evidence of no benefit. Is the cost argument enough to carry it?
3. Does the predicate-based ground truth, with an audit only of top-1 passages labelled irrelevant, bias the
   comparison? Toward which arm?
4. The confusion and terminology tests barely discriminated (pass rates of ~1.0). Is that a real finding (keyword
   retrieval already handles exact terms) or a design flaw (distractor passages rarely contain the queried term,
   so the contests are too easy)? How would you redesign them?
5. Is max-over-queries scoring (question and LLM rewrite) a fair way to compare models? Could it favour one
   model family?
6. Are there confounds in comparing a 568M multilingual model (BGE v2-m3) with a 110M PubMed-trained model on a
   1,500-character window with `max_length` 512?
7. Is the MedCPT tie-bug diagnosis and fix sound? Is the claim that production is unaffected adequately
   supported?
8. What would you recommend for production, given CPU only and ~1.3 GB of free RAM?
