"""OpenAI wrappers: generation (gpt-4o-mini), judging (gpt-4o), embeddings,
and the optional web-search current-events probe.

All network use is behind this module so the rest of the app is testable with
fakes. Rough token accounting is kept so precompute runs can report cost.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from .uniqueness import hash_embedding

log = logging.getLogger("social_agent.llm")

# $ / 1M tokens (2026-07 public prices) — for run reporting only.
_PRICES = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "text-embedding-3-small": (0.02, 0.0),
}


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_model: dict = field(default_factory=dict)

    def add(self, model: str, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        p, c = self.by_model.get(model, (0, 0))
        self.by_model[model] = (p + prompt, c + completion)

    @property
    def approx_cost_usd(self) -> float:
        total = 0.0
        for model, (p, c) in self.by_model.items():
            for known, (pin, pout) in _PRICES.items():
                if model.startswith(known):
                    total += p / 1e6 * pin + c / 1e6 * pout
                    break
        return total


class LLMClient:
    """Thin async wrapper; every call is JSON-in/JSON-out text."""

    def __init__(self, api_key: str, embed_dim: int = 384):
        self._client = AsyncOpenAI(api_key=api_key)
        self.embed_dim = embed_dim
        self.usage = LLMUsage()

    async def chat_json(self, model: str, system: str, user: str, temperature: float = 1.0) -> str:
        resp = await self._client.chat.completions.create(
            model=model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if resp.usage:
            self.usage.add(model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
        return resp.choices[0].message.content or ""

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Embed texts; on API failure fall back to deterministic hashing so
        the uniqueness gate still functions (weaker, but never absent)."""
        if not texts:
            return []
        try:
            resp = await self._client.embeddings.create(
                model=model, input=texts, dimensions=self.embed_dim
            )
            if resp.usage:
                self.usage.add(model, resp.usage.prompt_tokens, 0)
            ordered = sorted(resp.data, key=lambda d: d.index)
            return [d.embedding for d in ordered]
        except Exception:  # noqa: BLE001 — degrade, don't die
            log.exception("embedding call failed; falling back to hash embeddings")
            return [hash_embedding(t, self.embed_dim) for t in texts]

    async def web_search_trends(self, model: str = "gpt-4o") -> str:
        """Trend probe for the dual-direction reply pipeline.

        Asks the web-search tool for a FEW lighthearted things trending today
        (vs. web_search_events' single event for post flavoring). Returns the
        model's text; callers parse defensively and treat failures/empty as
        'no trends this cycle'.
        """
        resp = await self._client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=(
                "List up to 3 big, lighthearted things trending TODAY that ordinary "
                "people are posting about on social media (sports tournaments, awards "
                "shows, game/movie releases, holidays, viral pop-culture moments). "
                "STRICTLY avoid politics, elections, war, disasters, deaths, crime, "
                "and anything sensitive. One line each: NAME — one-sentence summary. "
                "If nothing suitable, say NONE."
            ),
        )
        try:
            u = resp.usage
            if u:
                self.usage.add(model, getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0))
        except Exception:  # noqa: BLE001
            pass
        return getattr(resp, "output_text", "") or ""

    async def web_search_events(self, model: str = "gpt-4o") -> str:
        """Optional current-events probe via OpenAI's web-search tool.

        Returns the model's text answer (may be empty). Callers treat any
        failure as 'no event available' — this feature is strictly optional.
        """
        resp = await self._client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=(
                "In one short paragraph: what is ONE big, lighthearted, globally "
                "recognizable event happening right now or within the next few days "
                "(sports tournament, awards show, holiday, pop-culture moment)? "
                "Avoid politics, disasters, deaths, and anything sensitive. "
                "Name the event and one fun angle. If nothing suitable, say NONE."
            ),
        )
        try:
            u = resp.usage
            if u:
                self.usage.add(model, getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0))
        except Exception:  # noqa: BLE001
            pass
        return getattr(resp, "output_text", "") or ""
