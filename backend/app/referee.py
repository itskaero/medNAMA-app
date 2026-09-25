"""Answer-Key Referee: check a recall/past-paper answer against the textbooks.

Given a question with its published answer (a recall line such as
"Superficial cardiac plexus is made by = Left vagus") or a full MCQ with its
key, the referee retrieves textbook passages and returns a verdict:

  supported        the textbooks state the published answer
  contradicted     the textbooks state something different
  books_conflict   the textbooks disagree with each other
  textbooks_silent the books don't settle it (AI reasoning is labelled as such)

Every quote shown is checked verbatim against the retrieved passage; a verdict
other than textbooks_silent must rest on at least one verified quote, or it is
downgraded. The recall source itself is never searched (it is not ingested),
so a book can never be cited to prove its own key.
"""

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.llm import chat_completion, llm_configured
from app.retrieval import retrieval_service

logger = logging.getLogger(__name__)

VERDICTS = ("supported", "contradicted", "books_conflict", "textbooks_silent")


def _norm(s: str) -> str:
    return " ".join((s or "").split()).lower()


def judge(db: Session, question: str, answer: str | None = None, options: dict[str, str] | None = None,
          key: str | None = None) -> dict[str, Any]:
    """Referee one question. Pass `answer` for a recall line, or `options` (+ optional `key`) for an MCQ."""
    question = (question or "").strip()
    if not question:
        raise ValueError("question is required")
    options = {str(k).strip().upper(): str(v).strip() for k, v in (options or {}).items() if str(v).strip()}
    key = (key or "").strip().upper()[:1] or None
    published = answer or (options.get(key) if key else None)

    search_text = f"{question} {published or ' '.join(options.values())}"
    result = retrieval_service.search(db, search_text[:600], limit=5)
    blocks = result.context
    context = "\n\n".join(
        f"Chunk {i}: [{b.book.title if b.book else 'Textbook'}, Page {b.page_number}]\n{b.content}"
        for i, b in enumerate(blocks, 1)
    ) or "NO TEXTBOOK PASSAGES FOUND."

    if options:
        task = (
            "MCQ OPTIONS:\n" + "\n".join(f"{k}. {v}" for k, v in sorted(options.items()))
            + (f"\nPUBLISHED KEY: {key}" if key else "\nPUBLISHED KEY: (none given)")
        )
    else:
        task = f"PUBLISHED ANSWER: {answer}"

    if not llm_configured("chat"):
        return {"verdict": "textbooks_silent", "textbook_answer": None, "supported_option": None,
                "agrees_with_key": None, "evidence": [], "explanation": "The AI service is not configured.",
                "figures": [], "sources": result.sources}

    try:
        raw = chat_completion(
            [
                {"role": "system", "content": (
                    "You are an exam answer-key referee for FCPS candidates. Decide what the TEXTBOOK PASSAGES say the "
                    "answer is, and whether the published answer/key agrees. Be strict and honest:\n"
                    "- 'supported': a passage states the published answer (or clearly implies it).\n"
                    "- 'contradicted': a passage states a different answer.\n"
                    "- 'books_conflict': passages from different books disagree.\n"
                    "- 'textbooks_silent': the passages don't settle it. You may then give your own reasoning in "
                    "'ai_reasoning', clearly marked as not from the books.\n"
                    "Quotes must be copied EXACTLY, character for character, from a chunk. Never invent quotes.\n"
                    "Return JSON: {\"verdict\": \"...\", \"textbook_answer\": \"short answer the books support, or null\", "
                    "\"supported_option\": \"letter for MCQs, else null\", "
                    "\"quotes\": [{\"chunk\": 1, \"quote\": \"exact sentence\"}], "
                    "\"explanation\": \"2-5 sentences; for MCQs say briefly why each other option is wrong\", "
                    "\"ai_reasoning\": \"only if textbooks_silent, else empty\"}"
                )},
                {"role": "user", "content": f"QUESTION: {question}\n{task}\n\nTEXTBOOK PASSAGES:\n{context[:9000]}"},
            ],
            json_mode=True, temperature=0.0, max_tokens=1200, label="referee", role="chat",
        )
        out = json.loads(raw)
    except Exception as e:
        logger.warning("Referee failed for %r: %s", question[:80], e)
        return {"verdict": None, "error": f"{type(e).__name__}: {e}", "evidence": [], "figures": [],
                "sources": result.sources}

    evidence = []
    for q in out.get("quotes") or []:
        try:
            idx = int(q.get("chunk"))
        except (TypeError, ValueError):
            continue
        quote = str(q.get("quote") or "").strip()
        if not (1 <= idx <= len(blocks)) or len(quote) < 12:
            continue
        block = blocks[idx - 1]
        if _norm(quote) not in _norm(block.content):
            logger.info("Referee dropped an unverifiable quote for %r", question[:60])
            continue
        evidence.append({"chunk_id": block.id, "book_title": block.book.title if block.book else None,
                         "page_number": block.page_number, "quote": quote})

    verdict = out.get("verdict") if out.get("verdict") in VERDICTS else "textbooks_silent"
    if verdict != "textbooks_silent" and not evidence:
        verdict = "textbooks_silent"   # no verified quote -> the books haven't been shown to settle it
    if verdict == "books_conflict" and len({e["book_title"] for e in evidence}) < 2:
        verdict = "contradicted" if evidence else "textbooks_silent"

    supported_option = str(out.get("supported_option") or "").strip().upper()[:1] or None
    if supported_option and options and supported_option not in options:
        supported_option = None
    agrees = None
    if options and key and supported_option and verdict in ("supported", "contradicted"):
        agrees = supported_option == key
    elif not options and verdict in ("supported", "contradicted"):
        agrees = verdict == "supported"

    pages = {(e["book_title"], e["page_number"]) for e in evidence}
    figures = [f for f in result.figures if (f.get("book_title"), f.get("page_number")) in pages]
    explanation = str(out.get("explanation") or "").strip()
    ai_reasoning = str(out.get("ai_reasoning") or "").strip() if verdict == "textbooks_silent" else ""
    return {
        "verdict": verdict,
        "textbook_answer": (str(out.get("textbook_answer")).strip() if out.get("textbook_answer") else None),
        "supported_option": supported_option,
        "published": published,
        "agrees_with_key": agrees,
        "evidence": evidence,
        "explanation": explanation,
        "ai_reasoning": ai_reasoning,
        "figures": figures,
        "sources": result.sources,
    }
