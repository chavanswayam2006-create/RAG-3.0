"""Generation LLM providers: openrouter + anthropic (real) and fake (offline template)."""
from __future__ import annotations

import logging
import os

log = logging.getLogger("studybuddy.llm")


class LLM:
    name = "abstract"

    def generate(self, system: str, user: str, *, sources: list, subject_label: str) -> str:
        raise NotImplementedError


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, model: str, *, max_tokens: int = 1200, temperature: float = 0.2, api_key: str | None = None) -> None:
        from anthropic import Anthropic  # deferred import

        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))
        if not os.getenv("ANTHROPIC_API_KEY") and not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY missing (set it in .env)")

    def generate(self, system: str, user: str, *, sources: list, subject_label: str) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


class OpenRouterLLM(LLM):
    """Real generation via OpenRouter's OpenAI-compatible gateway.

    One key unlocks hundreds of models (Anthropic, OpenAI, Google, …) through
    a single endpoint — no per-provider keys needed. Does not require the
    `anthropic` package; it reuses the `openai` SDK pointed at OpenRouter.

    Supports an optional fallback_model: if the primary model errors with a
    transient failure (502/503/529 overloaded, timeout, etc.), the request is
    retried once with the fallback model.
    """

    name = "openrouter"
    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        model: str = "nvidia/nemotron-3-super-120b-a12b:free",
        *,
        fallback_model: str | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.2,
        api_key: str | None = None,
    ) -> None:
        from openai import OpenAI  # deferred import keeps startup cheap

        api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY missing (set it in .env)")
        self.model = model
        self.fallback_model = fallback_model
        self.max_tokens = max_tokens
        self.temperature = temperature
        # `default_headers` is forwarded on every request — lets the app show
        # up on the OpenRouter leaderboards and satisfies their ToS.
        self._client = OpenAI(
            base_url=self.BASE_URL,
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "http://localhost:8000",
                "X-OpenRouter-Title": "StudyBuddy",
            },
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """Return True if the error is a transient/overloaded failure worth retrying."""
        msg = str(exc)
        for code in ("429", "502", "503", "529", "timeout", "overloaded", "rate"):
            if code in msg.lower():
                return True
        return False

    def _call(self, model: str, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    def generate(self, system: str, user: str, *, sources: list, subject_label: str) -> str:
        try:
            return self._call(self.model, system, user)
        except Exception as exc:  # noqa: BLE001
            if "402" in str(exc):
                raise RuntimeError(
                    f"OpenRouter 402: model {self.model!r} needs credits. Either pick a ':free' "
                    "model in config.yaml (e.g. google/gemma-4-31b-it:free) or add credits at "
                    "https://openrouter.ai/settings/credits."
                ) from exc
            # If there's a fallback model and the error is transient, retry once.
            if self.fallback_model and self._is_transient(exc):
                log.warning(
                    "Primary model %s failed (%s), retrying with fallback %s",
                    self.model, exc, self.fallback_model,
                )
                return self._call(self.fallback_model, system, user)
            raise


class FakeLLM(LLM):
    """Template responder for offline development/demo (no API key required).

    Produces a grounded-looking answer from the top retrieved chunk and the
    correct "not in syllabus" reply when nothing was retrieved. The reply is
    visibly labelled as a demo so nobody mistakes it for a real Claude answer.
    """

    name = "fake"

    def __init__(self, **_: object) -> None:
        pass

    @staticmethod
    def _lead(chunk_text: str, limit: int = 320) -> str:
        chunk_text = chunk_text.strip()
        if len(chunk_text) <= limit:
            return chunk_text
        cut = chunk_text[:limit]
        return cut.rsplit(" ", 1)[0] + " …"

    def generate(self, system: str, user: str, *, sources: list, subject_label: str) -> str:
        demo_note = "\n\n*(Demo answer — set `llm.provider: anthropic` + ANTHROPIC_API_KEY in .env for real Claude answers.)*"
        if not sources:
            return (
                f"This isn't covered in the syllabus for {subject_label}. "
                "I don't have a passage that matches, so I'd rather not guess. "
                "Ask me something from a unit in the syllabus, or switch subjects. *(Demo — set ANTHROPIC_API_KEY for real answers.)*"
            )
        top = sources[0]
        unit = top.metadata.get("unit", "General")
        page = top.metadata.get("page", "")
        src = f"{top.metadata.get('file','')}" + (f", page {page}" if page else "")
        answer = (
            f"Based on **{unit}** in the {subject_label} syllabus ({src}), here's what the syllabus says on this:\n\n"
            f"{self._lead(top.text)}\n\n"
            "That's the passage the syllabus uses for this topic — review it and try the related "
            "exercises before moving on."
        )
        return answer + demo_note


def create_llm(settings) -> LLM:
    cfg = settings.llm
    provider = (cfg.get("provider") or "fake").lower()
    if provider == "openrouter":
        if not os.getenv("OPENROUTER_API_KEY"):
            log.warning("OPENROUTER_API_KEY not set — falling back to the demo 'fake' responder.")
            return FakeLLM()
        return OpenRouterLLM(
            model=cfg.get("model", "nvidia/nemotron-3-super-120b-a12b:free"),
            fallback_model=cfg.get("fallback_model") or None,
            max_tokens=int(cfg.get("max_tokens", 1200)),
            temperature=float(cfg.get("temperature", 0.2)),
        )
    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            log.warning("ANTHROPIC_API_KEY not set — falling back to the demo 'fake' responder.")
            return FakeLLM()
        return AnthropicLLM(
            model=cfg.get("model", "claude-sonnet-4-20250514"),
            max_tokens=int(cfg.get("max_tokens", 1200)),
            temperature=float(cfg.get("temperature", 0.2)),
        )
    if provider == "fake":
        return FakeLLM()
    raise ValueError(f"Unknown llm.provider: {provider!r} (openrouter | anthropic | fake)")