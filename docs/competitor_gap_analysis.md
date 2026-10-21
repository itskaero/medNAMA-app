# MedNAMA competitor and gap analysis

Researched 2026-10-07 from each product's public site, app-store listings, news coverage and third-party
reviews. I could not log in to any of them, so this reflects what they claim publicly, not tested behaviour.
Osmosis blocks automated fetching; its section rests on Elsevier's announcements and independent reviews.
MedNAMA's column is from its code, not its marketing.

## 1. The seven at a glance

| | Market | Core asset | AI layer | Price |
|---|---|---|---|---|
| **AMBOSS** | Global, USMLE/boards | Peer-reviewed library + 13,000+ Qbank | AI Mode Learning (Feb 2026), grounded in and citing its own library | Subscription (USD) |
| **Osmosis** (Elsevier) | Global, USMLE | Videos, notes, Quiz Builder, flashcards | Osmosis AI (Feb 2026): cited answers linked to videos and Elsevier content | Subscription (USD) |
| **Revisable** | India + global (NEET PG, USMLE, AMC) | 200K flashcards, 100K MCQs, educator notes/videos | "Ruby AI" tutor, AI test analysis, AI-generated notes and quizzes | ₹0 / ₹999 per quarter / ₹1,999 per year |
| **iTutor** | Generic, any subject; MENA/South Asia | Your own uploaded files | Chat grounded in your PDFs, voice tutor in 12 languages (incl. Urdu, RTL), generates everything | Free for students |
| **PrepFCPS** | **Pakistan, FCPS Part 1** | 19,500 reviewed MCQs, 1,300 image questions, 11 Paper-II tracks | AI tutor from the verified answer and reference, voice AI, photo AI, SEQ grading | Rs 1,500–5,999 one-off |
| **Medmacs** | **Pakistan, MBBS / FCPS-1 / NRE** | 200,000 MCQs | "Dr. Ahroid" tutor, AI test generator | PKR tiers, free tier |
| **NEETPGAI** | India (NEET PG, INI-CET, FMGE) | 33,500 MCQs, PYQs (free) | AI tutor with diagrams, AI MCQ generator, OSCE case simulator, study planner | Free bank; Pro ₹2,499/year |

**Direct competitors for MedNAMA's users are PrepFCPS and Medmacs.** AMBOSS and Osmosis set the quality bar.
NEETPGAI is the closest product in shape: an AI-native exam platform with a published quality pipeline.

## 2. Feature by feature

✓ = has it · ◐ = partial or limited · ✗ = not found · ? = not stated publicly

| Feature | AMBOSS | Osmosis | Revisable | iTutor | PrepFCPS | Medmacs | NEETPGAI | **MedNAMA** |
|---|---|---|---|---|---|---|---|---|
| AI chat / tutor | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **✓** (+ Socratic "viva" tutor mode) |
| Textbook-grounded RAG | ◐ own library | ◐ Elsevier content | ◐ claims "1M pages" | ✓ your uploads | ◐ explains from the stored reference | ? | ◐ "anchored to" textbooks | **✓ 25 standard textbooks, live retrieval** |
| Page-level citations | ◐ library articles | ◐ "fully cited", granularity unclear | ? | ◐ "the right part of your file" | ✓ edition + page in curated explanations | ? | ◐ paraphrased quotes ≤15 words | **✓ [Book, Page] validated against retrieved pages, linked to the page image** |
| Exam-level personalisation | ✓ by career stage | ◐ USMLE-aligned | ✓ 7 exams | ✗ | ✓ 11 Paper-II tracks | ✓ MBBS / FCPS-1 / NRE | ✓ 3 exams | **✓ 7 programmes, changes retrieval, depth and MCQ style** |
| Revision notes / summaries | ✓ library | ✓ notes | ✓ smart book summaries | ✓ study guides | ✓ "smart content" | ? | ✓ guides | **✓ one-page sheets from chosen books/chapters, every bullet cited** |
| MCQ generation | ✓ Qbank sessions | ✓ | ✓ from weak areas | ✓ | ✗ (curated only) | ✓ test generator | ✓ (Pro) | **✓ grounded in book pages, answer-support check, level/difficulty/format** |
| MCQ explanations | ✓ + "attending tip" | ✓ | ✓ AI analysis | ✓ | ✓ with page refs | ✓ "high-yield" | ✓ + "why others wrong", mnemonics | **✓ with textbook refs** |
| Adaptive testing | ✓ custom sessions by difficulty and past performance | ◐ | ✓ weak-area modules | ◐ | ✓ weak-topic drills | ✓ | ✓ | **✓ weakest-subject Daily Dose, unseen-first, drill missed** |
| Flashcards / spaced repetition | ✓ Anki add-on | ✓ SRS + Anki export | ✓ 200K cards | ✓ | ✓ | ? | ✓ SM-2 on every wrong answer | **✓ FSRS cards, concept re-tests, Anki export** |
| Source / book selection | ✗ | ✗ | ✗ | ✓ your files | ✗ | ✗ | ✗ | **✓ pick the books (hard scope)** |
| Clinical reasoning / case simulation | ✓ attending tips, clinical mode | ✓ decision trees | ✗ | ✗ | ◐ SEQ grading vs rubric | ◐ viva prompts | **✓ 6-stage OSCE simulator, 100-point rubric** | ◐ Socratic tutor only |
| Image-based questions | ✓ | ✓ | ? | ✗ | **✓ 1,300+** | ? | ✓ | ◐ past-paper media, 26 "spot the diagnosis" |
| Image / photo input to AI | **✓ ECG / slides upload** | ? | ? | ✓ photos | **✓ "AI Visual"** | ? | ? | ✗ |
| Voice | ? | ? | ? | **✓ 12 languages incl. Urdu** | **✓ voice AI** | ? | ? | ✗ |
| Study planner | ✓ | ✓ | ✓ | ✓ | ◐ | ? | **✓ day-by-day, adapts to accuracy and SRS queue** | ◐ Today + Final sprint + exam countdown |
| Readiness / score prediction | ? | ? | ✓ AI test analysis | ✗ | ✓ vs 75% pass line | ✓ "mark leak tracking" | ✓ projected band, Bloom level, percentile | **✓ readiness vs FCPS 75% line** |
| Gamification | ◐ | ◐ | ? | ✗ | ◐ themes | **✓ battle mode, streaks, leaderboards** | **✓ XP, badges, missions** | ◐ streak, weekly-mock leaderboard, duels |
| Past-paper intelligence | ✗ | ✗ | ◐ | ✗ | ✗ (no leaked papers, by policy) | ◐ full-length papers | ✓ SME-gated PYQs | **✓ 34K past-paper Qs, recall groups across archives, asked-years frequency, high-yield map** |
| Answer-key verification | ✗ | ✗ | ✗ | ✗ | ◐ reviewer approval | ✗ | ◐ cross-model re-solve | **✓ Referee: verifies keys against textbooks with checked quotes** |
| Question QC pipeline | ✓ editorial | ✓ editorial | ? | ✗ | ✓ reviewer approval | "verified" | **✓ Claude Haiku → Gemini re-solve → SME 7-point rubric** | ◐ textbook support check + referee; no human review queue |
| Mobile | ✓ native | ✓ native | ✓ native | ✓ | ✓ native iOS/Android | ✓ Android, offline | ✓ | ◐ PWA + offline packs |
| Multilingual / Urdu | ◐ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |

## 3. Against MedNAMA

### 3.1 What MedNAMA already does better

1. **Verifiable, page-level grounding.** Live retrieval over the 25 books FCPS candidates actually study,
   citations checked against the retrieved pages (fabricated ones are stripped), and a link to the page image.
   - PrepFCPS cites pages, but only in curated, static explanations.
   - AMBOSS and Osmosis cite their *own* content.
   - NEETPGAI paraphrases at most 15 words.
2. **The student chooses the sources.** A hard book scope ("only Guyton and Katzung") exists nowhere else
   except iTutor, which can only do it with the student's own uploads.
3. **Answer-key verification (the Referee).** It settles disputed keys with quoted textbook evidence and now
   adds unknown MCQs automatically. This addresses Pakistan's biggest content problem, wrong recall keys, which
   no competitor tackles. PrepFCPS sidesteps it by refusing leaked papers; MedNAMA solves it.
4. **Past-paper intelligence.**
   - Recall groups link the same question across archives.
   - Asked-years frequency and the high-yield map.
   - Twists (new-angle variants of past questions, refereed) and harder versions.
   No competitor models *how often* CPSP asks a concept.
5. **Programme-aware depth across chat, revision and MCQs** (MBBS → FCPS-II, PGET, MD/MS, USMLE), with
   textbook grounding kept as a hard rule.
6. **Mistake diagnosis.** Missed answers are classed as confusion, misconception or gap, and look-alike sets
   target confusable pairs. Competitors stop at "weak topics".

### 3.2 What competitors do better

1. **Volume and human-reviewed quality.**
   - PrepFCPS: 19,500 reviewed MCQs, each with a page reference.
   - Medmacs: 200,000.
   - NEETPGAI: a documented three-stage pipeline with expert sign-off.

   MedNAMA's checks are automated (textbook support, referee) with no human review queue.
2. **Images.** PrepFCPS has 1,300+ image questions (histology, radiographs, ECGs) and lets students photograph
   an X-ray or a printed question. AMBOSS takes ECG uploads. MedNAMA accepts no images from students.
3. **Clinical reasoning practice.** NEETPGAI's six-stage OSCE simulator with a 100-point rubric; PrepFCPS's
   written SEQ grading. MedNAMA's tutor mode is Socratic but unstructured.
4. **Planning.** NEETPGAI, AMBOSS and Osmosis turn exam date plus weak areas into a day-by-day plan.
   MedNAMA has the inputs (exam date, mastery, FSRS queue, past-paper frequency) but no plan.
5. **Voice and language.** PrepFCPS and iTutor have voice; iTutor speaks Urdu.
6. **Engagement and polish.**
   - Native apps (PrepFCPS, Medmacs).
   - Battle mode (Medmacs).
   - XP, badges and missions (NEETPGAI).
   - Clear free tiers and PKR pricing.
7. **Explanation format.** NEETPGAI makes "why each other option is wrong", high-yield facts and mnemonics a
   one-tap standard. AMBOSS adds the "attending tip" (how an expert would reason).

### 3.3 Important missing features (ranked)

| # | Missing | Who has it | Why it matters for FCPS |
|---|---|---|---|
| 1 | **Image questions + photo input** | PrepFCPS, AMBOSS | CPSP papers include images; students share questions as WhatsApp photos |
| 2 | **Day-by-day study plan** | NEETPGAI, AMBOSS, Osmosis | Turns MedNAMA's many tools into one daily path |
| 3 | **Structured case / viva practice** (FCPS-II TOACS, viva, SEQ) | NEETPGAI (OSCE), PrepFCPS (SEQ) | FCPS-II is case- and viva-based; MCQs alone don't prepare for it |
| 4 | **Human review queue for AI and recalled questions** | NEETPGAI, PrepFCPS | Trust: one wrong key costs credibility |
| 5 | **Difficulty labels** | AMBOSS (1–5 "hammers") | Lets students choose challenge level and pace |
| 6 | **Voice (and Urdu)** | PrepFCPS, iTutor | Phone-first, commuting students |
| 7 | **Stable public access** | (all) | The Cloudflare quick tunnel fails; nothing else matters if students can't reach the site |

## 4. Low-effort / high-impact improvements

Each builds on code that already exists.

| Improvement | Effort | Builds on |
|---|---|---|
| **Stable public URL**: named Cloudflare Tunnel; long requests (revision sheets, Check an answer) as background jobs | Low | Existing cloudflared container (token mode is already templated); MCQ-generation job pattern |
| **Photo → Check an answer**: student photographs an MCQ; vision OCR fills question and options; the referee verifies; the auto-add keeps it | Low–medium | Gemini key already configured (figure captions); Check an answer + `checked_mcqs.py` |
| **Today plan v2**: a day-by-day plan from exam date, readiness gap, FSRS due cards, weakest subjects and past-paper frequency | Medium | ReadinessCard, mastery map, FSRS queue, high-yield map, Final sprint |
| **Difficulty labels from real answers**: % of students correct per question, bucketed 1–5, shown and filterable | Low | `answer_events` already records every answer |
| **Explanation standard**: always "why each wrong option is wrong" + one mnemonic, with page references | Low | Explanation prompt and `textbook_refs.py` |
| **Surface "the books disagree"**: show the referee's `books_conflict` verdict to students as a feature | Low | Referee verdicts already computed |
| **Voice input** (browser speech-to-text, English first) | Low | Chat input |
| **Universal exam-level selector** (already promised) | Low | Shared level state already exists |

## 5. What would genuinely differentiate MedNAMA

These lean on assets competitors don't have: the textbooks themselves, page images, the referee and
past-paper frequency. Copying a feature list wouldn't produce them.

1. **"Settle the key" for Pakistan's recall culture.** A public, quoted, page-linked verdict for every disputed
   past-paper key: "CPSP recall says B; Guyton p227 and Ganong p612 say C", plus a dispute list per sitting.
   Students already argue keys on WhatsApp; MedNAMA can be where arguments end, and the share-card already
   exists. Every check a student runs grows the bank (auto-add). No competitor does this; PrepFCPS refuses
   recalled papers entirely.
2. **Image MCQs from the textbooks' own figures.** The ingested books contain thousands of captioned figures
   (histology, radiology, anatomy). Generating image questions from figure plus caption plus surrounding page
   text gives grounded, page-cited image questions at scale. That answers PrepFCPS's 1,300-image lead without
   hand-curation, and each question links to its page.
3. **FCPS-II TOACS / viva simulator grounded in the books.** Station-by-station cases (history → examination →
   investigation → management) whose rubric is built from the retrieved textbook passages, scored with page
   references for every missed point. NEETPGAI's OSCE simulator isn't textbook-grounded or FCPS-shaped;
   PrepFCPS grades SEQs only. This also gives the FCPS-II programme real content.
4. **"Where the books disagree" as a study mode.** The referee already detects `books_conflict`. Collecting the
   highest-yield textbook disagreements (doses, cut-offs, "most common" claims) is something only a
   multi-textbook grounded system can produce, and examiners love those points.
5. **Exam-frequency-weighted everything.** Use asked-years frequency (the recall groups) to weight the study
   plan, Daily Dose, revision-sheet emphasis and mock blueprints: "this concept was asked in 6 of the last 8
   sittings". Competitors weight by weak areas only; MedNAMA can weight by weak areas *times* exam yield.

## 6. Recommended order

1. **Now (days):** stable public URL; universal level selector; difficulty labels; explanation standard.
2. **Next (1–2 weeks):** photo → Check an answer; Today plan v2 with frequency weighting; "settle the key"
   dispute pages.
3. **Then (weeks):** image MCQs from textbook figures; a human review queue for AI and recalled questions.
4. **Later:** FCPS-II TOACS/viva simulator; voice and Urdu; native app wrappers; gamification beyond streaks.

## Sources

- AMBOSS: [homepage](https://www.amboss.com/), [features](https://www.amboss.com/us/features),
  [AI Mode Learning announcement](https://www.amboss.com/us/newsroom/introducing-amboss-ai-mode-learning),
  [iatroX review](https://www.iatrox.com/blog/amboss-is-no-longer-just-a-q-bank-what-that-means-for-doctors-2026)
- Osmosis: [Osmosis AI launch](https://mygwork.com/news/elsevier-launches-study-companion-osmosis-ai-to-transform-how-medical-students-learn),
  [learning tools](https://www.osmosis.org/features), [product updates 2026](https://www.osmosis.org/blog/osmosis-product-updates-2026?nb=1),
  [iatroX Step 2 CK audit](https://www.iatrox.com/blog/osmosis-usmle-step-2-ck-course-audit-what-is-taught-what-is-tested-and-what-you-still-need-elsewhere)
- Revisable: [homepage](https://www.revisableapp.com/), [App Store](https://apps.apple.com/app/id6451157089)
- iTutor: [homepage](https://itutor.study/)
- PrepFCPS: [homepage](https://www.prepfcps.com/)
- Medmacs: [homepage](https://medmacs.app/)
- NEETPGAI: [homepage](https://neetpgai.com/)
