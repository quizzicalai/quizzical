# backend/app/api/endpoints/events.py
"""
First-party product analytics ingest (P1 Virality §C).

A deliberately tiny, vendor-free funnel endpoint. The frontend ``track()`` util
POSTs ``{ event, props? }`` here; we validate a small, strict payload and emit a
single structured ``analytics.event`` ``structlog`` line. No third-party SDK, no
DB table, no PII — operators read the funnel straight out of the existing log
pipeline.

Design constraints (all enforced below):
- Allow-listed event names only (``quiz_start`` / ``quiz_complete`` /
  ``share_click``). Anything else → 422. This keeps the cardinality bounded and
  prevents the endpoint becoming an open log-injection sink.
- ``props`` is an optional flat map of small scalar values, hard-capped in key
  count and string length. No nested objects, no PII fields.
- Per-IP token-bucket rate limit (fail-open on Redis errors, mirroring the rest
  of the app). Body size is already capped by the global body-size middleware.
- Always returns ``204 No Content`` on accept; never leaks internal errors.
"""
from __future__ import annotations

from typing import Any, Literal

import structlog
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.dependencies import get_redis_client
from app.core.config import settings
from app.security.rate_limit import RateLimiter, _client_ip

router = APIRouter(tags=["Analytics"])
logger = structlog.get_logger(__name__)

# Allow-listed funnel events. Extend deliberately — every name here becomes a
# log dimension operators may build dashboards on.
ALLOWED_EVENTS = ("quiz_start", "quiz_complete", "share_click")

# Defensive caps on the optional props bag.
_MAX_PROPS = 10
_MAX_KEY_LEN = 40
_MAX_VALUE_LEN = 200

# Per-IP rate limit for the events endpoint. Kept generous (a single user fires
# at most a handful of funnel events per session) but bounded so a script can't
# flood the log pipeline. Falls back to sane defaults if config lacks the knob.
_EVENTS_CAPACITY = 60
_EVENTS_REFILL_PER_SECOND = 1.0


class AnalyticsEvent(BaseModel):
    """Strict, minimal analytics payload.

    ``extra="forbid"`` rejects unknown top-level keys (e.g. a client trying to
    smuggle PII alongside the event) with a 422.
    """

    model_config = ConfigDict(extra="forbid")

    event: Literal["quiz_start", "quiz_complete", "share_click"]
    # Optional flat props bag. Values may be str/int/float/bool only.
    props: dict[str, Any] | None = Field(default=None)

    @field_validator("props")
    @classmethod
    def _validate_props(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("props must be an object")
        if len(v) > _MAX_PROPS:
            raise ValueError(f"props may contain at most {_MAX_PROPS} keys")
        cleaned: dict[str, Any] = {}
        for key, value in v.items():
            if not isinstance(key, str) or not key:
                raise ValueError("props keys must be non-empty strings")
            if len(key) > _MAX_KEY_LEN:
                raise ValueError(f"props key exceeds {_MAX_KEY_LEN} chars")
            # Only allow small scalars. Reject nested structures and None.
            if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
                cleaned[key] = value
            elif isinstance(value, str):
                if len(value) > _MAX_VALUE_LEN:
                    raise ValueError(f"props value for '{key}' exceeds {_MAX_VALUE_LEN} chars")
                cleaned[key] = value
            else:
                raise ValueError(
                    f"props value for '{key}' must be a string, number, or boolean"
                )
        return cleaned


async def _enforce_events_rate_limit(request: Request) -> bool:
    """Per-IP token-bucket throttle. Returns True if the request is allowed.

    Fails open (allows) on any Redis/config error so analytics never blocks or
    errors the user-facing flow.
    """
    try:
        # Honour the app-wide rate-limit enable flag if present.
        rl_cfg = getattr(getattr(settings, "security", None), "rate_limit", None)
        if rl_cfg is not None and not getattr(rl_cfg, "enabled", True):
            return True
        redis_client = get_redis_client()
        limiter = RateLimiter(
            redis=redis_client,
            capacity=_EVENTS_CAPACITY,
            refill_per_second=_EVENTS_REFILL_PER_SECOND,
        )
        key = f"rl:events:{_client_ip(request)}"
        res = await limiter.check(key)
        return bool(res.allowed)
    except Exception:
        logger.warning("analytics.rate_limit.fail_open", exc_info=True)
        return True


@router.post(
    "/events",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
    summary="Ingest a first-party funnel analytics event",
)
async def ingest_event(
    payload: AnalyticsEvent,
    request: Request,
) -> Response:
    """Validate a funnel event and emit a structured log line.

    Returns 204 on accept; 422 for invalid payloads (handled by FastAPI). On a
    rate-limit hit we still return 204 (drop-and-ack) so the client never
    retries — analytics loss is acceptable, user-facing errors are not.
    """
    allowed = await _enforce_events_rate_limit(request)
    if not allowed:
        # Silently drop over-limit events; do not surface a 429 to the FE.
        logger.info("analytics.event.dropped", event=payload.event)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Emit the single structured event line. NO PII: we log only the event
    # name and the validated, size-capped scalar props. Client IP is
    # intentionally omitted from the structured payload.
    logger.info(
        "analytics.event",
        event=payload.event,
        props=payload.props or {},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
