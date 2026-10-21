# medNAMA — TOS-Based Examination Preparation Feature

Act as a senior full-stack engineer, medical education product designer, and RAG systems architect.

Implement a new, integrated **TOS-Based Preparation** feature in the existing medNAMA application.

The feature should transform medNAMA from a medical knowledge retrieval tool into a structured examination preparation platform, organized around official Tables of Specifications (TOS).

This is an implementation task, not a request for a proposal or mockup. Inspect the existing repository, reuse the current architecture, implement the feature, and test it.

## 1. Core concept

Users preparing for PGET and FCPS should be able to select their examination, navigate the relevant official TOS, choose a topic they have already studied, and use medNAMA to consolidate and test their knowledge.

The workflow is:

**Select examination → Navigate TOS → Select topic → Review concepts → Read short revision notes → Practice MCQs → Review performance.**

The feature should be optimized for rapid revision, active recall, and examination-oriented practice.

Assume the user has already read the relevant topic. Do not treat every session as a beginner's lecture.

## 2. Examination and TOS selection

Introduce a dedicated preparation area in medNAMA.

Support the following hierarchy, adapting it to the actual structure of each uploaded TOS:

- Examination family: PGET or FCPS.
- Examination or paper.
- Specialty, where applicable.
- Subject.
- Topic.
- Subtopic.

Examples include:

- PGET Paper I — Basic Medical Sciences.
- PGET Paper II — Medicine & Allied.
- PGET Paper II — other specialties when their TOS documents are available.
- FCPS examinations, organized according to the actual uploaded specifications.

Do not hardcode the structure of one examination into all other examinations.

Use the uploaded official TOS documents as the source of truth for topic names, hierarchy, published question counts, minimum-question requirements, and other explicit specifications.

Each TOS must have an identifiable source and version.

If a specialty's TOS has not been uploaded or parsed, show that its blueprint is unavailable instead of inventing one.

## 3. TOS navigation interface

Create a clear, responsive interface that allows users to browse their examination blueprint.

Include:

- Examination and specialty selector.
- Searchable subject and topic navigation.
- Expandable topic and subtopic hierarchy.
- Official question count or weighting where specified.
- Topic availability indicators.
- MCQ bank size for each topic, when available.
- Revision-note availability.
- Topic practice entry point.
- Coverage indicators showing which topics have usable content.

Distinguish between:

1. Official TOS question counts.
2. Number of unique MCQs stored in medNAMA.
3. Number of questions attempted by the learner.
4. Learner performance, if available.

Never present the number of stored MCQs as the official examination weighting.

Do not display misleading completion percentages when content has not yet been seeded or the TOS has no numerical weighting.

## 4. Topic learning workspace

When a user selects a topic or subtopic, open a dedicated workspace with four primary sections.

### A. Concept refresher

Provide a concise explanation of the important concepts using medNAMA's existing medical RAG pipeline.

Focus on:

- Core mechanisms.
- Key relationships.
- Clinical correlations.
- Important distinctions.
- High-yield exceptions.
- Common misconceptions and examination traps.

Keep explanations proportional to the topic. Prefer structured explanations, compact tables, and cause-and-effect sequences over lengthy textbook-style prose.

Cite the medical references actually retrieved by the application.

### B. Short revision notes

Display a separate revision sheet optimized for a 1–3-minute review.

Use buzzwords, concise facts, comparison tables, formulas, diagnostic associations, mechanisms, and high-yield distinctions as appropriate.

The notes should remain accessible independently of the question session.

If pre-generated notes exist, use them. If they do not, generate them through the existing evidence-grounded generation pipeline and save them according to the existing persistence architecture.

Avoid regenerating identical notes on every visit.

### C. Topic MCQs

Allow users to practice the questions mapped to the selected topic.

Support:

- Configurable question count.
- Difficulty levels 1–5.
- Mixed-difficulty practice.
- Optional adaptive difficulty when supported by sufficient performance data.
- Individual-question practice.
- Examination mode with answers withheld until submission.
- Explanations after answering or submitting.
- Source references for explanations.

Use validated, pre-seeded questions first. Generate additional questions only when appropriate and supported by evidence.

Do not silently show questions from unrelated topics merely to meet a requested count.

If fewer validated questions are available than requested, clearly indicate the available count.

### D. Session results

At the end of practice, show:

- Questions attempted.
- Correct and incorrect answers.
- Accuracy.
- Performance by difficulty.
- Weak concepts or subtopics.
- Questions worth revisiting.
- Suggested revision notes.

Only calculate metrics supported by recorded attempts.

Do not claim mastery from a small number of questions. Distinguish observed accuracy from estimated mastery.

## 5. MCQ quality and evidence

Use the existing medNAMA question bank and generation architecture.

Every question should remain associated with:

- Examination and TOS version.
- Subject, topic, and subtopic.
- Learning objective.
- Difficulty level.
- Stem and options.
- Correct answer.
- Answer explanation.
- Distractor explanations, where available.
- Source citations.
- Validation status.

Only use questions that have passed the application's validation requirements for the standard practice experience.

Questions generated on demand must pass the same quality checks as seeded questions before being presented as validated content.

If source evidence is missing, conflicting, or insufficient, communicate the limitation rather than inventing an answer.

Do not label AI-generated questions as official past-paper questions without verified provenance.

## 6. Progress and coverage

Where the application already supports user accounts and attempt tracking, integrate with those systems.

Track progress by examination, subject, topic, and subtopic.

Provide:

- Topics practiced.
- Questions attempted.
- Accuracy trends.
- Difficulty-level performance.
- Repeatedly missed concepts.
- Revision-note access.
- Suggested topics for further practice.

Keep examination coverage separate from learner progress.

The official TOS describes the examination blueprint; user performance describes preparation progress. These are related but different concepts.

If a topic has no numerical weighting in the official TOS, show coverage without inventing an official percentage.

If authentication or persistence is not available for a particular workflow, document the limitation and use the smallest compatible implementation.

## 7. TOS-aware mock examinations

Make the topic question bank reusable for full mock examinations.

For PGET Paper I, reproduce the published 100-question subject distribution from the applicable TOS.

For Paper II, use the selected specialty's actual specification.

For FCPS, use the uploaded FCPS blueprint and its explicit requirements rather than assuming the PGET structure applies.

The mock examination engine should:

- Allocate questions according to the official blueprint where counts are specified.
- Respect explicit topic minimums.
- Detect insufficient bank coverage.
- Avoid duplicate questions within the same mock.
- Distinguish official weighting from internal allocation rules.
- Report any deviation from the intended blueprint.

Do not claim a mock is fully TOS-compliant if the bank cannot satisfy the required distribution.

## 8. Architecture and implementation constraints

Before implementation, inspect:

- Existing application structure.
- Current navigation and design system.
- Medical retrieval and reranking.
- MCQ generation and storage.
- TOS ingestion and normalized data.
- Authentication and attempt tracking.
- Existing API routes and services.
- Tests and deployment configuration.

Extend existing components wherever possible.

Do not build a separate application, unrelated database, or duplicate generation pipeline.

Keep TOS data separate from UI code. Examination-specific configuration should be data-driven so new TOS documents can be added without redesigning the feature.

Implement appropriate loading, empty, error, and unavailable-content states.

Ensure the feature works on desktop and mobile.

Preserve existing medNAMA functionality and visual identity. Do not redesign unrelated areas.

## 9. Acceptance criteria

The implementation is complete when:

1. Users can select an available PGET or FCPS examination.
2. The interface reflects the actual uploaded TOS hierarchy.
3. Users can navigate to a topic or subtopic.
4. Available concept refreshers and revision notes are displayed.
5. Users can practice validated MCQs for the selected topic.
6. Difficulty selection works from 1 to 5.
7. Answers and explanations are presented according to the selected practice mode.
8. Session results accurately reflect recorded attempts.
9. Official counts, bank counts, and learner progress are clearly distinguished.
10. Full mock examinations use the correct examination-specific blueprint.
11. Missing content and insufficient bank coverage are handled honestly.
12. Source citations and question provenance are preserved.
13. Existing functionality remains intact.
14. Relevant automated tests pass.

## 10. Implementation workflow

First inspect the codebase and identify the existing components that can support this feature.

Then implement the feature in small, testable increments:

1. TOS data integration.
2. Examination and topic navigation.
3. Topic learning workspace.
4. Revision notes and MCQ practice.
5. Results and progress.
6. TOS-aware mock examinations.
7. Validation and regression tests.

Do not stop after presenting a plan or creating a UI prototype. Implement the working feature.

At completion, report the files changed, tests executed, supported examinations, remaining gaps, and any configuration or migration required.

Prioritize a reliable end-to-end experience for one available TOS before generalizing to all uploaded examinations.s