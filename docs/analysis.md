# medNAMA — FCPS-I Product, Competitive & Local Codebase Audit

## Context

You are running directly inside the local `medNAMA` repository.

The local filesystem/codebase is the **source of truth**.

Do NOT rely on the GitHub repository, README, previous descriptions, or assumptions about medNAMA when determining what currently exists.

Inspect the actual local codebase first.

---

# 1. Audit the Local Repository First

Start by understanding the local project structure.

Inspect the repository recursively and identify:

- frontend
- backend
- database
- API routes
- components
- pages/routes
- services
- models/schemas
- migrations
- scripts
- AI/RAG implementation
- authentication
- quiz engine
- MCQ data
- analytics
- admin functionality
- configuration
- tests
- deployment files

Read the relevant source files rather than relying only on filenames.

Use the actual implementation to determine what features exist.

### Important

Do NOT assume a feature is missing because it isn't documented.

Do NOT assume a feature exists because it is mentioned in a README.

Verify it from the code.

Do NOT rewrite the repository.

This task is an **analysis and planning exercise only** unless a specific implementation is requested later.

---

# 2. Establish the Actual Current State

Create a factual baseline of the local application.

For every major feature determine:

- whether it exists
- where it is implemented
- how it works
- whether it appears complete
- whether it is production-ready
- major limitations
- technical debt

Use this format:

| Feature | Status | Implementation | Quality | Problems |
|---|---|---|---|---|
| MCQ bank | | | | |
| Quiz engine | | | | |
| Exam mode | | | | |
| Tutor mode | | | | |
| AI MCQ generation | | | | |
| RAG | | | | |
| Textbook retrieval | | | | |
| Citations | | | | |
| Figures | | | | |
| Quiz history | | | | |
| Analytics | | | | |
| Authentication | | | | |
| Admin | | | | |
| Mobile UX | | | | |
| PWA | | | | |
| Offline | | | | |
| Spaced repetition | | | | |
| Adaptive learning | | | | |
| Weak-topic detection | | | | |
| Flashcards | | | | |
| Study planner | | | | |
| Exam readiness | | | | |
| Gamification | | | | |
| FCPS taxonomy | | | | |
| Recall system | | | | |
| Question review | | | | |
| Question reporting | | | | |

For every important finding, reference the relevant local file/path.

---

# 3. Understand the Existing Product Before Designing Changes

Before proposing anything, explain:

### What medNAMA actually is today

Answer:

- What is the primary user journey?
- What happens when a user opens the app?
- How does a user select questions?
- How is a quiz generated?
- How are answers scored?
- How are explanations generated?
- How does RAG work?
- How are references obtained?
- How are figures handled?
- What gets stored after an attempt?
- What analytics already exist?
- What can the admin control?
- What can the student customize?

Then identify the current product model:

```text
Current user journey
↓
Current learning loop
↓
Current strengths
↓
Current limitations