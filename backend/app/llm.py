"""Shared LLM clients and call helpers.

Two roles, each an OpenAI-compatible endpoint:
  "chat"  answers and MCQ explanations (quality matters most)
  "fast"  query rewrite and MCQ batch generation (volume, latency)
Both default to the DEEPSEEK_* settings; LLM_CHAT_* / LLM_FAST_* override them,
so a different model (e.g. MedGemma behind vLLM) can be trialled for one role
without touching the code. Every client has a bounded timeout and retry count.
"""

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# Heavy background AI work (a hardening seed, a twist job) takes one slot while it runs: textbook retrieval,
# the reranker and the embedding model are CPU-bound, so more at once only slows every job (and, before the pool
# fix, starved the rest of the app of DB connections). Size with AI_JOB_WORKERS (default 2; the PC can take 4).
AI_JOB_SLOTS = threading.BoundedSemaphore(max(1, settings.ai_job_workers))


class LLMNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class Endpoint:
    model: str
    base_url: str
    api_key: str

    @property
    def is_deepseek(self) -> bool:
        return "deepseek" in self.base_url.lower() or self.model.lower().startswith("deepseek")


def endpoint(role: str = "chat") -> Endpoint:
    prefix = "llm_fast" if role == "fast" else "llm_chat"
    model = getattr(settings, f"{prefix}_model") or settings.deepseek_model
    base_url = getattr(settings, f"{prefix}_base_url") or settings.deepseek_base_url
    api_key = getattr(settings, f"{prefix}_api_key") or settings.deepseek_api_key
    return Endpoint(model=model, base_url=base_url, api_key=(api_key or "").strip())


def llm_configured(role: str = "chat") -> bool:
    key = endpoint(role).api_key
    return bool(key) and key != "sk-dummy"


_clients: dict[tuple[str, str], OpenAI] = {}


def get_client(role: str = "chat") -> OpenAI:
    ep = endpoint(role)
    if not llm_configured(role):
        raise LLMNotConfigured(f"No API key configured for the '{role}' LLM (DEEPSEEK_API_KEY or LLM_{role.upper()}_API_KEY).")
    key = (ep.base_url, ep.api_key)
    if key not in _clients:
        _clients[key] = OpenAI(
            api_key=ep.api_key,
            base_url=ep.base_url,
            timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
        )
    return _clients[key]


def chat_completion(messages: list[dict], *, json_mode: bool = False, temperature: float = 0.0,
                    max_tokens: int | None = None, thinking: bool | None = None, label: str = "llm",
                    role: str = "chat") -> str:
    """Run one chat completion on the role's endpoint and return the message text.

    `thinking=None` uses settings.llm_thinking. The thinking switch is a
    DeepSeek extension and is only sent to DeepSeek endpoints.
    """
    ep = endpoint(role)
    kwargs: dict[str, Any] = {"model": ep.model, "messages": messages, "temperature": temperature}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    use_thinking = settings.llm_thinking if thinking is None else thinking
    if ep.is_deepseek and not use_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    start = time.monotonic()
    response = get_client(role).chat.completions.create(**kwargs)
    elapsed = time.monotonic() - start
    usage = getattr(response, "usage", None)
    logger.info(
        "LLM %s call [%s/%s]: %.1fs, completion_tokens=%s",
        label, role, ep.model, elapsed, getattr(usage, "completion_tokens", "?"),
    )
    return response.choices[0].message.content or ""


def key_instruction(n: int = 1, keys: str = "ABCDE") -> str:
    """Tell the model where to put the correct answers. Left alone (and shown an example key of "A") it put
    54% of keys at A, which teaches "pick A"; a random position per question keeps keys balanced."""
    plan = [random.choice(keys) for _ in range(max(1, n))]
    if n <= 1:
        return f"Put the correct answer at option {plan[0]}."
    return "Put the correct answers at these options, question by question: " + ", ".join(plan) + "."
