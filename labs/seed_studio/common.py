"""Shared helpers: database sessions, the audit log, LLM calls that report token usage, and error classes."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from . import settings  # noqa: F401  (backend import path)

from app.database import SessionLocal  # noqa: E402
from app.seed_models import SeedAudit  # noqa: E402

logger = logging.getLogger("seed_studio")


@contextmanager
def session() -> Iterator[Any]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def audit(db, actor: str | None, action: str, entity: str, entity_id: Any, **detail: Any) -> None:
    """Record an operator or system action. The caller commits."""
    db.add(SeedAudit(actor=actor, action=action, entity=entity, entity_id=str(entity_id), detail=detail))


class TransientError(RuntimeError):
    """Worth retrying: timeouts, rate limits, overloaded provider, a reply that was not JSON."""


class PermanentError(RuntimeError):
    """Retrying will not help: no API key, a rejected request, no textbook evidence at all."""


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


def _classify(e: Exception) -> Exception:
    name = type(e).__name__.lower()
    status = getattr(e, "status_code", None)
    if status in (401, 403):
        return PermanentError(f"The AI provider refused the key ({status}).")
    if status == 400:
        return PermanentError(f"The AI provider rejected the request: {e}")
    if status in (408, 409, 429, 500, 502, 503, 504) or "timeout" in name or "connection" in name:
        return TransientError(f"{type(e).__name__}: {e}")
    return TransientError(f"{type(e).__name__}: {e}")


def llm_json(messages: list[dict], *, role: str = "fast", max_tokens: int = 6000, temperature: float = 0.4,
             label: str = "seed") -> tuple[dict, LLMResult]:
    """One JSON-mode call on medNAMA's configured endpoint for `role`, with its token usage.

    Same client, model and thinking switch as app.llm.chat_completion; this variant also returns usage so jobs can
    record cost. Errors come back as TransientError / PermanentError for the retry logic."""
    from app.config import settings as app_settings
    from app.llm import LLMNotConfigured, endpoint, get_client, llm_configured

    if not llm_configured(role):
        raise PermanentError("The AI service is not configured (DEEPSEEK_API_KEY in the root .env).")
    ep = endpoint(role)
    kwargs: dict[str, Any] = {"model": ep.model, "messages": messages, "temperature": temperature,
                              "response_format": {"type": "json_object"}, "max_tokens": max_tokens}
    if ep.is_deepseek and not app_settings.llm_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    start = time.monotonic()
    try:
        resp = get_client(role).chat.completions.create(**kwargs)
    except LLMNotConfigured as e:
        raise PermanentError(str(e))
    except Exception as e:
        raise _classify(e)
    usage = getattr(resp, "usage", None)
    out = LLMResult(text=resp.choices[0].message.content or "", model=ep.model,
                    tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
                    tokens_out=int(getattr(usage, "completion_tokens", 0) or 0))
    logger.info("LLM %s [%s]: %.1fs in=%d out=%d", label, ep.model, time.monotonic() - start, out.tokens_in, out.tokens_out)
    raw = out.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        return json.loads(raw), out
    except json.JSONDecodeError:
        raise TransientError("The AI reply was not valid JSON.")


def cost_usd(tokens_in: int, tokens_out: int) -> float | None:
    if settings.PRICE_IN is None or settings.PRICE_OUT is None:
        return None
    return round(tokens_in / 1e6 * settings.PRICE_IN + tokens_out / 1e6 * settings.PRICE_OUT, 4)
