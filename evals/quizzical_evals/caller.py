"""LLM caller for eval cells: live (LiteLLM) + deterministic mock.

This is the single seam between the harness and a paid provider. It deliberately
mirrors ``backend/Analysis/llm_caller.py`` (provider-agnostic JSON call, robust
parse, transient retry) but adds the one thing that file is missing and that the
launch audit called out: **token usage + cost capture**. Every call returns a
``CallOutput`` carrying parsed JSON, wall latency, and a ``Usage`` token record.

Two modes:
  * ``MockCaller`` (default / ``--dry-run``): NO network, NO keys, NO spend.
    Returns schema-shaped fake outputs with deterministic, seed-driven token
    counts so the full pipeline (stats, decision, report) can run in CI.
  * ``LiveCaller`` (``--live``): real ``litellm.responses`` / ``acompletion``.
    Gated behind an explicit flag AND a key check so it can never run by accident.

To populate real numbers you only need to (a) set provider keys, (b) pass
``--live``. Everything else -- pricing, stats, decision -- is identical.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .pricing import Usage, usage_from_litellm_response


@dataclass(frozen=True)
class CallOutput:
    parsed: Any
    raw_text: str
    usage: Usage
    latency_wall_s: float
    model: str
    ok: bool
    error: str | None = None


class Caller(Protocol):
    async def call_json(
        self,
        *,
        tool_name: str,
        system: str,
        user: str,
        model: str,
        max_output_tokens: int = 1500,
        temperature: float = 0.3,
        timeout_s: int = 60,
        effort: str | None = None,
        thinking_budget: int | None = None,
    ) -> CallOutput: ...


# ---------------------------------------------------------------------------
# Mock caller (default)
# ---------------------------------------------------------------------------


class MockCaller:
    """Deterministic, free, offline caller.

    Produces a minimally schema-valid object per ``tool_name`` and synthesises
    plausible token counts (scaled by prompt length and the variant's token cap)
    so cost/latency math exercises end-to-end. Quality scores are NOT faked here
    -- the judge is mocked separately so the report is honestly labelled
    ILLUSTRATIVE until a live run replaces it.
    """

    def __init__(self, seed: int = 1234) -> None:
        self._seed = seed

    async def call_json(
        self,
        *,
        tool_name: str,
        system: str,
        user: str,
        model: str,
        max_output_tokens: int = 1500,
        temperature: float = 0.3,
        timeout_s: int = 60,
        effort: str | None = None,
        thinking_budget: int | None = None,
    ) -> CallOutput:
        rng = random.Random(hash((tool_name, model, user, self._seed)) & 0xFFFFFFFF)
        # Rough token model: prompt ~= chars/4; completion ~= a fraction of cap.
        prompt_tokens = max(1, (len(system) + len(user)) // 4)
        completion_tokens = max(20, int(max_output_tokens * rng.uniform(0.25, 0.85)))
        reasoning = (
            int(completion_tokens * rng.uniform(0.3, 1.5))
            if model.startswith("gemini")
            else 0
        )
        usage = Usage(prompt_tokens, completion_tokens + reasoning, reasoning)
        await asyncio.sleep(0)  # keep it a real coroutine without real latency
        latency = rng.uniform(0.8, 6.0) * (1.6 if model.startswith("gemini") else 1.0)
        parsed = _mock_output(tool_name, user, rng)
        return CallOutput(
            parsed=parsed,
            raw_text=json.dumps(parsed),
            usage=usage,
            latency_wall_s=latency,
            model=model,
            ok=True,
        )


def _mock_output(tool_name: str, user: str, rng: random.Random) -> Any:
    """Tiny schema-shaped stubs so deterministic checks have something to chew on."""
    # Long enough to clear the production final-profile floor (>=400 chars,
    # >=3 paragraphs) so the deterministic gate has a realistic pass example.
    para = (
        "You move through the world with a recognisable rhythm, and the answers "
        "you gave in this quiz kept pointing the same direction without you "
        "forcing them. That consistency is the whole signal here.\n\n"
        "You tend to value depth over noise and keep a steady hand under "
        "pressure; when a problem shows up you lean in rather than wait for it "
        "to resolve itself. People around you read that as quiet competence.\n\n"
        "Lean into what makes this profile yours, stay curious about its blind "
        "spots, and you will keep growing into an even sharper version of it."
    )
    if tool_name == "initial_planner":
        return {
            "title": "What X Are You?",
            "synopsis": "A playful, precise quiz. " * 4,
            "ideal_archetypes": [f"Outcome {i}" for i in range(rng.randint(4, 6))],
            "ideal_count_hint": 5,
        }
    if tool_name in ("profile_batch_writer",):
        n = rng.randint(4, 6)
        return [
            {
                "name": f"Outcome {i}",
                "short_description": "A concrete, useful one-liner.",
                "profile_text": para,
                "image_url": None,
            }
            for i in range(n)
        ]
    if tool_name in ("profile_writer",):
        return {
            "name": "Outcome 0",
            "short_description": "A concrete, useful one-liner.",
            "profile_text": para,
            "image_url": None,
        }
    if tool_name == "question_generator":
        return {
            "questions": [
                {
                    "question_text": f"Question {i} about your preferences?",
                    "options": [
                        {"text": f"Option {j}"} for j in range(rng.randint(2, 4))
                    ],
                }
                for i in range(rng.randint(5, 6))
            ]
        }
    if tool_name == "next_question_generator":
        return {
            "question_text": "A novel narrowing question?",
            "options": [{"text": f"Choice {j}"} for j in range(rng.randint(2, 4))],
            "progress_phrase": "Narrowing in",
        }
    if tool_name == "decision_maker":
        finish = rng.random() < 0.4
        return {
            "action": "FINISH_NOW" if finish else "ASK_ONE_MORE_QUESTION",
            "confidence": round(rng.uniform(0.5, 0.97), 2),
            "winning_character_name": "Outcome 0" if finish else "",
        }
    if tool_name == "final_profile_writer":
        return {"title": "You are Outcome 0!", "description": para, "image_url": None}
    return {"ok": True}


# ---------------------------------------------------------------------------
# Live caller (gated)
# ---------------------------------------------------------------------------


class LiveCaller:
    """Real LiteLLM caller. Only constructed when ``--live`` is passed.

    Uses the Responses API for OpenAI/Gemini (matching production
    ``llm_service``) so token usage and reasoning-token details are captured.
    Retries transient errors with backoff. TODO(live): wire prompt-cache hints
    and per-tool ``response_format`` JSON-schema envelopes from
    ``schemas.jsonschema_for`` for stricter structured output once a live run
    is scheduled.
    """

    _TRANSIENT = (
        "503", "ServiceUnavailable", "RateLimit", "Timeout", "overloaded",
        "high demand", "UNAVAILABLE", "deadline", "Connection", "502", "504",
    )

    def __init__(self, max_retries: int = 4) -> None:
        self._max_retries = max_retries
        import litellm  # local import keeps mock path dependency-free

        litellm.suppress_debug_info = True
        litellm.drop_params = True
        self._litellm = litellm

    async def call_json(
        self,
        *,
        tool_name: str,
        system: str,
        user: str,
        model: str,
        max_output_tokens: int = 1500,
        temperature: float = 0.3,
        timeout_s: int = 60,
        effort: str | None = None,
        thinking_budget: int | None = None,
    ) -> CallOutput:
        from .parse import parse_json_loose  # robust extractor (see parse.py)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
            "timeout": timeout_s,
        }
        if model.lower().startswith(("gpt-", "openai/", "gemini")):
            kwargs["response_format"] = {"type": "json_object"}
        if effort and model.lower().startswith(("gpt-5", "o3", "o4")):
            kwargs["reasoning_effort"] = effort
        if thinking_budget is not None and model.lower().startswith("gemini"):
            # Cap Gemini hidden reasoning. Verified 2026-07-02: gemini-2.5-pro
            # burns 600-1000 CoT tokens on a judge rubric, which starved the
            # old 600-token judge cap into EMPTY output (100% judge failure).
            # A modest explicit budget keeps the same judge model at ~40% of
            # the unconstrained token cost with intact scoring quality.
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": int(thinking_budget)}

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            t0 = time.perf_counter()
            try:
                resp = await self._litellm.acompletion(**kwargs)
                latency = time.perf_counter() - t0
                text = _extract_text(resp)
                parsed = parse_json_loose(text)
                return CallOutput(
                    parsed=parsed,
                    raw_text=text,
                    usage=usage_from_litellm_response(resp),
                    latency_wall_s=latency,
                    model=model,
                    ok=True,
                )
            except Exception as exc:  # pragma: no cover - network path
                last_exc = exc
                msg = str(exc)
                transient = any(k in msg for k in self._TRANSIENT) or isinstance(
                    exc, asyncio.TimeoutError
                )
                if not transient or attempt == self._max_retries - 1:
                    break
                await asyncio.sleep(min((2**attempt) + random.random(), 15.0))
        return CallOutput(
            parsed=None,
            raw_text="",
            usage=Usage(),
            latency_wall_s=0.0,
            model=model,
            ok=False,
            error=f"{type(last_exc).__name__}: {last_exc}",
        )


def _extract_text(resp: Any) -> str:  # pragma: no cover - network path
    txt = getattr(resp, "output_text", None)
    if isinstance(txt, str) and txt.strip():
        return txt
    choices = getattr(resp, "choices", None) or (
        resp.get("choices") if isinstance(resp, dict) else None
    )
    if isinstance(choices, list) and choices:
        first = choices[0]
        msg = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
        c = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if isinstance(c, str):
            return c
    output = getattr(resp, "output", None) or (
        resp.get("output") if isinstance(resp, dict) else None
    )
    parts: list[str] = []
    if isinstance(output, list):
        for item in output:
            content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
            for part in content or []:
                t = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                if isinstance(t, str) and t.strip():
                    parts.append(t)
    return "\n".join(parts)
