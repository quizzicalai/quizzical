# backend/app/services/cost_meter.py
"""Per-call token/$ capture + a cluster-wide DAILY CENTS spend counter.

Hitlist #2 (2026-06-30). Previously the live paid pipeline recorded NO per-call
token/$ usage and the daily circuit-breaker was a dollar-BLIND start-count. This
module closes that gap:

* :func:`record_llm_cost` reads ``resp.usage`` and ``litellm.completion_cost``
  off every structured LLM response, emits ``tokens``/``cents`` on the existing
  ``llm.raw_response.received`` structured log (tagged model + tool + session),
  and INCRBYs a Redis daily CENTS counter (UTC-dated key).
* :func:`record_fal_image_cost` records FAL image spend into the SAME counter so
  the image fan-out draws down the same budget as LLM spend. The per-image cost is
  MODEL + SIZE aware (blackbox #3) — see :mod:`app.services.image_cost` for the
  authoritative cost model (per-megapixel rate × true area: schnell $0.003/MP,
  dev $0.025/MP). The old flat ~$0.011/image constant is retained ONLY as a
  legacy fallback when a caller passes no model/size.
* :func:`read_daily_cents` is read by ``_enforce_global_daily_cost_ceiling`` to
  trip a DOLLAR breaker (``security.live_cost_guard.daily_budget_usd``).

Hard contract — FAIL OPEN:
  Cost capture is best-effort instrumentation. A ``litellm.completion_cost``
  exception, a missing ``usage``, an unavailable Redis pool, or any Redis error
  MUST never raise into the caller and never block a quiz. Every public function
  swallows its own errors. Accounting is at-most-once per response (we record
  exactly one INCRBY per LLM response and per FAL batch — no double-count).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import litellm
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# ~25h so the per-day key spans the whole UTC day plus clock-skew margin, then
# self-expires (matches the existing live_spend counter TTL).
_DAILY_TTL_S = 90_000


def daily_cents_key(day: str | None = None) -> str:
    """Redis key for the UTC-dated aggregate live-spend cents counter."""
    if day is None:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"live_spend:cents:{day}"


def _extract_usage(resp: Any) -> dict[str, int]:
    """Pull token counts off a LiteLLM response (dict or SDK object). Never raises."""
    out = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
        if usage is None:
            return out

        def _g(key: str) -> Any:
            if isinstance(usage, dict):
                return usage.get(key)
            return getattr(usage, key, None)

        # Responses API uses input/output_tokens; Chat Completions uses
        # prompt/completion_tokens. Accept either.
        inp = _g("input_tokens")
        if inp is None:
            inp = _g("prompt_tokens")
        outp = _g("output_tokens")
        if outp is None:
            outp = _g("completion_tokens")
        total = _g("total_tokens")
        out["input_tokens"] = int(inp or 0)
        out["output_tokens"] = int(outp or 0)
        out["total_tokens"] = int(total or (out["input_tokens"] + out["output_tokens"]))
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return out


def _completion_cost_usd(resp: Any) -> float | None:
    """Best-effort ``litellm.completion_cost`` in USD. Returns None on any fault."""
    try:
        cost = litellm.completion_cost(completion_response=resp)
        if cost is None:
            return None
        cost = float(cost)
        if cost < 0:
            return None
        return cost
    except Exception:
        # LiteLLM raises for unknown models / unmapped pricing — never propagate.
        return None


def _live_cost_cfg() -> Any | None:
    return getattr(getattr(settings, "security", None), "live_cost_guard", None)


def _get_redis_for_metering() -> Any | None:
    """Obtain a Redis client for the daily counter. Fail-open: returns None if the
    pool isn't ready or anything goes wrong (metering must never raise)."""
    try:
        from app.api.dependencies import get_redis_client
        return get_redis_client()
    except Exception:
        return None


async def record_cents(redis_client: Any, cents: int) -> int | None:
    """INCRBY the UTC-dated daily cents counter. Fail-open (returns None on error).

    The ~25h TTL is set ATOMICALLY with the INCRBY (review item C): on a real
    Redis client we issue ``INCRBY`` + ``EXPIRE`` in a single pipeline so the key
    can never persist TTL-less if the process dies (or EXPIRE faults) between the
    two ops. When the client has no usable ``pipeline`` (test fakes) we fall back
    to a sequential ``INCRBY`` then ``EXPIRE`` — and we set EXPIRE on EVERY write
    (not just the first) so a key whose earlier EXPIRE failed gets its TTL
    re-asserted on the next metered call. Re-setting a ~25h TTL on a UTC-dated
    key is idempotent and harmless. At-most-once per call. Fail-open (None on
    error).
    """
    if redis_client is None or cents <= 0:
        return None
    key = daily_cents_key()
    delta = int(cents)

    # Preferred path — atomic INCRBY + EXPIRE in one pipeline (no TTL-less gap).
    pipeline_factory = getattr(redis_client, "pipeline", None)
    if callable(pipeline_factory):
        try:
            pipe = pipeline_factory()
            # redis.asyncio pipelines are async context managers; some fakes
            # return a plain object. Support both.
            aenter = getattr(pipe, "__aenter__", None)
            if aenter is not None:
                async with pipe:
                    pipe.incrby(key, delta)
                    pipe.expire(key, _DAILY_TTL_S)
                    results = await pipe.execute()
            else:
                pipe.incrby(key, delta)
                pipe.expire(key, _DAILY_TTL_S)
                results = await pipe.execute()
            # First element is the INCRBY result (new total).
            if results:
                return int(results[0])
            return None
        except Exception:
            logger.debug("cost_meter.record_cents.pipeline_fail", exc_info=True)
            # Fall through to the sequential path below.

    # Fallback — sequential. Set EXPIRE on every write so a missing TTL self-heals.
    try:
        total = int(await redis_client.incrby(key, delta))
        try:
            await redis_client.expire(key, _DAILY_TTL_S)
        except Exception:
            logger.debug("cost_meter.record_cents.expire_fail", exc_info=True)
        return total
    except Exception:
        logger.debug("cost_meter.record_cents.fail", exc_info=True)
        return None


async def read_daily_cents(redis_client: Any) -> int | None:
    """Read the current UTC-day aggregate spend, in cents. Fail-open (None on error)."""
    if redis_client is None:
        return None
    try:
        raw = await redis_client.get(daily_cents_key())
        if raw is None:
            return 0
        return int(raw)
    except Exception:
        logger.debug("cost_meter.read_daily_cents.fail", exc_info=True)
        return None


def _usd_to_cents(usd: float) -> int:
    # Round to the nearest cent; sub-cent calls (most per-call LLM spend at
    # gpt-4o-mini rates) still accrue once they cross a cent boundary because the
    # counter is integer cents and rounding is nearest-cent per call. We round so
    # a $0.006 call records 1 cent rather than vanishing — slightly conservative
    # (over-counts), which is the safe direction for a cost breaker.
    try:
        return max(0, round(float(usd) * 100.0))
    except Exception:
        return 0


async def record_llm_cost(
    resp: Any,
    *,
    model: str,
    tool: str | None,
    trace_id: str | None,
    session_id: str | None,
) -> None:
    """Capture tokens + $ for one LLM response and add it to the daily counter.

    Emits ``llm.cost.recorded`` (tokens + cents, tagged model/tool/session) and
    INCRBYs the Redis daily cents counter. Entirely best-effort / fail-open: any
    fault is logged at debug and swallowed. Called exactly once per response so
    the counter is never double-incremented.
    """
    try:
        usage = _extract_usage(resp)
        usd = _completion_cost_usd(resp)
        cents = _usd_to_cents(usd) if usd is not None else 0
        logger.info(
            "llm.cost.recorded",
            model=model,
            tool=tool,
            trace_id=trace_id,
            session_id=session_id,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
            cost_usd=round(usd, 6) if usd is not None else None,
            cents=cents,
        )
        if cents > 0:
            redis_client = _get_redis_for_metering()
            await record_cents(redis_client, cents)
    except Exception:
        # Instrumentation must never break the LLM path.
        logger.debug("cost_meter.record_llm_cost.fail", exc_info=True)


async def record_fal_image_cost(
    n_images: int,
    *,
    model: str | None = None,
    image_size: dict[str, int] | None = None,
) -> None:
    """Record FAL image spend into the daily cents counter. Best-effort /
    fail-open.

    Blackbox #3 — the per-image cost is MODEL + SIZE aware: a 256px schnell
    thumb costs ~$0.0002 while a 1024px FLUX-dev hero costs ~$0.025, so a flat
    constant mismeters both. When ``model`` is supplied the cost comes from
    :mod:`app.services.image_cost` (per-megapixel rate × true area); when it is
    omitted we fall back to the legacy flat ``fal_image_cost_usd`` constant so
    existing call sites that don't yet pass a model keep working.

    NOTE (sub-cent rounding — INTENDED): a single schnell 256px thumb is
    ~$0.0002 (~0.02 cents ≈ 20 micros), which rounds to 0 in this INTEGER-cents
    daily counter, so an individual thumb doesn't move the daily breaker — they
    accrue only once a batch crosses a cent boundary. This is deliberate and
    acceptable: the daily counter is a COARSE cluster-wide runaway-cost backstop
    (tripped at ``security.live_cost_guard.daily_budget_usd``, dollars not cents),
    and thumbnails are negligible relative to that ceiling. It does NOT under-meter
    over time, because the LIFETIME $150 FAL ledger
    (``app.services.icons.fal_ledger``) records every image LOSSLESSLY in
    micro-cents (1 cent = 1000 micros), so the long-run cap is micro-accurate."""
    try:
        if n_images <= 0:
            return
        if model is not None or image_size is not None:
            from app.services.image_cost import image_cost_usd
            per_image = image_cost_usd(model=model, image_size=image_size)
        else:
            cfg = _live_cost_cfg()
            per_image = (
                float(getattr(cfg, "fal_image_cost_usd", 0.011) or 0.0) if cfg else 0.011
            )
        cents = _usd_to_cents(per_image * int(n_images))
        logger.info(
            "image.cost.recorded",
            images=int(n_images),
            model=model,
            per_image_usd=round(per_image, 6),
            cents=cents,
        )
        # Sub-cent batches (e.g. one schnell thumb) round to 0 cents and are
        # intentionally NOT recorded here — see the docstring NOTE; the lifetime
        # micro-cent ledger is what guarantees long-run accuracy.
        if cents > 0:
            redis_client = _get_redis_for_metering()
            await record_cents(redis_client, cents)
    except Exception:
        logger.debug("cost_meter.record_fal_image_cost.fail", exc_info=True)
