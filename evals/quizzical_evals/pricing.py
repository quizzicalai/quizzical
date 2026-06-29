"""Token-cost accounting for eval runs.

The production codebase has **no hardcoded price table** -- cost is delegated to
LiteLLM (``litellm.cost_per_token`` / ``litellm.completion_cost``), and the
2026-06-28 launch audit flagged that "the eval harness has zero token/cost
accounting so the team literally cannot measure or optimize spend." This module
is the fix: it turns a recorded ``Usage`` (prompt/completion tokens) into a USD
cost using LiteLLM's price map, with a small, **version-pinned local override
table** so eval results are reproducible even if LiteLLM's bundled prices shift
under us.

Why a local override table at all?
  * Reproducibility: a report dated 2026-06-29 should compute the same dollars
    next quarter regardless of a LiteLLM upgrade. We snapshot the prices we used.
  * Air-gapped/dry-run: ``estimate_cost`` must work with no network and no
    provider keys so CI can validate the math.

Precedence: ``PRICE_OVERRIDES`` (this file) -> ``litellm.cost_per_token`` -> 0.0
with a logged warning (never raise; a missing price must not abort a sweep).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version-pinned price snapshot (USD per 1,000,000 tokens).
#
# Captured 2026-06-29 from ``litellm.cost_per_token`` in the repo venv
# (litellm pinned >=1.84.0,<2.0.0). Keep this table in sync with the models the
# agent actually uses (see evals/config/*.yaml) plus the judge models.
#
# These are *list* prices; they intentionally ignore prompt-caching discounts
# and batch-API discounts. Caching is a separate optimization lever that the
# methodology treats as its own config variant, not a silent price reduction.
# ---------------------------------------------------------------------------
PRICE_OVERRIDES: dict[str, tuple[float, float]] = {
    # model id                         (input $/Mtok, output $/Mtok)
    "gpt-4o-mini": (0.150, 0.600),
    "gpt-5-mini": (0.250, 2.000),
    "gpt-4o-2024-11-20": (2.500, 10.000),
    "gemini/gemini-flash-latest": (0.300, 2.500),
    "gemini/gemini-2.5-flash": (0.300, 2.500),
    "gemini/gemini-2.5-flash-lite": (0.100, 0.400),
    "gemini/gemini-2.5-pro": (1.250, 10.000),  # standard <=200k input tier ($2.50 only >200k)
}

_PRICE_SNAPSHOT_DATE = "2026-06-29"
_MILLION = 1_000_000.0


@dataclass(frozen=True)
class Usage:
    """Token usage for one LLM call.

    ``reasoning_tokens`` is tracked separately because reasoning ("thinking")
    models (e.g. Gemini Flash via the Responses API) bill hidden chain-of-thought
    tokens as *output* even though the user never sees them. Providers fold
    reasoning into ``completion_tokens`` for billing, so we keep the breakdown
    for diagnostics but bill against ``completion_tokens``.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


def price_per_million(model: str) -> tuple[float, float] | None:
    """Return ``(input_$_per_Mtok, output_$_per_Mtok)`` for ``model`` or None.

    Tries the pinned override table first (reproducible), then LiteLLM.
    """
    if model in PRICE_OVERRIDES:
        return PRICE_OVERRIDES[model]
    try:
        import litellm  # local import: keep import-time cheap and optional

        in_cost = litellm.cost_per_token(
            model=model, prompt_tokens=_MILLION, completion_tokens=0
        )[0]
        out_cost = litellm.cost_per_token(
            model=model, prompt_tokens=0, completion_tokens=_MILLION
        )[1]
        return (in_cost, out_cost)
    except Exception as exc:  # pragma: no cover - depends on litellm internals
        logger.warning("pricing.unknown_model model=%s err=%s", model, exc)
        return None


def estimate_cost(model: str, usage: Usage) -> float:
    """USD cost for ``usage`` on ``model``. Returns 0.0 (logged) if unpriced."""
    price = price_per_million(model)
    if price is None:
        return 0.0
    in_per_m, out_per_m = price
    return (usage.prompt_tokens / _MILLION) * in_per_m + (
        usage.completion_tokens / _MILLION
    ) * out_per_m


def usage_from_litellm_response(resp: object) -> Usage:
    """Best-effort extraction of token usage from a LiteLLM response object.

    Handles both the Responses API (``usage.input_tokens`` /
    ``usage.output_tokens`` with ``output_tokens_details.reasoning_tokens``) and
    Chat Completions (``usage.prompt_tokens`` / ``usage.completion_tokens``).
    """
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")
    if usage is None:
        return Usage()

    def g(obj: object, *names: str, default: int = 0) -> int:
        for n in names:
            v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
            if isinstance(v, (int, float)):
                return int(v)
        return default

    prompt = g(usage, "prompt_tokens", "input_tokens")
    completion = g(usage, "completion_tokens", "output_tokens")
    details = (
        usage.get("output_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "output_tokens_details", None)
    )
    reasoning = g(details, "reasoning_tokens") if details is not None else 0
    return Usage(
        prompt_tokens=prompt, completion_tokens=completion, reasoning_tokens=reasoning
    )


def snapshot_date() -> str:
    return _PRICE_SNAPSHOT_DATE
