# app/security/rate_limit.py
"""§15.1 — Distributed Redis token-bucket rate limiter.

Design:
- One small Lua script per request → atomic check + refill + decrement.
- Fail-open on Redis errors so infrastructure failures never DOS users.
- Bucket key derived from client IP + coarse route prefix (so a flood on
  one endpoint doesn't drain a different endpoint's budget).
- Middleware is enabled/disabled via ``settings.security.rate_limit.enabled``.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.errors import build_error_envelope

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Lua: atomic token bucket
# ---------------------------------------------------------------------------
# KEYS[1] = bucket key
# ARGV[1] = capacity      (int)
# ARGV[2] = refill_per_second (float, as string)
# ARGV[3] = now_seconds   (float)
# Returns: { allowed (1/0), remaining (int), retry_after_seconds (int) }
TOKEN_BUCKET_LUA = """
local capacity      = tonumber(ARGV[1])
local refill_rate   = tonumber(ARGV[2])
local now           = tonumber(ARGV[3])

local data = redis.call('HMGET', KEYS[1], 'tokens', 'updated_at')
local tokens     = tonumber(data[1])
local updated_at = tonumber(data[2])

if tokens == nil then
  tokens = capacity
  updated_at = now
else
  local delta = math.max(0, now - updated_at)
  tokens = math.min(capacity, tokens + (delta * refill_rate))
  updated_at = now
end

local allowed = 0
local retry_after = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  if refill_rate > 0 then
    retry_after = math.ceil((1 - tokens) / refill_rate)
  else
    retry_after = 60
  end
end

redis.call('HMSET', KEYS[1], 'tokens', tokens, 'updated_at', updated_at)
-- Auto-expire idle buckets after a generous window so we don't leak keys.
redis.call('EXPIRE', KEYS[1], 3600)

return { allowed, math.floor(tokens), retry_after }
"""


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_s: int
    fail_open: bool = False


def bucket_key(*, client_ip: str, path: str) -> str:
    """Derive a bucket key. Coarse route prefix avoids per-URL cardinality."""
    # Bucket by `/api/<first-segment>` (or whole path if shorter).
    parts = [p for p in (path or "/").split("/") if p]
    coarse = "/" + "/".join(parts[:2]) if parts else "/"
    return f"rl:{client_ip}|{coarse}"


def _client_ip(request: Request) -> str:
    # Honour X-Forwarded-For first hop (Kong / Container Apps usually inject).
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    try:
        return (request.client.host if request.client else "unknown") or "unknown"
    except Exception:
        return "unknown"


class RateLimiter:
    """Stateless wrapper over a Redis client + Lua script."""

    def __init__(
        self,
        *,
        redis,
        capacity: int = 30,
        refill_per_second: float = 1.0,
    ) -> None:
        self._redis = redis
        self._capacity = int(capacity)
        self._refill = float(refill_per_second)

    async def check(self, key: str, *, now_s: float | None = None) -> RateLimitResult:
        import time as _time
        now = float(now_s) if now_s is not None else _time.time()
        try:
            res = await self._redis.eval(
                TOKEN_BUCKET_LUA, 1, key,
                str(self._capacity), str(self._refill), str(now),
            )
        except Exception as e:
            logger.warning("rate_limit.fail_open", error=str(e), key=key)
            return RateLimitResult(allowed=True, remaining=self._capacity, retry_after_s=0, fail_open=True)
        try:
            allowed, remaining, retry_after = int(res[0]), int(res[1]), int(res[2])
        except Exception:
            return RateLimitResult(allowed=True, remaining=self._capacity, retry_after_s=0, fail_open=True)
        return RateLimitResult(allowed=bool(allowed), remaining=remaining, retry_after_s=retry_after)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Starlette middleware. Skips allowlisted paths and fails open on Redis errors."""

    def __init__(
        self,
        app,
        *,
        redis_factory: Callable[[], object],
        capacity: int = 30,
        refill_per_second: float = 1.0,
        allow_paths: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self._redis_factory = redis_factory
        self._capacity = capacity
        self._refill = refill_per_second
        self._allow_paths = list(allow_paths or [])
        self._enabled = enabled

    def _is_allowlisted(self, path: str) -> bool:
        if not path:
            return True
        # Exact-match for "/" (root redirect); prefix-match for everything else.
        for p in self._allow_paths:
            if p == "/" and path == "/":
                return True
            if p != "/" and path.startswith(p):
                return True
        return False

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path or "/"
        if not self._enabled or self._is_allowlisted(path):
            return await call_next(request)

        try:
            redis = self._redis_factory()
        except Exception as e:
            logger.warning("rate_limit.fail_open", error=str(e), where="redis_factory")
            return await call_next(request)

        limiter = RateLimiter(
            redis=redis, capacity=self._capacity, refill_per_second=self._refill
        )
        key = bucket_key(client_ip=_client_ip(request), path=path)
        res = await limiter.check(key)

        if not res.allowed:
            body = build_error_envelope(
                status_code=429,
                detail="Too many requests. Please slow down.",
                error_code="RATE_LIMITED",
            )
            response = JSONResponse(body, status_code=429)
            response.headers["Retry-After"] = str(max(1, res.retry_after_s))
            response.headers["X-RateLimit-Limit"] = str(self._capacity)
            response.headers["X-RateLimit-Remaining"] = "0"
            return response

        response = await call_next(request)
        try:
            response.headers.setdefault("X-RateLimit-Limit", str(self._capacity))
            response.headers.setdefault("X-RateLimit-Remaining", str(max(0, res.remaining)))
        except Exception:
            pass
        return response
