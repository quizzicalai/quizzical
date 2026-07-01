# backend/app/services/cost_meter.py
"""Per-call token/$ capture + a cluster-wide DAILY CENTS spend counter.

Hitlist #2 (2026-06-30). Previously the live paid pipeline recorded NO per-call
token/$ usage and the daily circuit-breaker was a dollar-BLIND start-count. This
module closes that gap:

* :func:`record_llm_cost` reads ``resp.usage`` and ``litellm.completion_cost``
  off every structured LLM response, emits ``tokens``/``cents`` on the existing
  ``llm.raw_response.received`` structured log (tagged model + tool + session),
  and INCRBYs a Redis daily CENTS counter (UTC-dated key).
* :func:`record_fal_image_cost` records FAL image spend (~$0.011/image) into the
  SAME counter so the image fan-out draws down the same budget as LLM spend.
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


async def reserve_estimated_cents(redis_client: Any, cents: int) -> int | None:
    """Hitlist #1 (2026-06-30) — reserve an ESTIMATED per-quiz cost at admission.

    The dollar breaker only READS the counter; per-call spend is recorded AFTER
    the agent runs. Between an admission and the first recorded cent a concurrent
    burst all reads the SAME pre-burst total and every request is admitted, so
    the live pipeline can overshoot the daily $ ceiling by ``(in-flight count) ×
    (per-quiz cost)``. Reserving an estimate via an atomic INCRBY at admission
    makes concurrent admissions see each other's reservations, turning the soft
    ceiling into a near-hard one (the breaker reads the reserved total).

    Returns the new counter total (so the caller can re-check the ceiling after
    its own reservation lands), or ``None`` on a non-positive amount / Redis
    fault. FAIL OPEN (hard contract): a reservation fault must NEVER block a
    legitimate quiz — the read-check and the per-IP/session caps remain the front
    line; this is defense-in-depth. ``record_cents`` already swallows its own
    errors and sets the ~25h TTL atomically, so reuse it."""
    if cents <= 0:
        return None
    return await record_cents(redis_client, int(cents))


async def reconcile_reservation(
    redis_client: Any, *, estimated_cents: int, actual_cents: int
) -> int | None:
    """Hitlist #1 — RECONCILE a prior reservation to ACTUAL spend on completion.

    A quiz reserves ``estimated_cents`` up front (``reserve_estimated_cents``).
    When it finishes we know the real spend, so we adjust the counter by the
    signed delta ``actual - estimated`` so the day's counter converges to true
    spend rather than the (deliberately conservative) estimate:

      * actual  > estimated  -> INCRBY (delta)   (counter was under-reserved)
      * actual  < estimated  -> DECRBY (-delta)  (release the over-reservation)
      * actual == estimated  -> no-op

    Per-call ``record_llm_cost`` / ``record_fal_image_cost`` ran DURING the quiz
    and already added the real cents on top of the reservation, so on completion
    we REMOVE the estimate again to avoid double-counting (the real spend stays).
    Callers therefore pass ``actual_cents=0`` when per-call metering already
    accrued the spend — that releases the whole reservation, leaving only the
    metered real cents. FAIL OPEN: a fault must never raise into the caller.

    A floor of 0 is enforced (never drive the counter negative). Returns the new
    total or ``None`` on a fault / no client."""
    if redis_client is None:
        return None
    delta = int(actual_cents) - int(estimated_cents)
    if delta == 0:
        return await read_daily_cents(redis_client)
    key = daily_cents_key()
    try:
        if delta > 0:
            total = int(await redis_client.incrby(key, delta))
        else:
            # DECRBY is widely supported; fall back to a negative INCRBY if a
            # client/fake lacks it. Either way clamp the stored value at >= 0.
            decrby = getattr(redis_client, "decrby", None)
            if callable(decrby):
                total = int(await decrby(key, -delta))
            else:
                total = int(await redis_client.incrby(key, delta))
        if total < 0:
            # Never leave a negative counter (a later read would mis-trip the
            # breaker open). Clamp ATOMICALLY: add back exactly the overshoot via
            # an INCRBY of ``-total`` (``total`` is negative, so this is a
            # positive add) instead of a blind ``SET(key, 0)``. A blind SET would
            # clobber a concurrent ``record_cents`` INCRBY (real LLM/FAL spend)
            # that landed between our DECRBY and the reseat back to 0; the
            # re-increment preserves any such concurrent write.
            try:
                total = int(await redis_client.incrby(key, -total))
                # If a concurrent write made it positive again, keep that value;
                # otherwise it's exactly 0. Either way, re-assert the TTL.
                await redis_client.expire(key, _DAILY_TTL_S)
                return max(0, total)
            except Exception:
                logger.debug("cost_meter.reconcile.reseat_fail", exc_info=True)
                return 0
        # Keep the TTL alive on the adjusted key (idempotent).
        try:
            await redis_client.expire(key, _DAILY_TTL_S)
        except Exception:
            logger.debug("cost_meter.reconcile.expire_fail", exc_info=True)
        return total
    except Exception:
        logger.debug("cost_meter.reconcile.fail", exc_info=True)
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


async def record_fal_image_cost(n_images: int) -> None:
    """Record FAL image spend (``n_images`` × ``fal_image_cost_usd``) into the
    daily cents counter. Best-effort / fail-open."""
    try:
        if n_images <= 0:
            return
        cfg = _live_cost_cfg()
        per_image = float(getattr(cfg, "fal_image_cost_usd", 0.011) or 0.0) if cfg else 0.011
        cents = _usd_to_cents(per_image * int(n_images))
        logger.info(
            "image.cost.recorded",
            images=int(n_images),
            per_image_usd=per_image,
            cents=cents,
        )
        if cents > 0:
            redis_client = _get_redis_for_metering()
            await record_cents(redis_client, cents)
    except Exception:
        logger.debug("cost_meter.record_fal_image_cost.fail", exc_info=True)
