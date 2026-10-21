"""Read a photographed MCQ (a phone photo, a WhatsApp screenshot of a recall) into question, options and key.

Only transcription happens here: the vision model copies what is printed and never answers the question, so the
Referee (app/referee.py) still decides against the textbooks. A key counts only if the image itself marks one
(ticked, circled, bold, "Ans: C"). Uses Gemini through its OpenAI-compatible endpoint (settings.vision_*).
"""

import base64
import json
import logging
import re
import time

from openai import APIStatusError, OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

MAX_BYTES = 8 * 1024 * 1024
# Tried in order after settings.vision_model when a model is overloaded (503), rate-limited (429) or retired (404).
FALLBACK_MODELS = ("gemini-flash-lite-latest", "gemini-3.5-flash")
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
_client: OpenAI | None = None
ANSWER_MARK = re.compile(r"\s*(?:\((?:ans|answer|correct|key)\.?\)|[✓✔✅]|\*+)\s*$", re.I)

PROMPT = (
    "This image shows one or more multiple-choice questions from a medical exam (a photo of a page, a screenshot "
    "or a recall message). Transcribe the FIRST complete question exactly as printed. Do not answer it, do not "
    "correct it, do not add options that are not visible.\n"
    "Return JSON only: {\"question\": \"the stem\", \"options\": {\"A\": \"...\", \"B\": \"...\"}, "
    "\"key\": \"the letter the image itself marks as the answer (ticked, circled, highlighted, 'Ans: C'), else null\", "
    "\"answer\": \"if there are no options but a recall answer is written after the question, that answer, else null\", "
    "\"readable\": true}\n"
    "Number options A, B, C... in the order shown even if the image uses 1-5 or a-e. If you cannot read a question, "
    "return {\"readable\": false}."
)


class PhotoUnavailable(RuntimeError):
    pass


def configured() -> bool:
    return bool((settings.gemini_api_key or "").strip())


def _get_client() -> OpenAI:
    global _client
    if not configured():
        raise PhotoUnavailable("Photo reading is not set up on this server (GEMINI_API_KEY).")
    if _client is None:
        _client = OpenAI(api_key=settings.gemini_api_key.strip(), base_url=settings.vision_base_url,
                         timeout=60, max_retries=1)
    return _client


def read_mcq(image: bytes, mime: str) -> dict:
    """{question, options, key, answer} from an image; raises ValueError when nothing readable is found."""
    if mime not in IMAGE_TYPES:
        raise ValueError("Send a photo (JPEG, PNG, WebP or HEIC).")
    if len(image) > MAX_BYTES:
        raise ValueError("That photo is over 8 MB. Crop it to the question and try again.")
    data_url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    messages = [{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                             {"type": "image_url", "image_url": {"url": data_url}}]}]
    models = list(dict.fromkeys([settings.vision_model, *FALLBACK_MODELS]))
    resp = None
    for i, model in enumerate(models):
        start = time.monotonic()
        try:
            resp = _get_client().chat.completions.create(model=model, temperature=0, messages=messages,
                                                         response_format={"type": "json_object"})
            logger.info("Vision read-mcq [%s]: %.1fs", model, time.monotonic() - start)
            break
        except APIStatusError as e:
            if e.status_code not in (404, 429, 500, 503) or i == len(models) - 1:
                raise
            logger.warning("Vision model %s unavailable (%s); trying %s", model, e.status_code, models[i + 1])
    raw = (resp.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Vision read-mcq returned non-JSON: %r", raw[:200])
        out = {"readable": False}
    if not isinstance(out, dict) or not out.get("readable", True) or not str(out.get("question") or "").strip():
        raise ValueError("No question could be read from that photo. Try a sharper, closer shot of one question.")
    options = {str(k).strip().upper()[:1]: str(v).strip() for k, v in (out.get("options") or {}).items()
               if str(k).strip() and str(v).strip()}
    options = {k: v for k, v in options.items() if k in "ABCDEFG"}
    # The image's own answer mark ("Appendix (Ans)", "✓") is the key, not part of the option text.
    for k, v in list(options.items()):
        cleaned = ANSWER_MARK.sub("", v).strip()
        if cleaned != v and not out.get("key"):
            out["key"] = k
        options[k] = cleaned or v
    key = str(out.get("key") or "").strip().upper()[:1] or None
    if key not in options:
        key = None
    answer = str(out.get("answer") or "").strip() or None
    return {"question": str(out["question"]).strip(), "options": options or None, "key": key,
            "answer": None if options else answer}
