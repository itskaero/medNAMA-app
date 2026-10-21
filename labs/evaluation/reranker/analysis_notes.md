# Analysis notes (hand-written; spliced into RERANKER_BENCHMARK.md by `run.py evaluate`)

Written 2026-10-07 after the final run (raw-logit scores, post-audit predicates). Numbers quoted here are
from that run; if you re-run with verified ground truth, re-read the tables before trusting this prose.

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

## Overall metrics

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

Four to six questions per subject: one question moves a cell by 0.17-0.25. The only patterns consistent with
the per-question evidence:

* **BGE v2-m3** is strongest where the right passage is a specific fact inside a topic-dense area
  (physiology, medicine, neurology): q008 sodium reabsorption, q052 the lateral medullary syndrome (First Aid's
  PICA lesion table, where production ranks a symptom list first), q060 Duchenne inheritance.
* **Production (ms-marco → MedCPT)** is strongest in haematology, entirely because of q056 (G6PD inclusions),
  where BGE v2-m3 sinks the Heinz-body passage to rank 21 under general G6PD overviews.
* **MedCPT alone** is weakest in neurology and ENT. Its losses are not domain-specific: see "Failure analysis".

No subject justifies routing by domain.

## Medical confusion performance

Every reranker except BGE base keeps the correct concept above every distractor in all 18 contests, in both
the natural and the controlled pools. The contests turned out easier than intended: a basophilic-stippling
passage rarely contains the words "Howell-Jolly", so once retrieval has found both, any cross-encoder separates
them. Real confusion in this corpus happens one level down, inside comparison tables that mention all the
competing entities, and those count as correct here.

The stricter signal is **whether the top-1 passage is the correct concept** (controlled pools): BGE v2-m3 0.89,
ms-marco 0.83, production 0.78, BGE base 0.78, MedCPT 0.72. BGE base is the only model that let a distractor
win a contest (c16 clopidogrel: an aspirin/COX passage at rank 4, above the P2Y12 passage at 6).

## Exact terminology performance

No evidence that semantic reranking pushes exact terms down. Every model except BGE base puts a passage with
the exact term first for all 19 bare-term queries, and no near-miss passage outranks the best exact-term
passage for any model. These queries are easy because production's keyword search already fills the pool with
exact-term passages. The weaker cells (Pappenheimer bodies, AICA, PCA infarct; exact-term share of the top 5
0.2-0.6) reflect a corpus with few passages containing those terms, not reranker drift.

## Structured metadata experiment

Prefixing `[BOOK] / [CHAPTER] / [SECTION] / [PAGE]` (from the chapter paths the corpus really has) changed no
model significantly (structured minus passage, MRR): ms-marco +0.026, MedCPT +0.011, BGE v2-m3 +0.019, BGE base
+0.065 (interval −0.005 to +0.136), production pipeline −0.016. Production's pipeline gets slightly worse, and
its confusion pass rate drops from 1.00 to 0.94 (ms-marco alone: 0.89). Why is not established here; the
point is that there is no gain to buy. Do not add metadata to the reranker input.

## Failure analysis

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
