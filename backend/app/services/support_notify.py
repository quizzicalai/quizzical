"""Resend-backed support notifier for whimsical-error codes (owner request,
2026-06-30).

When a failure maps to an error code with ``notify_support=True`` (see
:mod:`app.core.error_codes`), the API fires a FIRE-AND-FORGET email to
support@quafel.com so the team can triage. The send is:

  * **Resend over a thin HTTP POST** — uses ``httpx`` (already a dependency); no
    SDK / no new package, so ``poetry.lock`` is untouched.
  * **No-op without the key** — ``RESEND_API_KEY`` is added LATER. When it is
    absent the notifier logs a single WARNING and returns immediately. It NEVER
    raises and NEVER blocks the request.
  * **Rate-limited + deduped by code** — at most ONE email per code per
    ``DEDUPE_TTL_S`` (~15 min) via an atomic Redis ``SET key val NX EX`` dedupe
    key. A Redis outage fails OPEN toward *sending* only if we cannot reach the
    dedupe key at all? No — see ``_should_send``: a Redis fault is treated as
    "cannot dedupe" and we SKIP the send (fail toward NOT spamming), because a
    Redis-down incident is exactly when many requests would fail at once and we
    must not emit a storm.
  * **Fail-open / fire-and-forget** — every public entry point swallows its own
    errors and is scheduled via ``asyncio.create_task`` so it can never delay,
    block, or fail the user request.
  * **NON-PII context only** — code, trace_id, severity, http_status, and a
    short internal description. Never the category text, answers, or result.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from app.core.error_codes import ErrorCodeSpec

logger = structlog.get_logger(__name__)

# Resend transactional-email API.
_RESEND_ENDPOINT = "https://api.resend.com/emails"
# At most one email per code per this window (owner: "~15 min").
DEDUPE_TTL_S = 900
# Hard timeout on the outbound POST so a slow Resend can never tie up a task.
_HTTP_TIMEOUT_S = 5.0

# From/To. ``RESEND_FROM`` can override the sender once the domain is verified;
# until then a verified Resend sandbox/onboarding sender works for testing.
_DEFAULT_FROM = "Quafel Alerts <alerts@quafel.com>"
_SUPPORT_TO = "support@quafel.com"


def _resend_api_key() -> str | None:
    return (os.getenv("RESEND_API_KEY") or "").strip() or None


def _resend_from() -> str:
    return (os.getenv("RESEND_FROM") or "").strip() or _DEFAULT_FROM


def _support_to() -> str:
    return (os.getenv("SUPPORT_NOTIFY_TO") or "").strip() or _SUPPORT_TO


def _dedupe_key(code: str) -> str:
    return f"support_notify:dedupe:{code}"


def _get_redis() -> Any | None:
    """Best-effort Redis client for the dedupe key. Returns None on any fault."""
    try:
        from app.api.dependencies import get_redis_client

        return get_redis_client()
    except Exception:
        return None


async def _should_send(code: str) -> bool:
    """Atomically claim the per-code dedupe slot. True iff this caller won the
    slot (i.e. no email for ``code`` was sent in the last ``DEDUPE_TTL_S``).

    Uses ``SET key 1 NX EX ttl`` so the check-and-claim is a single atomic op
    (no get-then-set race across concurrent failures / replicas).

    Fail toward NOT sending on any Redis fault or when Redis is unavailable: a
    Redis-down incident is precisely when a flood of failures would otherwise
    each try to email, so we must not turn an outage into an email storm. The
    one downside — a genuinely novel error during a Redis outage goes
    un-emailed — is acceptable; those failures are still in the logs.
    """
    redis_client = _get_redis()
    if redis_client is None:
        logger.debug("support_notify.dedupe.no_redis", code=code)
        return False
    try:
        # redis-py asyncio: SET ... nx=True ex=ttl returns True when the key was
        # set (we won the slot), None/False when it already existed.
        won = await redis_client.set(_dedupe_key(code), "1", nx=True, ex=DEDUPE_TTL_S)
        return bool(won)
    except Exception:
        logger.debug("support_notify.dedupe.redis_fail", code=code, exc_info=True)
        return False


def _build_payload(
    spec: ErrorCodeSpec,
    *,
    trace_id: str | None,
    path: str | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the Resend email payload. NON-PII only."""
    safe_context = ""
    if context:
        # Only stringify simple scalars; never echo arbitrary nested user data.
        parts = []
        for k, v in context.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                parts.append(f"{k}: {v}")
        if parts:
            safe_context = "\n".join(parts)

    subject = f"[Quafel] {spec.severity.value.upper()} {spec.code}"
    lines = [
        "A Quafel failure tripped a support-notify error code.",
        "",
        f"Code:        {spec.code}",
        f"Severity:    {spec.severity.value}",
        f"HTTP status: {spec.http_status}",
        f"Cause:       {spec.internal_description}",
        f"Trace ID:    {trace_id or 'n/a'}",
        f"Path:        {path or 'n/a'}",
    ]
    if safe_context:
        lines += ["", "Context:", safe_context]
    lines += [
        "",
        "This is an automated, rate-limited alert (max 1 per code per ~15 min).",
        "No user PII is included.",
    ]
    text = "\n".join(lines)
    return {
        "from": _resend_from(),
        "to": [_support_to()],
        "subject": subject,
        "text": text,
    }


async def _post_to_resend(api_key: str, payload: dict[str, Any]) -> None:
    """Thin HTTP POST to Resend. Never raises out of here."""
    try:
        import httpx  # local import: keeps module import cheap

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                _RESEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code >= 400:
            logger.warning(
                "support_notify.resend.non_2xx",
                status_code=resp.status_code,
                # Resend error bodies are non-PII (validation/auth messages).
                body=resp.text[:500],
            )
        else:
            logger.info(
                "support_notify.resend.sent",
                status_code=resp.status_code,
                subject=payload.get("subject"),
            )
    except Exception:
        # Network error / DNS / timeout — alerting is best-effort.
        logger.warning("support_notify.resend.post_failed", exc_info=True)


async def _notify_async(
    spec: ErrorCodeSpec,
    *,
    trace_id: str | None,
    path: str | None,
    context: dict[str, Any] | None,
) -> None:
    """The actual notify coroutine. Fully self-contained / fail-open."""
    try:
        api_key = _resend_api_key()
        if api_key is None:
            # The key is added LATER — graceful no-op + single warning.
            logger.warning(
                "support_notify.skipped_no_key",
                code=spec.code,
                trace_id=trace_id,
                reason="RESEND_API_KEY not configured",
            )
            return

        if not await _should_send(spec.code):
            logger.debug(
                "support_notify.deduped",
                code=spec.code,
                trace_id=trace_id,
            )
            return

        payload = _build_payload(spec, trace_id=trace_id, path=path, context=context)
        await _post_to_resend(api_key, payload)
    except Exception:
        # Belt-and-suspenders: nothing in here may ever escape.
        logger.debug("support_notify.notify_async.error", exc_info=True)


def maybe_notify_support(
    spec: ErrorCodeSpec,
    *,
    trace_id: str | None = None,
    path: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget support notification for a ``notify_support=True`` code.

    Returns IMMEDIATELY. The actual send is scheduled on the event loop so it
    can never block, delay, or fail the user request. A no-op when:
      * ``spec.notify_support`` is False;
      * there is no running event loop (e.g. called from sync context) — in
        which case we still never raise.
    """
    if not spec.notify_support:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — we cannot fire-and-forget. Skip silently (the
        # failure is already logged by the caller's handler).
        logger.debug("support_notify.no_event_loop", code=spec.code)
        return
    try:
        task = loop.create_task(
            _notify_async(spec, trace_id=trace_id, path=path, context=context)
        )
        # Swallow any late exception from the detached task so it never surfaces
        # as an "exception was never retrieved" warning or unhandled error.
        task.add_done_callback(_consume_task_result)
    except Exception:
        logger.debug("support_notify.schedule_failed", code=spec.code, exc_info=True)


def _consume_task_result(task: "asyncio.Task[Any]") -> None:
    try:
        task.result()
    except Exception:
        logger.debug("support_notify.task_exception", exc_info=True)


__all__ = ["maybe_notify_support", "DEDUPE_TTL_S"]
