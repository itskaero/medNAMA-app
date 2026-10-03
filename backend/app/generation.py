"""Answer generation pipeline: retrieves textbook context, asks DeepSeek for a
books-first answer with a clearly labelled AI supplement, and validates
citations/figures server-side.

Answer shape (answer_markdown is kept for every existing consumer; the other
fields let the UI label how much of the answer is textbook-backed):
    textbook_answer_markdown  cited facts from the retrieved passages
    supplementary_markdown    AI clinical knowledge the books don't state (never cited)
    grounding                 'textbook' | 'partial' | 'ai_only' | 'none'
    status                    'ok' | 'llm_error' | 'not_configured'
"""

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.llm import LLMNotConfigured, chat_completion
from app.models import Book
from app.retrieval import retrieval_service

logger = logging.getLogger(__name__)

# Kept for callers that still compare against it; chat no longer returns it.
NOT_COVERED_RESPONSE = {
    "answer_markdown": "I am sorry, but the answer to your question is not covered in the provided textbooks.",
    "citations": [],
    "figures": [],
    "sources": [],
}
SUPPLEMENT_HEADING = "**Beyond the textbooks (AI clinical knowledge, not from your books):**"

LEVEL_GUIDANCE = {
    "undergraduate": "The reader is an MBBS undergraduate: explain clearly, define terms, keep it exam-focused.",
    "fcps1": "The reader is preparing for FCPS-I (basic sciences): emphasise mechanisms, physiology, pathology and pharmacology.",
    "fcps2": "The reader is a resident preparing for FCPS-II: emphasise clinical management, guidelines, doses and pitfalls.",
}
DEFAULT_LEVEL_GUIDANCE = (
    "The reader is a medical student or resident preparing for MBBS/FCPS (CPSP) exams: "
    "be concise, high-yield and exam-oriented."
)

# "Tutor me": the student asked to be taught, not told. Same JSON as an answer, so citations and figures still
# show; the model leads with questions and gives the full answer only at the end.
TUTOR_RULES = (
    "TUTOR MODE (overrides how you write textbook_answer_markdown; the JSON format stays the same):\n"
    "The student wants to work it out, not be told. Teach one step at a time, like a good viva examiner who wants "
    "them to pass.\n"
    "- First message on a topic: in one or two sentences say what you will work through, then ask ONE focused "
    "question the student can answer in a sentence (a mechanism, a key finding, the next step). A short hint from "
    "the passages is fine; do not give the answer.\n"
    "- When the student replies: say plainly whether they are right, correct any error with the textbook fact "
    "(cited [Book, Page]), then ask the next question that builds on it.\n"
    "- After three or four exchanges, or as soon as the student is stuck or asks for the answer, give the full, "
    "concise answer with citations and one line on what they got right and what to revise.\n"
    "- Keep each turn under 120 words. Leave supplementary_markdown and buzzwords_markdown empty except in that "
    "final answer. Cite every textbook fact you state; never attach a page to a question you ask.\n\n"
)

# Legacy refusal / error texts that must not be replayed as conversation history.
_REFUSAL_MARKERS = (
    "i am sorry, but the answer to your question is not covered",
    "the ai service did not respond",
)


def is_refusal_text(text: str) -> bool:
    lower = (text or "").strip().lower()
    return any(lower.startswith(m) for m in _REFUSAL_MARKERS)


def clean_text_references(text: str, stripped_citations: list[dict], stripped_figures: list[dict]) -> str:
    """Scan response text and strip references to deleted citations or figures."""
    cleaned = text

    for fig in stripped_figures:
        fig_id = fig.get("id")
        label = re.escape(str(fig.get("figure_label", f"Figure {fig_id}")))
        patterns = [
            re.compile(rf"\[{label}\]", re.IGNORECASE),
            re.compile(rf"\({label}\)", re.IGNORECASE),
            re.compile(rf"\[Figure ID:\s*{fig_id}\]", re.IGNORECASE),
            re.compile(rf"\bFigure\s*{fig_id}\b", re.IGNORECASE),
        ]
        for pattern in patterns:
            cleaned = pattern.sub("", cleaned)

    for cit in stripped_citations:
        title = re.escape(cit.get("book_title", ""))
        page = cit.get("page_number")
        patterns = [
            re.compile(rf"\[{title},\s*(?:Page|p\.)\s*{page}\]", re.IGNORECASE),
            re.compile(rf"\({title},\s*(?:Page|p\.)\s*{page}\)", re.IGNORECASE),
        ]
        for pattern in patterns:
            cleaned = pattern.sub("", cleaned)

    # Collapse whitespace runs without destroying markdown line breaks.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\[\s*\]", "", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    return cleaned.strip()


_ANY_BOOK_CITATION = re.compile(r"\s?[\[(][^\[\]()]{2,80},\s*(?:Page|p\.)\s*\d+[\])]", re.IGNORECASE)


def strip_all_citations(text: str) -> str:
    """Remove any [Book, Page N] references (used on the uncited AI supplement)."""
    return _ANY_BOOK_CITATION.sub("", text or "").strip()


def validate_generation(
    response_json: dict[str, Any], retrieved_chunks: list, retrieved_figures: list[dict]
) -> dict[str, Any]:
    """Strictly validates returned citations and figures against retrieved context.

    Strips any citations or figures that were not in the retrieved lists.
    """
    validated = {
        "answer_markdown": response_json.get("answer_markdown", ""),
        "citations": [],
        "figures": [],
    }

    valid_citations = set()
    for chunk in retrieved_chunks:
        book_title = chunk.book.title if chunk.book else "Unknown Textbook"
        valid_citations.add((book_title.strip().lower(), chunk.page_number))

    valid_figure_ids = {fig["id"] for fig in retrieved_figures}

    stripped_citations = []
    stripped_figures = []

    raw_citations = response_json.get("citations", [])
    if isinstance(raw_citations, list):
        for cit in raw_citations:
            if not isinstance(cit, dict):
                continue
            title = str(cit.get("book_title", ""))
            page = cit.get("page_number")
            try:
                page = int(page) if page is not None else None
            except (TypeError, ValueError):
                page = None
            cit["page_number"] = page
            if (title.strip().lower(), page) in valid_citations:
                validated["citations"].append(cit)
            else:
                logger.warning(f"Stripping fabricated/invalid citation: {title}, Page {page}")
                stripped_citations.append(cit)

    raw_figures = response_json.get("figures", [])
    if isinstance(raw_figures, list):
        for fig in raw_figures:
            if not isinstance(fig, dict):
                continue
            if fig.get("id") in valid_figure_ids:
                validated["figures"].append(fig)
            else:
                logger.warning(f"Stripping fabricated/invalid figure citation: Figure ID {fig.get('id')}")
                stripped_figures.append(fig)

    if stripped_citations or stripped_figures:
        validated["answer_markdown"] = clean_text_references(
            validated["answer_markdown"], stripped_citations, stripped_figures
        )

    return validated


def _format_context(chunks: list) -> str:
    blocks = []
    for idx, c in enumerate(chunks, 1):
        book_title = c.book.title if c.book else "Unknown Textbook"
        blocks.append(
            f"Chunk {idx}:\n"
            f"  Source Book: {book_title}\n"
            f"  Page: {c.page_number}\n"
            f"  Chapter: {c.chapter or 'N/A'}\n"
            f"  Text Content: {c.content}\n"
        )
    return "\n---\n".join(blocks) if blocks else "NO TEXTBOOK PASSAGES WERE RETRIEVED FOR THIS QUERY."


def _format_figures(figures: list[dict]) -> str:
    """Figures offered to the LLM: only captioned figures chosen by retrieval.select_figures."""
    lines = [
        f"Figure ID: {fig['id']}\n"
        f"  Label: {fig['figure_label']}\n"
        f"  Book: {fig.get('book_title') or 'N/A'}, Page: {fig['page_number']}\n"
        f"  Printed caption: {fig['caption']}\n"
        for fig in figures
    ]
    return "\n---\n".join(lines) if lines else "No relevant figures."


def _attach_figure_details(llm_figures: list[dict], selected: list[dict]) -> list[dict]:
    """Replace the LLM's figure entries with the real caption/book/page from the database."""
    by_id = {f["id"]: f for f in selected}
    out = []
    for fig in llm_figures:
        real = by_id.get(fig.get("id"))
        if real:
            out.append({**real, "reason_to_include": fig.get("reason_to_include")})
    return out


def _strip_unknown_refs(text: str, chunks: list) -> str:
    """Remove [Book, Page N] references that don't match a retrieved passage."""
    valid = {((c.book.title if c.book else "").strip().lower(), c.page_number) for c in chunks}

    def keep(m: re.Match) -> str:
        title, page = m.group(1).strip().lower(), int(m.group(2))
        return m.group(0) if (title, page) in valid else ""

    return re.sub(r"\s?\[([^\[\],]{2,80}),\s*(?:Page|p\.)\s*(\d+)\]", keep, text or "", flags=re.IGNORECASE).strip()


def _also_in(sources: list[dict], citations: list[dict], min_relevance: float = 0.0) -> list[dict]:
    """Books with a strong matching passage that the answer did not cite (one entry per book)."""
    cited_books = {str(c.get("book_title", "")).strip().lower() for c in citations}
    seen, out = set(), []
    for src in sources:
        title = (src.get("book_title") or "").strip()
        key = title.lower()
        if not title or key in cited_books or key in seen or src.get("relevance_score", -99) < min_relevance:
            continue
        seen.add(key)
        out.append({"book_title": title, "page_number": src.get("page_number"), "chunk_id": src.get("chunk_id")})
    return out[:4]


def compose_answer_markdown(textbook_md: str, supplement_md: str) -> str:
    parts = []
    if textbook_md.strip():
        parts.append(textbook_md.strip())
    if supplement_md.strip():
        parts.append(f"{SUPPLEMENT_HEADING}\n\n{supplement_md.strip()}")
    return "\n\n".join(parts)


def _grounding(textbook_md: str, supplement_md: str, citations: list, model_value: str | None) -> str:
    has_book = bool(textbook_md.strip()) and bool(citations)
    has_ai = bool(supplement_md.strip())
    if has_book and has_ai:
        return "partial"
    if has_book:
        return "textbook"
    if has_ai:
        return "ai_only"
    # Uncited text only: conversational replies are 'none', medical ones are AI knowledge.
    return "none" if model_value == "none" else "ai_only"


def generate_answer(
    session: Session,
    query: str,
    confidence_threshold: float = 0.55,  # accepted for API compatibility; no longer gates retrieval
    history: list[dict[str, str]] | None = None,
    book_id: int | None = None,
    chapter: str | None = None,
    level: str | None = None,
    on_stage=None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Retrieve textbook context and generate a books-first answer with a labelled AI supplement.
    mode="tutor": Socratic tutoring on the same passages (TUTOR_RULES).

    on_stage(name) is called as the pipeline advances ("searching", "generating")
    so a streaming endpoint can report progress.
    """
    def stage(name: str) -> None:
        if on_stage:
            try:
                on_stage(name)
            except Exception:
                pass

    stage("searching")
    # Use the previous user question to disambiguate follow-ups ("and in adults?") in the rewrite.
    prev_user = next((t["content"] for t in reversed(history or []) if t.get("role") == "user"), "")
    tutor = (mode or "").lower() == "tutor"
    search_q = query
    if tutor and history:
        # A tutoring reply ("low pH?") says little on its own: search with the topic the session started from.
        first_user = next((t["content"] for t in history if t.get("role") == "user"), "")
        search_q = f"{first_user[:300]} {query}".strip()
    result = retrieval_service.search(session, search_q, limit=6, book_id=book_id, chapter=chapter,
                                      context_hint=prev_user[:300])
    chunks = result.context
    all_figures = result.figures

    books_str = ", ".join(b.title for b in session.query(Book).all()) or "No books currently loaded."
    level_line = LEVEL_GUIDANCE.get((level or "").lower(), DEFAULT_LEVEL_GUIDANCE)

    system_prompt = (
        "You are Dr. MedNama, an expert medical tutor for MBBS and FCPS (CPSP) exam preparation.\n"
        f"{level_line}\n\n"
        + (TUTOR_RULES if tutor else "") +
        "BOOKS AVAILABLE IN SYSTEM:\n"
        f"{books_str}\n\n"
        "HOW TO ANSWER MEDICAL QUESTIONS (books first, then clearly labelled AI knowledge):\n"
        "1. textbook_answer_markdown: answer from the RETRIEVED TEXT CONTEXT. Use everything relevant, "
        "including reasonable clinical inference from what the passages state (for example, if a passage "
        "gives the resuscitation regimen, that IS the fluid of choice; if it explains a mechanism, that answers "
        "the 'why'). Cite every textbook fact inline with the exact book title and page, e.g. "
        "[Bailey Surgery, Page 280]. When passages from MORE THAN ONE BOOK are relevant, integrate them and "
        "cite each book that contributes (e.g. the ENT text for pathology AND the surgery text for management); "
        "say where books differ. Leave this empty only if the passages are unrelated to the question.\n"
        "2. supplementary_markdown: add what an exam candidate needs that the passages do NOT state: current "
        "terms, updated guidelines, exam pearls, classic MCQ traps, or the whole answer if the passages are "
        "unrelated. Use your own reliable medical knowledge. NEVER put book titles or page numbers here. "
        "Keep it short; leave it empty if the textbook part already fully answers the question.\n"
        "3. Never invent book titles, page numbers, figures or quotes. If you are genuinely unsure of a fact, "
        "say so instead of guessing.\n"
        "4. For CONVERSATIONAL or SYSTEM questions (greetings, 'which books do you have'), answer naturally in "
        "textbook_answer_markdown with no citations and set grounding to 'none'. Do not list the books unless asked.\n"
        "5. RETRIEVED DIAGRAMS lists figures with their printed captions. Add a figure to 'figures' only if its "
        "caption shows something your answer discusses; refer to it by its label (e.g. [Figure 49.1]). Never "
        "include a figure whose caption is about something else.\n"
        "6. buzzwords_markdown: for medical questions, 2-5 one-line exam buzzwords / key associations "
        "(e.g. 'Adolescent male + recurrent profuse epistaxis + nasopharyngeal mass -> juvenile angiofibroma'). "
        "If a textbook passage gives a mnemonic, use it and cite it; if you make one up, label it '(AI mnemonic)'. "
        "Empty string for conversational questions.\n\n"
        "Respond in valid JSON only, matching this structure:\n"
        "{\n"
        '  "textbook_answer_markdown": "Cited answer from the passages...",\n'
        '  "supplementary_markdown": "Uncited AI knowledge beyond the passages, or empty string",\n'
        '  "grounding": "textbook | partial | ai_only | none",\n'
        '  "buzzwords_markdown": "- buzzword 1\\n- buzzword 2 (AI mnemonic)",\n'
        '  "citations": [\n'
        '    {"book_title": "exact book title from the context", "page_number": 123, '
        '"excerpt": "exact sentence or key phrase from the context"}\n'
        "  ],\n"
        '  "figures": [\n'
        '    {"id": 1, "figure_label": "Figure X", "reason_to_include": "why it is relevant"}\n'
        "  ]\n"
        "}"
    )

    user_content = (
        f"USER QUESTION: {query}\n\n"
        f"RETRIEVED TEXT CONTEXT:\n{_format_context(chunks)}\n\n"
        f"RETRIEVED DIAGRAMS:\n{_format_figures(all_figures)}"
    )

    stage("generating")
    api_messages = [{"role": "system", "content": system_prompt}]
    for turn in history or []:
        # Replaying earlier refusals/errors teaches the model to refuse again.
        if turn.get("role") == "assistant" and is_refusal_text(turn.get("content", "")):
            continue
        api_messages.append(turn)
    api_messages.append({"role": "user", "content": user_content})

    try:
        raw = chat_completion(api_messages, json_mode=True, temperature=0.0, max_tokens=2500, label="chat")
        response_json = json.loads(raw)
    except LLMNotConfigured as e:
        logger.error(str(e))
        return {
            "answer_markdown": "The AI service is not configured on the server (DEEPSEEK_API_KEY is missing).",
            "citations": [], "figures": [], "sources": result.sources,
            "grounding": "none", "status": "not_configured",
        }
    except Exception as e:
        logger.error(f"Answer generation failed for {query!r}: {type(e).__name__}: {e}")
        return {
            "answer_markdown": (
                "The AI service did not respond in time or returned an error, so no answer was generated. "
                "Please try again. The closest textbook passages are listed under the sources below."
            ),
            "citations": [], "figures": [], "sources": result.sources,
            "grounding": "none", "status": "llm_error",
        }

    textbook_md = str(response_json.get("textbook_answer_markdown") or response_json.get("answer_markdown") or "")
    supplement_md = strip_all_citations(str(response_json.get("supplementary_markdown") or ""))

    validated = validate_generation(
        {"answer_markdown": textbook_md, "citations": response_json.get("citations", []),
         "figures": response_json.get("figures", [])},
        chunks, all_figures,
    )
    textbook_md = validated["answer_markdown"]
    grounding = _grounding(textbook_md, supplement_md, validated["citations"], response_json.get("grounding"))
    # Buzzword citations must be real too; drop any reference not in the context.
    buzzwords_md = _strip_unknown_refs(str(response_json.get("buzzwords_markdown") or "").strip(), chunks)

    logger.info(
        "answer %r: grounding=%s citations=%d top_rerank=%s",
        query, grounding, len(validated["citations"]),
        f"{result.top_score:.2f}" if result.top_score is not None else "n/a",
    )
    return {
        "answer_markdown": compose_answer_markdown(textbook_md, supplement_md),
        "textbook_answer_markdown": textbook_md,
        "supplementary_markdown": supplement_md,
        "grounding": grounding,
        "status": "ok",
        "citations": validated["citations"],
        "figures": _attach_figure_details(validated["figures"], all_figures),
        "sources": result.sources,
        "also_in": _also_in(result.sources, validated["citations"]) if grounding != "none" else [],
        "buzzwords_markdown": buzzwords_md,
    }


def generate_mcq_explanation(session: Session, mcq) -> dict:
    """Generates a grounded explanation for a specific MCQ using RAG and DeepSeek."""
    options = mcq.options or {}
    correct_text = options.get(mcq.correct_option, "")
    query = f"{mcq.question_text} {correct_text}".strip()
    result = retrieval_service.search(session, query, limit=6)
    chunks = result.context
    all_figures = result.figures
    options_str = "\n".join(f"- Option {k}: {v}" for k, v in options.items())

    system_prompt = (
        "You are an expert medical tutor writing an explanation for a multiple-choice question (MCQ) "
        "for MBBS/FCPS exam preparation.\n\n"
        "EXPLANATION RULES:\n"
        "1. Explain why the correct option is correct and why each other option is wrong.\n"
        "2. Prefer facts from the RETRIEVED TEXTBOOK CONTEXT and cite them inline with the exact book title "
        "and page, e.g. [Bailey Surgery, Page 280].\n"
        "3. Where the context does not cover a point, you may use reliable medical knowledge, but mark that "
        "sentence with '(AI knowledge)' and do not attach a book or page to it.\n"
        "4. If a figure from the provided list is directly relevant, refer to it by label and add it to 'figures'.\n\n"
        "Respond in valid JSON only:\n"
        "{\n"
        '  "answer_markdown": "Your structured explanation...",\n'
        '  "citations": [{"book_title": "exact title", "page_number": 123, "excerpt": "key phrase"}],\n'
        '  "figures": [{"id": 1, "figure_label": "Figure X", "reason_to_include": "why"}]\n'
        "}"
    )
    user_content = (
        f"QUESTION: {mcq.question_text}\n"
        f"OPTIONS:\n{options_str}\n"
        f"CORRECT OPTION: {mcq.correct_option}\n\n"
        f"RETRIEVED TEXTBOOK CONTEXT:\n{_format_context(chunks)}\n\n"
        f"RETRIEVED DIAGRAMS:\n{_format_figures(all_figures)}"
    )

    try:
        raw = chat_completion(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
            json_mode=True, temperature=0.0, max_tokens=2000, label="mcq-explain",
        )
        return validate_generation(json.loads(raw), chunks, all_figures)
    except LLMNotConfigured:
        return {
            "answer_markdown": f"Explanation cannot be generated: DEEPSEEK_API_KEY is not configured.\n\nCorrect Option was: **{mcq.correct_option}**",
            "citations": [], "figures": [], "status": "error",
        }
    except Exception as e:
        logger.error(f"Error generating MCQ explanation: {e}")
        return {
            "answer_markdown": f"Failed to generate explanation due to an internal error.\n\nCorrect Option was: **{mcq.correct_option}**",
            "citations": [], "figures": [], "status": "error",
        }
