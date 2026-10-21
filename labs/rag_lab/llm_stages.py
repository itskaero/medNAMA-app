"""Optional LLM stages: question understanding, evidence coverage, answer planning, and an AI judge.

Every stage asks for structured JSON and nothing else. None of them asks for, stores or shows
chain-of-thought: the planner returns a plan (sections and evidence requirements), not reasoning.
All use production's chat_completion() at temperature 0; the role is RAG_LAB_ANALYSIS_ROLE.
"""

from __future__ import annotations

import json
from typing import Any

from . import settings

from app.llm import chat_completion  # noqa: E402

PROMPT_VERSIONS = {"understanding": "qu-1", "coverage": "cov-1", "plan": "plan-1", "judge": "judge-1"}

INTENTS = ("definition", "mechanism", "clinical_features", "diagnosis", "investigation", "management", "treatment",
           "differential", "comparison", "complication", "prognosis", "anatomy", "physiology", "pharmacology",
           "pathology", "clinical_case", "general_explanation")


def _json_call(system: str, user: str, max_tokens: int = 700) -> dict[str, Any]:
    raw = chat_completion([{"role": "system", "content": system}, {"role": "user", "content": user}],
                          json_mode=True, temperature=0.0, max_tokens=max_tokens, thinking=False,
                          label="rag-lab", role=settings.ANALYSIS_ROLE)
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {"_raw": raw}
    except ValueError:
        return {"_raw": raw, "_error": "invalid JSON"}


def passages_block(context: list[Any]) -> str:
    """Numbered evidence as the LLM sees it, with the exact [Book, Page] label to cite."""
    out = []
    for i, c in enumerate(context, 1):
        title = c.book.title if c.book else "Unknown Textbook"
        out.append(f"[P{i}] [{title}, Page {c.page_number}] (chapter: {c.chapter or 'n/a'})\n{c.content}")
    return "\n\n".join(out) or "NO PASSAGES."


# ── question understanding ────────────────────────────────────────────────────

def understand(question: str, level: str) -> dict[str, Any]:
    system = (
        "You analyse a medical exam question before evidence is retrieved. Return JSON only:\n"
        '{"intent": one of ' + json.dumps(list(INTENTS)) + ',\n'
        ' "topic": "the condition/drug/structure asked about",\n'
        ' "answer_type": "short fact | explanation | list | comparison | stepwise management | case answer",\n'
        ' "exam_level": "' + level + '",\n'
        ' "required_components": ["the 2-6 things a complete answer at this level must contain"]}\n'
        "Rules: required_components are about WHAT the question asks, not everything known about the topic. "
        "A definition question does not require management. Do not answer the question."
    )
    out = _json_call(system, f"EXAM LEVEL: {level}\nQUESTION: {question}", max_tokens=400)
    if out.get("intent") not in INTENTS:
        out["intent_raw"] = out.get("intent")
        out["intent"] = "general_explanation"
    out["required_components"] = [str(x) for x in (out.get("required_components") or [])][:6]
    return out


# ── evidence coverage ─────────────────────────────────────────────────────────

def coverage(question: str, level: str, required: list[str], context: list[Any]) -> dict[str, Any]:
    system = (
        "You check whether retrieved textbook passages contain what is needed to answer a question. "
        "You do NOT answer it. Return JSON only:\n"
        '{"required": ["component", ...],\n'
        ' "covered": [{"component": "...", "passages": ["P1", "P3"]}],\n'
        ' "missing": ["component not stated in any passage"],\n'
        ' "coverage_score": 0.0-1.0 (covered / required)}\n'
        "A component is covered only if a passage STATES it, not merely mentions the topic."
        + ("" if required else " First list the 2-6 components a complete answer must contain, as 'required'.")
    )
    user = (f"EXAM LEVEL: {level}\nQUESTION: {question}\n"
            + (f"REQUIRED COMPONENTS: {json.dumps(required)}\n" if required else "")
            + f"\nPASSAGES:\n{passages_block(context)}")
    out = _json_call(system, user, max_tokens=600)
    req = [str(r) for r in (out.get("required") or required)]
    missing = [str(m) for m in (out.get("missing") or [])]
    norm = lambda s: " ".join(str(s).lower().split())  # noqa: E731
    gone = {norm(m) for m in missing}
    # The model sometimes lists a component as both covered and missing, or as covered with no
    # passage; neither counts. The score comes from the explicit missing list, never the covered one.
    covered = [c for c in (out.get("covered") or []) if isinstance(c, dict) and c.get("passages")
               and norm(c.get("component", "")) not in gone]
    out["required"], out["covered"], out["missing"] = req, covered, missing
    if req:
        out["coverage_score"] = round(max(0.0, 1.0 - len(missing) / len(req)), 2)
    return out


# ── answer plan (structure only; no reasoning text) ───────────────────────────

def plan(question: str, level: str, understanding: dict | None, cov: dict | None, context: list[Any]) -> dict[str, Any]:
    system = (
        "Plan the structure of an exam answer before it is written. Return JSON only - a plan, not an answer, "
        "and no explanation of your reasoning:\n"
        '{"answer_structure": ["section 1", "section 2", ...],\n'
        ' "evidence_requirements": ["which claim must be supported by which passage, e.g. mechanism <- P2"],\n'
        ' "state_as_insufficient": ["parts the passages do not support, to be flagged rather than invented"],\n'
        ' "length": "brief | standard | detailed"}\n'
        "Order sections by what the question asks first. Keep to what was asked."
    )
    user = (f"EXAM LEVEL: {level}\nQUESTION: {question}\n"
            + (f"QUESTION ANALYSIS: {json.dumps(understanding)}\n" if understanding else "")
            + (f"EVIDENCE COVERAGE: {json.dumps({k: cov.get(k) for k in ('covered', 'missing', 'coverage_score')})}\n" if cov else "")
            + f"\nPASSAGES:\n{passages_block(context)}")
    return _json_call(system, user, max_tokens=500)


# ── AI judge (never ground truth) ─────────────────────────────────────────────

AI_JUDGE_LABEL = "AI evaluation — not ground truth"
RUBRIC = ("accuracy", "completeness", "relevance", "grounding", "citation_correctness", "reasoning_quality",
          "exam_level_appropriateness", "overall")
PASS_FAIL = ("factual_correctness", "answered_actual_question", "major_omissions", "unsupported_claims",
             "citation_errors", "hallucination")


def judge(question: str, level: str, answer: str, context_text: str, expected: list[str] | None,
          gold_notes: str | None) -> dict[str, Any]:
    system = (
        "You grade a medical exam answer. Score 0-5 each: " + ", ".join(RUBRIC) + ". "
        "Then PASS/FAIL each check: " + ", ".join(PASS_FAIL) + " (for major_omissions, unsupported_claims, "
        "citation_errors and hallucination, PASS means none found). Grounding and citation_correctness are judged "
        "against the PASSAGES given. Return JSON only: {\"scores\": {...}, \"pass_fail\": {...}, "
        "\"notes\": \"one or two sentences\"}."
    )
    user = (f"EXAM LEVEL: {level}\nQUESTION: {question}\n"
            + (f"EXPECTED COMPONENTS: {json.dumps(expected)}\n" if expected else "")
            + (f"REVIEWER NOTES: {gold_notes}\n" if gold_notes else "")
            + f"\nPASSAGES GIVEN TO THE MODEL:\n{context_text[:14000]}\n\nANSWER:\n{answer}")
    out = _json_call(system, user, max_tokens=500)
    out["label"] = AI_JUDGE_LABEL
    return out
