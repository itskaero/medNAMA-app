# MedNAMA — Master Product Audit, Enhancement & Implementation Specification

## 1. Your role

Act as the lead software architect, senior full-stack engineer, medical education product designer, AI/RAG engineer, and quality-assurance lead for **MedNAMA**.

Repository: https://github.com/itskaero/medNAMA-app/

MedNAMA is an existing medical learning platform with substantial functionality already implemented. Your job is to **understand, preserve, improve and extend the existing application**, not replace it with a new one.

The scope covers two connected product initiatives:

1. **Core MedNAMA:** source-grounded medical learning, revision notes, AI chat, MCQs, adaptive practice, mistake analysis and advanced knowledge tools.
2. **TOACS/Viva Helper:** an integrated FCPS Part II examination-preparation system featuring adaptive viva, practical stations, timed circuits, transparent assessment, structured debriefing and personalised remediation.

These must be treated as parts of the same product, sharing existing infrastructure wherever appropriate.

## 2. Non-negotiable engineering constraints

- Do not rebuild the application from scratch.
- Do not replace the existing framework, architecture, routing, database, authentication, UI system, RAG pipeline or deployment setup without a demonstrated technical reason.
- Do not duplicate working functionality merely to create a cleaner-looking implementation.
- Do not remove, rename or break existing features without an explicit migration plan and justification.
- Inspect the repository before making architectural assumptions.
- Identify existing implementations before creating new components, services, tables, APIs or state models.
- Reuse existing authentication, user preferences, content storage, notes, MCQ engine, retrieval services, citations, mistake tracking, revision scheduling and offline infrastructure where suitable.
- Avoid introducing heavyweight local language models or unnecessary infrastructure.
- Keep medical assessment content and clinical safety logic separate from presentation components.
- Prefer small, reversible, tested changes over sweeping refactors.
- Never report a feature as implemented merely because a plan, placeholder, mock UI or interface exists.
- Do not fabricate repository findings, test results, medical sources, examination requirements or feature-completion status.

If the repository contradicts any assumptions in this specification, report the discrepancy and adapt the implementation to the actual codebase while preserving the product requirements.

---

# PART I — MEDNAMA CORE PLATFORM

## 3. Product vision

MedNAMA should become an adaptive medical learning platform connecting:

- A searchable corpus of approximately 25 medical textbooks.
- Source-grounded AI chat.
- Hybrid textbook and examination-oriented revision notes.
- Topic-based and blueprint-driven MCQs.
- Personalised mistake analysis.
- Spaced revision.
- Knowledge and concept comparison tools.
- A competency-based FCPS Part II TOACS/Viva preparation system.

The platform should support MBBS, PGET, FCPS Part I and FCPS Part II. FCPS Part I may be used as an initial validation priority, but the architecture must not restrict the product to that examination.

The central learning loop is:

**Learn → Practise → Receive feedback → Identify weaknesses → Revise → Reassess → Retain.**

The home experience should guide learners toward their next best action without preventing them from browsing, searching or choosing their own study path.

## 4. Repository audit: core platform

Before modifying anything, inspect and document:

- Framework, application structure and routing.
- Existing chat and RAG entry points.
- Retrieval, reranking, chunking and context assembly.
- Model/provider integrations and prompt construction.
- Citation generation, validation and source navigation.
- Textbook ingestion and page/figure metadata.
- Revision-note generation and rendering.
- MCQ generation, storage, deduplication, explanations and assessment logic.
- User progress, mistake analysis and spaced-revision systems.
- Existing confusion/comparison features and concept relationships.
- Authentication, database schema, APIs and permissions.
- Offline caching, local persistence and sync.
- Existing tests, analytics, logging, error handling and deployment.
- Existing UI components, design tokens, navigation and responsive behaviour.

For every relevant feature, classify it as:

- **EXISTS:** implemented and verified.
- **ENHANCE:** works but needs improvement.
- **MODIFY:** exists but requires a justified change.
- **MISSING:** no adequate implementation exists.
- **VERIFY:** code exists, but its behaviour or correctness is uncertain.
- **BLOCKED:** requires a dependency, permission, source or infrastructure change.

Provide repository paths, relevant functions and data models as evidence. Do not create replacements for features classified as EXISTS unless the audit demonstrates a specific defect.

### Required first deliverable

Produce an architecture map, feature inventory, dependency map, risk register, test baseline and prioritised implementation plan.

Identify the smallest viable path that connects existing systems into the agreed learning experience.

**Do not begin a broad implementation before this audit and plan are complete.** After presenting the audit and plan, proceed in small, testable increments unless a critical ambiguity or destructive decision requires clarification.

## 5. Home, navigation and learner experience

Retain the existing home and navigation patterns where they work.

Enhance the learner experience to provide:

- A personalised next-best action.
- Universal search.
- Syllabus and subject browsing.
- Direct access to weak topics and due revisions.
- Recent activity and learning continuity.
- MCQ practice and examination preparation.
- Contextual links between topics, notes, questions and mistakes.

Use a hybrid navigation model: syllabus hierarchy, universal search and direct access to weak or due topics.

Onboarding should be lightweight. Capture examination level and subject preferences where necessary; make examination dates optional.

The interface should be responsive, accessible and consistent with MedNAMA's existing design system. Do not create an unrelated visual identity for every new module.

## 6. AI chat and RAG

Retain and enhance the existing **Chat with MedNAMA** experience rather than replacing it.

Where supported by existing architecture, provide relevant context from:

- Current topic or note.
- Learner's examination level.
- Recent mistakes and weak competencies.
- Relevant retrieved textbook passages.
- The current station or question.
- Appropriate recent conversation context.

Add contextual actions where useful, such as opening a linked note, creating a targeted practice session or reviewing a related concept.

### Medical accuracy requirements

Implement improvements to the existing retrieval and grounding pipeline, guided by evaluation rather than assumptions.

Distinguish between:

1. Claims supported by retrieved textbook material.
2. Approved external evidence.
3. Clearly labelled AI-generated educational enrichment.
4. Unsupported or uncertain claims.

Citation presence is not sufficient: evaluate whether the cited source actually supports the claim.

- Preserve source/page metadata.
- Verify that citations point to relevant passages.
- Do not present an unsupported inference as a textbook fact.
- Express uncertainty when evidence is insufficient.
- Flag meaningful disagreement between sources.
- Preserve source and content versions where relevant.
- Do not silently rewrite accepted answer keys or historical assessments.

For high-risk clinical claims, use stronger grounding and abstention safeguards.

## 7. Revision notes and textbook reading

The note system must combine **textbook fidelity with examination usefulness**.

Retain the existing textbook ingestion, retrieval, citation and rendering pipeline wherever possible.

Notes should support:

- Source-grounded explanations.
- Examination-focused summaries and buzzwords.
- Relevant tables, comparison frameworks and figures.
- Clear separation between textbook material and AI enrichment.
- Numbered references at the end.
- Clickable references that open the correct textbook page where supported by existing metadata.
- A book-page reading experience rather than a generic dump of disconnected bullet points.
- Preservation of user annotations and meaningful change notifications when source material changes.

Generate or enhance notes on demand where appropriate. Do not unnecessarily pre-generate every possible note.

Respect textbook licensing and access restrictions. Do not assume the existence of rights to redistribute full books or figures.

## 8. MCQ system

Use the existing MCQ infrastructure as the foundation.

Support a combination of:

- A reviewed, blueprint-mapped core question bank.
- Targeted adaptive generation.
- Topic coverage and examination blueprint mapping.
- Exact and semantic deduplication.
- Source provenance.
- Quality checks and review status.
- Separate adaptive practice and fixed-blueprint mock examinations.

Explanations should use a layered structure:

1. Decisive clue.
2. Correct answer and core rationale.
3. Explanation of all options.
4. Linked revision note.
5. Numbered, source-grounded references.
6. Optional deeper explanation.

Collect confidence ratings after each practice question **before revealing the answer**, consistent with the learner's established preference. Do not disrupt timed mock examinations with answer-revealing prompts.

Measure coverage, recall, application, discrimination, confidence calibration and retention. Do not infer mastery from one attempt or a small sample.

## 9. Advanced learning tools

Audit the current implementation of the following before changing it.

### Confusion Engine

Identify commonly confused concepts and compare them through:

- High-yield differentiating features.
- Relevant textbook references.
- Personal mistake patterns.
- Confidence and error patterns.
- Interactive discrimination practice.
- Targeted revision and reassessment.

### Textbook Battle

Support evidence-based comparison of conflicting textbook statements.

Classify disagreements where possible, explain their context and clinical or examination implications, cite the underlying sources, and clearly label any AI synthesis. Do not invent a consensus when sources genuinely disagree.

### Concept Graph

Represent relevant relationships between medical concepts.

Distinguish source-supported relationships from AI-inferred relationships. Preserve provenance and avoid presenting speculative connections as established facts.

### Community content

Keep community-contributed material separate from verified textbook-grounded or reviewed assessment content. Provide appropriate provenance and review states.

## 10. Adaptive revision, privacy and commercial model

Reuse existing progress and revision systems.

Revision prioritisation should consider due dates, demonstrated weaknesses, examination relevance, coverage gaps and previous performance.

Support eligible offline access to saved notes, cached MCQs and flashcards, annotations and queued changes where the existing architecture permits.

AI generation may require connectivity. Clearly represent pending operations and sync status.

Provide appropriate account security, private-by-default mastery data, data export and deletion, and optional sharing.

The planned commercial model is:

- Free tier.
- Premium subscription.
- Time-limited Exam Pass.

Preserve access to an essential learning loop, including meaningful explanations and citations. Paid features may expand usage and access to advanced tools. Do not invent prices, quotas or entitlement rules without an explicit product decision.

---

# PART II — TECHNICAL FINDINGS TO VERIFY

The following are previously identified findings and hypotheses, **not a substitute for checking the current repository**. Confirm each before acting on it.

## 11. Existing retrieval pipeline

Prior inspection indicated:

- Hybrid vector retrieval and PostgreSQL full-text search.
- Reciprocal Rank Fusion.
- A cross-encoder reranker using `cross-encoder/ms-marco-MiniLM-L-6-v2`.
- An optional `ncbi/MedCPT-Cross-Encoder`.
- Approximately 16 candidates and 5–6 context passages.
- BGE-based embeddings.
- Citation validation that checked source/page references but did not fully establish claim-level entailment.
- Examination level affecting only a limited part of the prompt.
- Generation potentially allowing model knowledge or reasonable inference even when retrieved evidence was weak.
- Frontend streaming simulated after generation had already been collected.

Earlier retrieval testing reportedly found:

- MedCPT offered limited improvement while adding latency and memory use.
- BGE v2-m3 was too resource-intensive for the intended CPU environment.
- Some clinically relevant retrieval cases were missed despite otherwise promising benchmark results.

Reproduce the relevant benchmarks before deciding whether to remove or replace a component.

Prioritise chunk quality, retrieval misses, source metadata and end-to-end grounded-answer evaluation before introducing a larger model.

Do not confuse a retrieval benchmark with proof of clinical correctness.

## 12. Hardware and deployment constraints

Previously stated operating constraints:

- An ingestion machine with a 16 GB RAM, seventh-generation i7-class CPU.
- A production server with approximately 4 GB RAM and a fourth-generation i5-class CPU.
- Production inference should primarily use an appropriate API model rather than a heavyweight local LLM.

Verify these constraints against the current deployment before selecting models or dependencies.

Keep ingestion and production inference requirements distinct. Avoid adding a local model that consumes a substantial fraction of production memory without measured justification.

---

# PART III — TOACS / VIVA HELPER

## 13. Product objective

Build an integrated **FCPS Part II TOACS/Viva Helper** within MedNAMA.

The module must support a universal, reusable assessment engine with specialty-specific station configurations. It should prepare candidates for clinical reasoning, oral viva, practical tasks, interpretation, prioritisation, communication and patient safety.

It is an educational preparation tool, not an official CPSP examination system or a guarantee of examination success.

Use approved and appropriately licensed sources. Do not claim that recalled questions are official, guaranteed to recur or predictive of the next examination.

## 14. All 30 discovery decisions — binding product requirements

The following decisions were made explicitly during product discovery. Preserve them in the implementation plan.

### Q1. Specialty architecture

**Decision:** universal FCPS Part II framework with reusable station infrastructure and specialty-specific modules.

Use a shared engine, not a generic assessment forced onto every specialty. Timing, tasks, scoring criteria and examination expectations must be specialty-appropriate.

### Q2. What the examiner evaluates

**Decision:** holistic assessment of knowledge, reasoning, prioritisation, communication, practical steps and patient safety, adapted to each station rubric.

Distinguish critical unsafe decisions from minor omissions. Explain deductions and their evidence.

### Q3. Viva interaction mode

**Decision:** structured text-based adaptive viva first, with a separate voice-examination mode when supported.

Voice must not delay a reliable text examiner. Preserve existing chat infrastructure where suitable.

### Q4. Station content source

**Decision:** hybrid reviewed core stations plus AI-generated practice variants grounded in approved source material.

Generated variants must be checked against their modified clinical scenario. Do not automatically reuse an answer key when changed details alter the correct response.

### Q5. Station workflow

**Decision:** both guided learning and timed examination modes.

Guided mode supports hints and teaching. Timed mode preserves exam conditions and hides answers and feedback until submission.

### Q6. Scoring model

**Decision:** hybrid scoring combining:

- Explicit checklist marks.
- Critical safety criteria.
- Rubric-weighted AI assessment of reasoning and communication.

Award credit for valid alternative answers. Critical safety findings must be grounded in the station rubric rather than arbitrary penalties.

### Q7. Candidate response formats

**Decision:** progressive multimodal delivery.

1. Typed responses, structured selections and step-by-step actions.
2. Images and investigation interpretation.
3. Voice responses.
4. Richer branching clinical and procedural simulations.

Each station must specify its allowed inputs and expected response formats.

### Q8. Station library

**Decision:** individual topic-based stations plus configurable exam circuits.

Support browsing by specialty, topic, presentation and competency, alongside configurable timed circuits. Mock circuits must follow a defined blueprint.

### Q9. Post-station experience

**Decision:** full structured debrief plus adaptive remediation and scheduled follow-up.

Include criterion-level marks, safety flags, reasoning errors, model performance, references, linked notes, targeted MCQs, repeat stations and follow-up scheduling.

Distinguish knowledge gaps from performance, communication, prioritisation, interpretation and time-management errors.

### Q10. Difficulty

**Decision:** hybrid adaptive difficulty with defined levels, learner choice and progression based on demonstrated competence.

Use appropriate difficulty dimensions, not simply obscure facts. Keep mock circuits blueprint-driven and comparable.

### Q11. Clinical media

**Decision:** hybrid pipeline prioritising verified real images, clearly labelled synthetic teaching visuals and text fallback.

Use appropriate permissions, provenance and attribution. Validate image interpretation and answer keys. Do not treat unverified synthetic diagnostic images as authoritative assessment material.

### Q12. Rubric creation

**Decision:** structured AI-assisted rubric drafts grounded in approved references, reviewed before becoming assessment-ready.

Version rubrics, preserve source references and review history, and validate the actual station–rubric combination. A modified scenario requires reassessment of its rubric.

### Q13. Examiner personas

**Decision:** selectable supportive tutor, realistic examiner and strict examiner.

Personas change tone, pacing and probing style, not clinical truth or scoring standards.

### Q14. Pressure simulation

**Decision:** configurable calm, realistic and high-pressure modes, appropriate to the station.

Use purposeful challenges, interruptions and time pressure only where appropriate. Do not distort the scoring rubric or reward unsafe haste.

### Q15. Viva question generation

**Decision:** structured adaptive questioning based on a reviewed question framework with contextual follow-ups.

Maintain required competency coverage and station timing. Adaptive probing must not undermine mock-exam comparability.

### Q16. Incorrect or unsafe answers

**Decision:** mode-dependent handling.

- Learning mode: probe, teach and explain.
- Timed mode: record the response, preserve examination integrity and debrief afterward.
- Apply safeguards for dangerous misconceptions without unnecessarily revealing answers in a mock.

Distinguish an incorrect response from an unsafe action.

### Q17. Performance tracking

**Decision:** competency-level tracking against each station rubric.

Track relevant competencies such as clinical knowledge, reasoning, prioritisation, communication, practical steps and patient safety. Preserve station context and rubric version. Avoid unsupported cross-specialty comparisons.

### Q18. Integration with core MedNAMA

**Decision:** deep integration linking weaknesses to notes, MCQs, mistake analysis and revision scheduling, reusing existing architecture.

A poor viva response must be classified correctly before remediation is selected. Do not assign MCQs indiscriminately for every performance problem.

### Q19. Specialty configuration

**Decision:** shared station engine with specialty-specific blueprints, station types, rubrics, competencies and media requirements.

A module must be validated before being described as exam-ready.

### Q20. Content management

**Decision:** admin dashboard for drafting, reviewing, approving, versioning and publishing stations.

Use role-based permissions and an audit trail. Published station or rubric changes must not silently alter historical assessments.

### Q21. Historical exam recollections

**Decision:** separate supplementary recollection bank, clearly labelled unverified until reviewed and corroborated.

Preserve provenance, dates and review status. Exclude personally identifying patient details and confidential or improperly obtained examination material. Repeated recollections do not guarantee authenticity or future recurrence.

### Q22. Learner preparation

**Decision:** flexible preparation with a brief first-use tutorial, optional station instructions and direct entry for experienced candidates.

Do not reveal answers through station instructions in timed mode.

### Q23. Timing

**Decision:** mode-dependent timing.

- Guided practice: optional timers and appropriate pause/resume.
- Timed mocks: blueprint-defined durations and transitions.
- Specialty formats: configurable according to validated requirements.

Define behaviour for network loss, backgrounding, interruptions and session recovery. Never silently extend a timed examination.

### Q24. AI assessment uncertainty

**Decision:** score supported criteria, explain uncertainty and flag ambiguous or disputed criteria for review.

Use stronger safeguards for critical safety deductions and pass/fail conclusions. Test against expert-marked examples. Do not treat multiple AI evaluations alone as proof of correctness.

### Q25. Score challenges

**Decision:** structured review workflow.

Learners can challenge a specific criterion, provide a rationale or source, and track the review outcome. Preserve the original result and record any correction and reviewer rationale.

### Q26. Offline support

**Decision:** hybrid offline support.

Cache eligible content, save draft responses, recover interrupted sessions and mark AI-dependent assessment as pending until connectivity returns. Prevent duplicate submissions and preserve assessment integrity.

### Q27. Dashboard

**Decision:** balanced personalised dashboard with:

- Continue/Start Practice.
- Station browsing.
- Mock circuits.
- Concise weakness and revision summaries.

Explain recommendations and preserve free learner choice.

### Q28. Confidence tracking

**Decision:** collect confidence after every response before feedback is revealed.

Use confidence alongside accuracy and safety-critical errors. Identify recurring overconfidence and underconfidence only when sufficient evidence exists. Do not let confidence replace competence assessment.

### Q29. Release strategy

**Decision:** phased, usable releases.

The first release must deliver a complete learning loop with reviewed stations, guided and timed modes, rubric-based debriefs and basic integration. Expand in tested phases to image interpretation, voice, richer simulations and additional specialty modules.

### Q30. First implementation priority

**Decision: A — repository audit and implementation plan first.**

Map existing assessment, chat, notes, authentication, storage and offline infrastructure; identify reusable components, gaps, risks and the smallest viable implementation path before changing code.

---

# PART IV — TOACS FUNCTIONAL SPECIFICATION

## 15. Station data model

Inspect existing schemas before introducing new ones. Where functionality is missing, design a versioned, extensible representation for:

- Specialty and module.
- Topic, presentation and competency tags.
- Station type and response modalities.
- Candidate instructions.
- Clinical scenario and staged information release.
- Available findings, images and investigation results.
- Expected actions and acceptable alternative responses.
- Marking checklist, criterion weights and critical safety criteria.
- Time limits and permitted interactions.
- Examiner question framework and adaptive follow-up rules.
- Model performance or answer guidance.
- Approved sources and page-level references.
- Difficulty dimensions.
- Review and publication status.
- Content and rubric versions.
- Provenance, licensing and media verification.
- AI-generated variant lineage and validation status.

Keep assessment logic separate from presentation. Avoid hard-coded specialty assumptions inside the generic engine.

## 16. Assessment attempt lifecycle

Model the lifecycle explicitly, reusing existing persistence where possible.

1. Select a station or circuit.
2. Resolve the applicable station and rubric versions.
3. Display permitted instructions.
4. Start guided or timed attempt.
5. Collect candidate responses and relevant confidence ratings.
6. Reveal only information allowed by the station workflow.
7. Save progress safely.
8. Submit the attempt.
9. Score rubric criteria and identify uncertainty.
10. Produce the structured debrief.
11. Link weaknesses to existing learning resources.
12. Schedule or recommend remediation.
13. Record subsequent reassessment and performance trends.

Preserve the original submitted response, the applicable content versions and the scoring rationale. Corrections must be auditable.

## 17. Guided learning mode

- Allow hints and progressive disclosure.
- Support contextual teaching and follow-up questions.
- Explain incorrect reasoning without humiliating the learner.
- Clearly distinguish prompted performance from independent performance.
- Do not award full independent-performance credit for answers obtained after hints when the rubric distinguishes these behaviours.
- End with a useful debrief and targeted learning plan.

## 18. Timed examination mode

- Use validated station timing and circuit composition.
- Hide model answers, scoring feedback and hints until submission.
- Record answers and confidence without exposing correctness.
- Constrain adaptive questioning to the required competency framework.
- Record interruptions and recovery events.
- Do not adapt examination difficulty silently.
- Preserve comparable scoring rules across attempts.
- Clearly label simulated results; never imply that AI scores are official FCPS results.

## 19. Examiner engine

Use structured adaptive questioning rather than unconstrained question generation.

The examiner should:

- Begin with a reviewed framework.
- Ask one question at a time where the station format calls for it.
- Probe reasoning, prioritisation and contradictions.
- Introduce clinically justified changes in findings.
- Maintain required coverage.
- Respect station time limits.
- Adapt persona and pressure without changing scoring standards.
- Keep every assessment decision traceable to the station rubric.

For voice mode, reuse the same assessment framework. Add voice only when the current architecture and supported APIs permit reliable speech interaction. Provide a text fallback.

## 20. Scoring and debrief

For every scored criterion, capture where supported:

- Expected behaviour.
- Observed candidate response.
- Marks available and awarded.
- Rationale for the score.
- Source or rubric reference.
- Uncertainty or ambiguity.
- Whether the criterion is safety-critical.

The debrief should cover:

1. Overall result, with appropriate qualification.
2. Criterion-level marks.
3. Critical safety findings.
4. Reasoning and prioritisation.
5. Communication and practical performance where assessed.
6. Strong example performance.
7. Source-linked learning points.
8. Error classification.
9. Recommended notes, MCQs or repeat stations.
10. Follow-up practice and later reassessment.

Do not provide a definitive pass/fail conclusion when the rubric or evidence does not support it.

## 21. Admin workflow

Build an admin dashboard only after confirming the existing permission model and UI architecture.

Provide workflows for:

- Drafting and editing station content.
- Creating and reviewing rubric drafts.
- Validating generated variants.
- Reviewing media and references.
- Approving or rejecting content.
- Publishing and unpublishing.
- Version history and change comparison.
- Retiring outdated content.
- Reviewing learner-reported scoring defects.
- Tracking review status and corrective actions.

Protect published content and historical attempts from silent mutation. Enforce authorisation on the server, not only in the interface.

## 22. Starter content and specialty rollout

Do not claim full specialty coverage simply because the engine supports it.

Start with a deliberately scoped, reviewed pilot module chosen according to available authorised content and repository readiness. Establish:

- A representative set of station types.
- Reviewed station scenarios.
- Rubrics and critical safety criteria.
- Verified references and media.
- Guided and timed examples.
- Expert-marked assessment examples.
- Test cases for acceptable alternative answers.

Then expand through specialty-specific modules.

Do not invent official examination blueprints or exact station counts. Where official requirements are unavailable, mark the configuration as provisional and document the source gap.

---

# PART V — QUALITY, VALIDATION AND RELEASE

## 23. Medical and assessment validation

The platform should undergo evaluation before consequential assessment claims are made.

Establish a test set containing expert-reviewed station scenarios, acceptable alternative answers, critical safety cases and representative candidate responses.

Evaluate:

- Clinical correctness.
- Rubric adherence.
- Scoring consistency.
- Valid alternative-answer recognition.
- Citation relevance.
- Detection of critical unsafe actions.
- False safety flags.
- Uncertainty handling.
- Voice transcription errors when voice is introduced.
- Reproducibility across repeated assessments.
- Appropriate behaviour when evidence is missing.

Medical experts or suitably qualified reviewers should validate content and scoring rules before assessment-ready publication.

For public beta, support structured learner feedback and issue reporting. Triage defects by safety risk, severity, impact, frequency and supporting evidence. Review consequential medical and scoring corrections before publication.

## 24. Engineering tests

Extend existing tests rather than creating an isolated testing strategy.

Include:

- Unit tests for station validation, rubric scoring and state transitions.
- Integration tests for existing authentication, storage and APIs.
- Regression tests for existing chat, RAG, notes, MCQs and revision.
- End-to-end tests for guided and timed station workflows.
- Tests for hidden answers before submission.
- Tests for valid alternative answers.
- Tests for uncertain scoring and score challenges.
- Tests for versioned historical attempts.
- Offline recovery and duplicate-submission tests.
- Permission and authorisation tests for admin workflows.
- Accessibility, mobile and responsive-layout checks.
- Performance and API-cost monitoring.

Do not report a test as passing unless it has actually been run.

## 25. Analytics

Use analytics to improve the product, not merely maximise session length.

Track appropriate measures such as:

- Successful station completion.
- Learner return and follow-up completion.
- Improvement on repeat competency assessments.
- Performance and confidence calibration.
- Common scoring disputes and corrected assessments.
- Retrieval and citation quality.
- Session recovery and failed submissions.
- Content review and correction rates.
- AI latency and operating cost.

Keep privacy in mind. Collect only what is necessary and make appropriate controls available.

## 26. Implementation phases

### Phase 0 — Audit and plan

- Inspect repository and current working tree.
- Establish the existing architecture and test baseline.
- Verify the technical findings in Part II.
- Map current functionality to all product requirements.
- Identify reusable services and missing capabilities.
- Produce the architecture map, feature inventory, risks and dependency map.
- Define the smallest viable first release.
- Do not make sweeping changes.

### Phase 1 — End-to-end TOACS foundation

Subject to audit findings, prioritise:

- Reusable station representation.
- Reviewed starter content.
- Guided and timed station workflows.
- Structured text viva.
- Versioned checklist and rubric scoring.
- Transparent post-station debrief.
- Basic competency tracking.
- Links to existing notes, MCQs and revision.
- Foundational error reporting.

Deliver a coherent usable experience, not disconnected screens.

### Phase 2 — Content operations and quality

- Admin authoring and review workflow.
- Rubric and content versioning.
- AI-assisted drafts and reviewed variants.
- Structured score challenges.
- Stronger evaluation and regression testing.
- Improved uncertainty and safety handling.

### Phase 3 — Adaptive learning

- Targeted remediation.
- Scheduled repeat stations.
- Confidence calibration.
- Adaptive practice difficulty.
- Personalised dashboard and recommendations.
- Longitudinal competency trends.

### Phase 4 — Multimodal expansion

- Verified image and investigation stations.
- Voice viva where supported.
- Richer branching cases and simulations.
- Additional specialty modules.
- Further offline resilience.

Reorder phases if the repository reveals dependencies, but preserve the agreed product decisions and safety requirements.

---

# PART VI — REQUIRED WORKING METHOD

## 27. Before changing code

First:

1. Inspect the repository structure and working tree.
2. Read the project instructions and relevant documentation.
3. Identify application entry points and existing implementations.
4. Inspect database schemas and migrations.
5. Trace the actual request lifecycle for chat, retrieval, notes and MCQs.
6. Identify reusable components and service boundaries.
7. Run the available tests and record the baseline.
8. Compare current functionality with this specification.
9. Present a prioritised implementation plan with paths, dependencies, risks and validation criteria.

Do not stop after merely listing files. Trace the real implementation sufficiently to determine what can be reused.

## 28. During implementation

For each increment:

1. State the requirement being implemented.
2. Identify the existing implementation and files involved.
3. Make the smallest justified change.
4. Add or update relevant tests.
5. Run those tests.
6. Check regressions and security implications.
7. Update documentation where appropriate.
8. Report what is genuinely complete and what remains.

Do not implement all phases in one giant refactor.

Avoid speculative dependencies, unnecessary database migrations and duplicated state systems.

## 29. Definition of done

A feature is complete only when:

- Its actual implementation exists.
- It integrates with the current application.
- Relevant tests pass.
- Existing functionality remains intact.
- Errors and uncertainty are handled.
- Data and permissions are appropriate.
- The interface reflects real state rather than mock data.
- Documentation and completion status are accurate.

A screen without working persistence, a rubric without validation, or an AI answer without appropriate grounding is not a complete feature.

## 30. Final audit and handover

When an implementation milestone is complete, provide:

- Changes made and files affected.
- Existing components reused.
- Database or configuration changes.
- Tests executed and their actual results.
- Features completed, partial and outstanding.
- Known limitations and unresolved risks.
- Medical content requiring review.
- Deployment or migration requirements.
- Next recommended implementation increment.

Maintain a traceable mapping from the product requirements to implementation and tests.

## Final instruction

**Begin with Phase 0: repository audit and implementation plan.**

Do not rebuild MedNAMA. Do not assume every proposed feature is missing. Discover what already works, preserve it, and extend it deliberately.

The objective is to turn MedNAMA into a reliable, source-grounded adaptive medical learning platform with a robust FCPS Part II TOACS/Viva Helper—while minimising disruption, avoiding duplicate infrastructure, and keeping clinical accuracy and assessment integrity central to every decision.