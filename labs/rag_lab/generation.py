"""Answer generation for the lab.

Two prompts:
  * experimental  (this file): new rules (answer the actual question, prefer answer-bearing evidence,
                  never fabricate, STRICT vs TUTOR boundary). Production's prompt is NOT edited.
  * production    production's own generate_answer() fed this configuration's context (see
                  production_runner.with_context), to separate "better context" from "better prompt".

Citations are validated with production's own validators (validate_generation, _strip_unknown_refs):
a [Book, Page N] that is not one of the passages given is removed and reported.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import settings  # noqa: F401
from .llm_stages import passages_block

from app import exam_levels  # noqa: E402
from app.generation import _strip_unknown_refs, strip_all_citations, validate_generation  # noqa: E402
from app.llm import chat_completion  # noqa: E402

PROMPT_VERSION = "exp-gen-1"

CORE_RULES = """You are Dr. MedNama, a medical tutor for MBBS and FCPS (CPSP) exam preparation.

RULES
1. Answer the question that was actually asked. A passage being on the same topic is not a reason to use it.
2. Prefer passages that directly state the answer. Use several passages when one is not enough for a complete answer.
3. Every textbook-derived medical claim must be supported by a passage and cited inline exactly as labelled,
   e.g. [Guyton & Hall 15e, Page 331]. Never cite a book or page that is not in the PASSAGES.
4. If the passages do not support part of the answer, say so in "insufficient_evidence". Never fill a gap silently.
5. The exam level changes depth, emphasis and structure. It never changes what is factually correct.
6. Do not dump everything retrieved: include what answers the question.
"""

STRICT_RULES = """MODE: STRICT. Use ONLY information supported by the passages. "ai_supplement_markdown" must be an empty string."""

TUTOR_RULES = """MODE: TUTOR. The passages are the primary source. If an exam candidate needs something the passages do not
state, put it in "ai_supplement_markdown" (it is shown under "AI Supplement — Not directly supported by retrieved
textbook passages"). Never put a book title, page or citation in the supplement, and never present supplement
content as if it came from a textbook. Keep it short."""

OUTPUT_FORMAT = """Return JSON only:
{"answer_markdown": "the answer, citing passages inline",
 "insufficient_evidence": "what the passages did not support, or empty string",
 "ai_supplement_markdown": "TUTOR mode only, else empty string",
 "citations": [{"book_title": "exact title", "page_number": 123, "excerpt": "key phrase from that passage"}]}"""

SUPPLEMENT_HEADING = "AI Supplement — Not directly supported by retrieved textbook passages"


def prompt_hash() -> str:
    body = CORE_RULES + STRICT_RULES + TUTOR_RULES + OUTPUT_FORMAT + PROMPT_VERSION
    return hashlib.sha1(body.encode()).hexdigest()[:12]


def generate(question: str, level: str, strictness: str, context: list[Any], figures: list[dict],
             understanding: dict | None = None, cov: dict | None = None, plan: dict | None = None) -> dict[str, Any]:
    programme = exam_levels.normalise(level)
    system = "\n".join([
        CORE_RULES,
        f"EXAM LEVEL: {level}. {exam_levels.chat_guidance(programme)}",
        STRICT_RULES if strictness == "strict" else TUTOR_RULES,
        OUTPUT_FORMAT,
    ])
    parts = [f"QUESTION: {question}"]
    if understanding:
        parts.append(f"QUESTION ANALYSIS: {json.dumps({k: understanding.get(k) for k in ('intent', 'topic', 'answer_type', 'required_components')})}")
    parts.append(f"PASSAGES:\n{passages_block(context)}")
    if figures:
        parts.append("FIGURES ON THESE PAGES (mention one only if its caption shows what you discuss):\n" + "\n".join(
            f"- {f.get('figure_label')}: {f.get('caption')} [{f.get('book_title')}, Page {f.get('page_number')}]"
            for f in figures))
    if cov:
        parts.append(f"EVIDENCE COVERAGE: {json.dumps({k: cov.get(k) for k in ('covered', 'missing', 'coverage_score')})}")
    if plan:
        parts.append(f"ANSWER PLAN (follow it): {json.dumps(plan)}")
    raw = chat_completion([{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}],
                          json_mode=True, temperature=0.0, max_tokens=2500, label="rag-lab-answer", role="chat")
    try:
        out = json.loads(raw)
    except ValueError:
        out = {"answer_markdown": raw, "citations": []}
    answer = str(out.get("answer_markdown") or "")
    supplement = strip_all_citations(str(out.get("ai_supplement_markdown") or "")) if strictness == "tutor" else ""
    validated = validate_generation({"answer_markdown": answer, "citations": out.get("citations") or [], "figures": []},
                                    context, [])
    cleaned = _strip_unknown_refs(validated["answer_markdown"], context)
    removed_inline = _count_refs(answer) - _count_refs(cleaned)
    composed = cleaned + (f"\n\n**{SUPPLEMENT_HEADING}**\n\n{supplement}" if supplement.strip() else "")
    return {
        "answer_markdown": composed,
        "textbook_answer_markdown": cleaned,
        "ai_supplement_markdown": supplement,
        "insufficient_evidence": str(out.get("insufficient_evidence") or ""),
        "citations": validated["citations"],
        "citation_check": {
            "citations_given": len(out.get("citations") or []),
            "citations_kept": len(validated["citations"]),
            "inline_refs_removed": removed_inline,
            "supplement_had_citations": supplement != str(out.get("ai_supplement_markdown") or "") if strictness == "tutor" else False,
        },
        "prompt": {"name": "experimental", "version": PROMPT_VERSION, "hash": prompt_hash(), "strictness": strictness},
    }


def _count_refs(text: str) -> int:
    import re
    return len(re.findall(r"\[([^\[\],]{2,80}),\s*(?:Page|p\.)\s*(\d+)\]", text or "", flags=re.I))
