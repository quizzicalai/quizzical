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

# PII hygiene: only these prop keys are ever logged. Any other key is *silently
# dropped* (not 422'd) so a client can never smuggle free-form / identifying
# text into the structured logs, even by accident. Extend deliberately — every
# key here must be low-cardinality and non-identifying. Today the funnel only
# uses ``method`` (the share channel: copy/native/x/facebook/...).
_ALLOWED_PROP_KEYS = frozenset(
    {
        "method",   # share_click channel label
        "source",   # optional UI surface label
        "variant",  # optional A/B / experiment bucket
    }
)

# Per-IP rate limit for the events endpoint. Kept generous (a single user fires
# at most a handful of funnel events per session) but bounded so a script can't
# flood the log pipeline. Falls back to sane defaults if config lacks the knob.
_EVENTS_CAPACITY = 60
_EVENTS_REFILL_PER_SECOND = 1.0


def _coerce_scalar(key: str, value: Any) -> Any:
    """Validate a single prop value is a small scalar; raise otherwise.

    Extracted from the model validator to keep its cyclomatic complexity within
    the lint budget (C901). bool/int/float pass through; str is length-capped;
    everything else (nested structures, None, lists) is rejected.
    """
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_VALUE_LEN:
            raise ValueError(f"props value for '{key}' exceeds {_MAX_VALUE_LEN} chars")
        return value
    raise ValueError(f"props value for '{key}' must be a string, number, or boolean")


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
            # PII hygiene: silently drop any key not on the allow-list so
            # arbitrary / identifying fields never reach the structured logs.
            if key not in _ALLOWED_PROP_KEYS:
                continue
            cleaned[key] = _coerce_scalar(key, value)
        return cleaned or None


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

    Returns 204 on accept; 422 for invalid payloads (handled by FastAPI). When
    *this endpoint's own* per-IP limiter trips we still return 204 (drop-and-ack)
    so the client never retries — analytics loss is acceptable, user-facing
    errors are not.

    NOTE: the app-wide rate-limit middleware (``rate_limit_middleware`` in
    ``app.main``) runs BEFORE this handler and can still emit a 429 under
    extreme abuse, since its allow-list lives in ``config.py`` (owned
    elsewhere). To make the funnel endpoint truly never 429, the config owner
    should add the mounted path prefix (``/api/v1/events``) to
    ``security.rate_limit.allow_paths``. Flagged in the PR as a follow-up.
    """
    allowed = await _enforce_events_rate_limit(request)
    if not allowed:
        # Silently drop over-limit events; do not surface a 429 to the FE.
        logger.info("analytics.event.dropped", event_name=payload.event)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Emit the single structured event line. NO PII: we log only the event
    # name and the validated, size-capped scalar props. Client IP is
    # intentionally omitted from the structured payload.
    #
    # NOTE: the funnel event name is logged as `event_name`, NOT `event` —
    # structlog's BoundLogger.info() reserves the first positional ("event") as
    # the log-message key, so an `event=` kwarg collides with it (TypeError).
    logger.info(
        "analytics.event",
        event_name=payload.event,
        props=payload.props or {},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
