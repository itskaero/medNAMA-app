# Reranker recommendation for MedNAMA

Based on the benchmark of 2026-10-07 (`RERANKER_BENCHMARK.md`): 67 questions, 18 confusion tests and 19
terminology tests, over production's own retrieval pools from the 25-book library. **Ground truth is
provisional** (term predicates, audited once by an AI reader, not by a clinician), and 66 questions resolve
differences of about 0.06 MRR at best. Nothing here has been changed in production.

## The ten questions

**1. Which reranker is best for MedNAMA?**
On quality, BGE v2-m3 ranks best (MRR 0.936 vs production's 0.879; right passage first 89% vs 77%), and on
production's real ~16-candidate pool its lead is significant (+0.075 MRR, 12 better / 3 worse). But it
cannot run on the NAS: about 1.6 GB of RAM against ~1.3 GB free, and about 12 s per question on a desktop CPU
even as a second stage. Among options the NAS can run, **ms-marco → BGE base top 8** is the best performer
(MRR 0.936 on a 50-candidate pool, 0.895 on production's ~16), and **production's current pipeline is a close
second**: the gap between those two is not significant (+0.025 on production's pool).

**2. Is MedCPT actually better for medical textbooks?**
No. Alone it is worse than production (MRR 0.811 vs 0.879; 1 question better, 11 worse; significant). As
production's second stage it adds nothing measurable over ms-marco alone (+0.016, 7 better / 6 worse). Its
characteristic error: it ranks first a passage *about* the topic that does not *state* the answer (an epistaxis
definition for "most common site of epistaxis"). It was trained to match PubMed searches to clicked articles,
which rewards topicality, not answer-bearing passages.

**3. Does BGE outperform it despite being less medically specialised?**
Yes. BGE v2-m3 beats MedCPT by +0.125 MRR (18 questions better, 4 worse; 95% interval +0.057 to +0.195). BGE
base is weaker than MedCPT on its own, but as a second stage behind ms-marco it is the one that helps (+0.074
over ms-marco alone, significant) where MedCPT does not.

**4. Does textbook metadata improve ranking?**
No. Prefixing book / chapter / section / page changed no model significantly, and it made production's own
pipeline slightly worse (−0.016 MRR; confusion contests 1.00 → 0.94). Keep scoring the passage alone.

**5. What candidate count should production retrieve before reranking?**
Keep the current ~16 (top 16 by RRF plus each list's top 3). Reranking 50 instead gave production the same MRR
(0.879 vs 0.870) and rescued one question, at three times the reranker cost. Retrieval order alone already has
a relevant passage in its top 10 for 97% of questions.

**6. What top-K should be passed to the LLM?**
Keep 5-6. Production puts a relevant passage in the top 3 for every answerable question on a 50 pool, and 97%
on its real pool (98.5% within 5). Fewer than 5 would start losing answers; more adds tokens without adding
answers on this set. (Book diversity, `select_diverse`, is a separate reason to keep 5-6.)

**7. Is the latency acceptable?**
Production's reranking costs about **3.6 s per question on a desktop i7** (ms-marco ~1.3 s + MedCPT ~2.3 s),
and more on the NAS. That is acceptable for chat but is the largest single cost after the LLM call. Dropping
MedCPT would cut it to ~1.3 s; BGE base in its place would raise it to ~4.5 s; BGE v2-m3 to ~12.5 s.

**8. Are there specific domains where one reranker clearly performs better?**
Not clearly: 4-6 questions per subject cannot support a verdict. Signals only: BGE v2-m3 leads in physiology,
medicine and neurology; production leads in haematology because of one question (G6PD inclusions, where BGE
sinks the Heinz-body passage to rank 21). Do not route by domain.

**9. Single reranker or hybrid?**
Two-stage (cheap model orders everything, a stronger model re-orders the top 8) is the right shape: it gets
most of the strong model's gain at a fraction of its cost (ms-marco → BGE v2-m3 top 8 matches BGE v2-m3 alone
at Hit@1, at a sixth of the GPU time). Score fusion (MedCPT + BGE v2-m3) is the only arm significant against
production (+0.062, barely: interval +0.002 to +0.129) but costs about 11 s per question on a GPU and is not
practical. What matters is **which** model is the second stage, and MedCPT is the weak choice.

**10. What should be changed in production, if anything?**
Nothing automatically. In order:

1. **Verify the ground truth** before any quality-driven change: the 17 strict questions plus the 16 questions
   where BGE v2-m3 and production disagree at rank 1 (`run.py sheet export`, mark y/n, `sheet import`,
   `evaluate`). About an hour of clinical reading, and it decides whether the borderline gains are real.
2. **Consider removing the MedCPT stage** (`RERANKER_SECOND_STAGE=""`; the setting exists in
   `app/config.py`). On this benchmark it buys no measurable quality, and it costs ~2.3 s per question and
   ~370 MB on a NAS whose memory pressure already OOM-kills a second MedCPT process. This is the one change the
   data supports today, and it is reversible. Note: the root `docker-compose.yml` passes the variable through,
   but `NAS/docker-compose.yaml` does not (its backend has an explicit `environment:` list and no `env_file`),
   so on the NAS it needs one line added there: `RERANKER_SECOND_STAGE: ${RERANKER_SECOND_STAGE-ncbi/MedCPT-Cross-Encoder}`.
3. **Only if the verified rerun still shows a gain**, try BGE base as the second stage
   (`RERANKER_SECOND_STAGE=BAAI/bge-reranker-base`), first checking its ~720 MB fits beside the backend on the
   NAS. Watch for its known failure: heading-only chunks ("Organophosphate poisoning") ranked first.
4. Independently of rerankers, two fixes the failure analysis points to: filter heading-only and
   bibliography-like chunks out of the candidate pool (both BGE models over-rank the former), and improve
   coverage for the one true retrieval miss (q033: the pyloric stenosis fluid is in a single passage).
