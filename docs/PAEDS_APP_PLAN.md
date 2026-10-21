# PaedNAMA: a Paediatrics IMM / FCPS-II app, branched from medNAMA

*Plan, October 2026. Exam facts come from the CPSP Paediatrics curriculum (`pdfs/paeds/prospectus.pdf`, for batches entering from July 2026). Page numbers are the document's printed ones.*

## Why

- **Nelson is hard to retain.** Reading it is passive, so it fades within days. The app has to turn reading into recall.
- **The exams test reasoning under questioning,** not only facts. Every TOACS station ends in examiner questions. The FCPS-II long case is marked partly on "knowledge of recent advances relevant to the case".
- **medNAMA already has the engines:** cited answers from the books, the station and viva engine, spaced re-tests, the recall bank, Seed Studio and the Today plan. PaedNAMA builds a Paediatrics product on top of them.

## Decisions

- **A separate app for now:** `apps/paednama/`, with its own server, web app, port and tables.
  - It **imports medNAMA's backend code as a library and changes nothing in medNAMA.**
  - It is merged into medNAMA once it works.
- **Same accounts and the same book library** as medNAMA.
- **Users:** built for one trainee first, then other trainees. Licensing has to be settled before book-derived content is shared.
- **Name:** **PaedNAMA**, the recommendation. Alternatives: Nanha ("little one"), Bachpan ("childhood"), medNAMA Paeds.

---

## 1. The exams (CPSP curriculum 2026)

| | IMM (after years 1–2) | FCPS-II (after years 3–4) |
|---|---|---|
| Eligibility | FCPS-I (Medicine & Allied), 2 years of training, e-logbook, workshops, research synopsis (p.66) | IMM passed, 2 more years, e-logbook, workshops, one published research paper (p.121) |
| Theory | Paper I: 100 single-best MCQs · Paper II: 100 single-best MCQs (p.67) | Paper I: 100 single-best MCQs · Paper II: 100 single-best MCQs (p.122) |
| Clinical | TOACS, sat only after passing theory (p.67) | TOACS first, then **one long case + four short cases** (p.123) |
| TOACS | 12–18 stations, one minute to change, **all interactive** (p.68) | Same format (p.123) |
| Long case | — | 60 min: 30 min history and examination under observation, then 30 min presentation and discussion (pp.123–124) |
| Short cases | — | At least 4 in 40 min, a specific examination task on each (p.124) |

**What "interactive" means at TOACS:** you perform a task (a history, an examination, counselling, assembling an instrument, or another task). The examiner at the station rates it and **asks critical-thinking and problem-solving questions**. The viva happens at every station; there is no separate viva.

**Long-case marking areas** (p.124):
- interviewing
- consent
- a systematic examination
- correct findings and logical interpretation
- the differential diagnosis
- investigations and treatment, each justified
- prevention and prognosis
- **recent advances relevant to the case**

**Workplace-based assessment** (pp.60–65, 115–120): about 40 Mini-CEX topics and about 30 DOPS procedures. These are the best list of what TOACS and the cases will test. Examples:
- **Mini-CEX:** hydration, growth plotting, triage, a comatose child, prolonged neonatal jaundice, PUO, shock, developmental delay, short stature, a heart murmur, DKA planning, ambiguous genitalia, poisoning, a premature baby.
- **DOPS:** IV cannulation, lumbar puncture, intubation, umbilical lines, surfactant, bubble CPAP, intraosseous access, exchange transfusion, a chest drain, peritoneal dialysis.

**Syllabus:** IMM pp.22–40 and FCPS-II pp.71–88. About 25 systems, from general principles, drug therapy, growth and development, adolescent medicine, vaccinology and nutrition, through neonatology, infections, critical care, allergy and immunology, genetics and metabolism, neurology, GI and hepatology, respiratory, cardiology, haematology and oncology, nephrology, endocrinology, rheumatology, bone, gynaecology, psychiatry and skin, to paediatric surgery.

**Competencies:** IMM pp.41–59 and FCPS-II pp.89–114. Each has an expected level and number of cases, by rotation.

All of this becomes data in `apps/paednama/data/cpsp_paeds.json`, with an `imm` stage and an `fcps2` stage. Every fact keeps its page number, and the app shows "per CPSP curriculum 2026, p.X". Nothing about the exam is invented. If CPSP announces a change, only the data file changes.

---

## 2. Features

### A. My exam, my training year
- **Setup:** stage (IMM or FCPS-II), sitting date, training year, **current rotation** and daily study time.
- **Today follows the rotation:** in NICU, today's topics are neonatal ones, because you'll see those cases that week.
- **A weekly target for each component:** syllabus topics, TOACS stations, MCQs and (for FCPS-II) cases.
- **Readiness for each component,** because you can pass theory and fail TOACS.

### B. Nelson that sticks: the reading loop
For each topic:
1. **Case first.** A three-line case; commit to an answer, then read to check it.
2. **Short sections** of 10–15 minutes, with page numbers.
3. **Recall without rereading.** 3–5 questions at the end of each section.
4. **Explain it back** in your own words, checked against the passage, with the page for anything missed.
5. **Every miss becomes a card** on the spaced-repetition scheduler, and comes back as a *different* question just before you'd forget it.

On top of that:
- **High-yield order:** topics ordered by the syllabus, Mini-CEX importance and your weak spots.
- **Interleaving:** daily review mixes systems instead of finishing one at a time.

### C. Short topics: from all the books, one book at a time when you want
- **Topic one-pager** (default): one cited sheet per syllabus topic, written from *all* the Paediatrics books.
  - Nelson Essentials is the spine; Nelson 22 adds depth; the specialty books cover their areas.
  - It shows where the books agree or differ.
  - Fixed sections: definition, causes by age, red flags, investigations in order, management with doses, complications, the examiner's favourite question.
- **Book lens:** the same topic as one book tells it (Nelson Essentials only, WHO only, Cloherty only).
- **Why not one book:** a single book misses local protocols (WHO, IMNCI) and practical neonatology (Cloherty).

### D. Theory MCQs (both stages: 2 × 100 single-best)
- **A Paediatrics question bank** for every syllabus topic, written in Seed Studio, checked against the book page, and held for review.
- **The recall bank** of past IMM and FCPS-II questions, with disputed answer keys checked against the books.
- **Question types the exam favours:** images, data interpretation (blood gas, CSF, electrolytes, CBC), calculations (fluids, doses per kg, GCS, z-scores).
- **Pakistan context:** the national vaccination schedule (EPI), IMNCI, XDR typhoid, dengue, malaria, thalassaemia, severe acute malnutrition.
- **Full timed papers:** two papers of 100 questions each.

### E. TOACS trainer (12–18 interactive stations)
Every station is a task followed by examiner questions, which is what medNAMA's station engine already does.

**Station types,** written first from the Mini-CEX and DOPS lists:

| Type | Examples |
|---|---|
| History | A floppy infant, failure to thrive, a child with recurrent wheeze |
| Examination (described findings) | Cardiovascular with a murmur, a developmental assessment, a neurological exam |
| Counselling | Breaking bad news, vaccine hesitancy, starting insulin, end-of-life decisions |
| Instruments and procedures | Assembling bubble CPAP, intraosseous needle, the steps of a lumbar puncture, an exchange transfusion |
| Data and images, with questions | Blood gas, CSF, CBC/smear, X-rays, growth charts |
| Algorithms | NRP, PALS, status epilepticus, DKA, anaphylaxis |

**How it works:**
- **Two modes:** guided (with hints) or timed (with the real one-minute changeover).
- **Circuits** of 12–18 stations.
- **The debrief:** each criterion marked, with your own words quoted as evidence and safety-critical points kept apart.
- **AI parent** for history and counselling stations, with an emotion and an agenda.

### F. Long and short cases (FCPS-II)
- **History-taking simulator:** an AI parent who answers only what you ask. You're marked on missed key questions (antenatal, birth, development, immunisation, family history, diet).
- **Examination reveal:** you request findings system by system.
- **Presentation and discussion**, marked on the curriculum's own long-case areas, including **recent advances relevant to the case**.
- **Short-case drills:** 4 cases in 40 minutes, each with a task, findings, an interpretation and a diagnosis, answered briefly.

### G. Clinical reasoning: differentials
- **Cases open from a presenting complaint:** fever and rash, a floppy infant, bilious vomiting, short stature, and so on.
- **Information is released stage by stage:** history, then examination, then results.
- **At each stage you commit** to a ranked differential, the must-not-miss diagnoses and the next best test.
- **Marking:** the correct diagnosis in your top 3; every must-not-miss named; the right test ordered and no harmful one; the differential updated as findings arrive.
- **Sources:** *Pediatric Decision Making Strategies* (symptom algorithms) and *Case Files*.
- **Illness-script library:** one card per diagnosis (who and when, key features, discriminating tests, what it mimics), drilled as "which diagnosis fits?"

### H. Recent evidence library
- **One card per change:** what changed, why it matters, a 30-second answer for the examiner, the citation and the date.
- **Linked to topics and long cases:** reading about bronchiolitis surfaces the RSV-prevention card.
- **Reviewed and dated** before it's shown, and drawn only from ingested sources; nothing invented.
- **A monthly "what's new" set** of a few questions.

**Starter topics (each to be checked against its source):**
- **Neonatology:** WHO 2022 preterm and low-birth-weight recommendations (immediate kangaroo care); the AAP 2022 jaundice thresholds.
- **Infections and vaccines:** nirsevimab and maternal RSV vaccination; typhoid conjugate vaccine and XDR typhoid; the RTS,S and R21 malaria vaccines; the AAP 2021 febrile-infant guideline.
- **Critical care:** the Phoenix paediatric sepsis criteria (2024); the latest AHA paediatric life-support update.
- **Gene and targeted therapy:** gene therapy for sickle cell disease and thalassaemia; blinatumomab in B-ALL; therapies for SMA; CFTR modulators; teplizumab.
- **Other:** the ILAE 2022 epilepsy syndromes; the AAP 2023 obesity guideline; MIS-C (now in the syllabus, p.39).

### I. Revision tools (for exam practice, not patient care)
- **Calculation drills:** doses and fluids.
- **Charts and schedules:** WHO growth-chart plotting with z-scores; a milestones trainer; Pakistan EPI schedule drills; a jaundice-chart exercise.
- **Algorithm flashcards:** NRP, PALS and status epilepticus, practised in order.

### J. Progress and retention
- **Mastery map** by system, marked for each component.
- **Mistake types:** knowledge gap, reasoning, prioritisation, safety or communication. Each needs a different fix.
- **Forgetting forecast** from the review scheduler.
- **Logbook mirror:** the curriculum's competencies, with your practice against each.

### K. Trust and review
- **Clinician review** of stations, cases, evidence cards and generated MCQs.
- **Expert gold answers** to check the AI marker against.
- **Labelled as practice:** an educational simulation, never an official result.

---

## 3. Books

Already in the library: **Nelson Textbook of Pediatrics, 22nd ed.** (vol 1 and 2).

Added in `pdfs/paeds/` (about 7,700 pages), each with a role in the app:

| Book | Pages | Role |
|---|---|---|
| Nelson Essentials of Pediatrics | 840 | **Spine**: the first read for each topic |
| Nelson Textbook, 22nd ed. | — | Reference: depth and details |
| Cloherty & Stark's Manual of Neonatal Care | 1,073 | Neonatal |
| Textbook of Neonatal Resuscitation, 8th ed. | 333 | Neonatal / algorithms |
| WHO Hospital Care for Children, 2nd ed. | 438 | Local protocol |
| Pediatric Decision Making Strategies | 371 | Reasoning |
| Case Files Pediatrics | 550 | Reasoning / cases |
| Clinical Pediatrics (Aruchamy) | 784 | Clinical skills |
| Essential Clinical Skills in Pediatrics | 251 | Clinical skills |
| Imperial Paeds Long Cases, 2nd ed. | 524 | Long and short cases (**scanned: needs OCR**) |
| Pediatric Critical Care Medicine, vol 1 | 766 | Critical care |
| Radiology Illustrated: Pediatric Radiology | 1,077 | Radiology / images |
| AAP PREP 2024 | 736 | MCQ critique (used for retrieval, never copied as questions) |

**Suggested reading habit:**
1. Read the topic one-pager, or Nelson Essentials.
2. Use Nelson 22 only to look up details and for the questions you got wrong.
3. Test yourself straight away.

Also worth adding later: Ghai Essential Pediatrics (South Asian context), the Harriet Lane Handbook (doses and normal values), the Oxford Handbook of Paediatrics, and national IMNCI and EPI documents.

---

## 4. Architecture

```
apps/paednama/
  server/      python -m apps.paednama.server.main  → :8780
               Imports medNAMA's API app (backend/app) and adds /api/paeds/* at start-up,
               so one process serves both. No medNAMA file is edited.
  web/         Next.js on :3100 (npm run dev). Its own brand, navigation and pages.
               "@/..." imports medNAMA's UI (frontend/src) read-only; "~/..." is its own code.
               node_modules is a link to frontend/node_modules (scripts/link_modules.bat),
               so both apps share one React.
  data/        cpsp_paeds.json, built by scripts/build_registry.py from the curriculum PDF
  tests/       its own pytest suite
```

- **Sign-in:** medNAMA's own login and cookie session. Cookies ignore the port, so signing in on `:3000` or `:3100` signs you in to both apps on the same machine.
- **Shared:** the Postgres database, the user accounts, the book library (`books`, `chunks`, `figures`), retrieval with `book_ids`, cited generation, the station engine, the FSRS review cards and the LLM client.
- **Memory:** don't run the PaedNAMA server (which loads the search models) while books are being ingested on a 16 GB PC; it starves the ingestion workers.
- **Its own tables:** `paeds_book_roles`, `paeds_sheets`, `paeds_reading`, `paeds_cases`, `paeds_case_attempts`, `paeds_evidence`, `paeds_profile`.
- **Wrappers, not edits:**
  - Book-scoped sheets use its own `paeds_sheets.py`, which calls `retrieval_service.search(book_ids=…)`.
  - Station drafting uses its own prompt and calls the engine's turn and scoring functions.
  - Anything it must change is copied and marked "merge back".
- **The guarantee:** `git diff` shows no changes under `backend/` or `frontend/` while PaedNAMA is being built.
- **Merging later:**
  - its routers move into `backend/app/routers`;
  - its tables get a proper Alembic migration;
  - its pages become a Paediatrics product in the main frontend;
  - the "merge back" copies are reconciled with their originals.

---

## 5. "Train an LLM": what to train and what not to

- **Not the answer model, not now.** Without hundreds of expert-checked examples, fine-tuning makes answers more confident, not more correct. Training on the books' text also creates a copyright problem once the app is shared.
- **Worth training, in order:**
  1. **The search models.** Fine-tune the embedder (bge-large) and the reranker (bge-reranker-base) on Paediatrics question-to-passage pairs, so the right passage comes first. Measure it on medNAMA's existing reranker benchmark with a Paediatrics question set.
  2. **The marker, by example.** Expert gold answers plus few-shot marking, with agreement measured. The station calibration feature exists already. No weights change.
  3. **A small examiner or marker model, later.** A LoRA adapter on an open 7–8B model, trained only on reviewed transcripts and marks (at least 500). The goal is lower cost and offline use.
- **Where:** a `labs/training/` folder on the PC GPU (never on the NAS).

---

## 6. Phases

| Phase | Delivers |
|---|---|
| **A** | This document; ingest the 12 books, with OCR for Imperial; book roles |
| **B** | PaedNAMA shell: server, web app, login, navigation, launcher; `cpsp_paeds.json` from the curriculum |
| **C** | Topic one-pagers with the book lens; the reading loop; Paediatrics MCQs via Seed Studio |
| **D** | TOACS stations from the Mini-CEX and DOPS lists; clinical reasoning cases; illness scripts |
| **E** | FCPS-II long and short case trainer; recent-evidence library v1; voice answers |
| **F** | Paediatrics retrieval set; embedder and reranker fine-tune |

## 7. Verification

- **Ingestion:** all 12 books show as ready, and 10 Paediatrics test questions retrieve passages from the expected books.
- **Book lens:** a sheet scoped to one book cites only that book; the all-books sheet cites at least two where they cover the topic.
- **PaedNAMA:**
  - its own tests pass;
  - a medNAMA account signs in on :3100;
  - medNAMA's code is unchanged;
  - a browser walkthrough of setup → Today → a topic → the reading loop → a reasoning case.
- **Reasoning cases:** a missed must-not-miss is flagged, and staged information is never released early.
- **Training:** top-1 retrieval hit rate on the Paediatrics set, before and after fine-tuning.
