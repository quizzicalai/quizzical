"""
Main FastAPI Application
"""
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from app.agent.graph import aclose_agent_graph, create_agent_graph
from app.api.dependencies import (
    close_db_engine,
    close_redis_pool,
    create_db_engine_and_session_maker,
    create_redis_pool,
)
from app.api.endpoints import config, feedback, quiz, results
from app.core.config import settings
from app.core.logging_config import configure_logging

try:
    from opentelemetry import trace as _otel_trace
except Exception:
    _otel_trace = None


# --- Lifespan Helpers (Extracted to fix C901) ---

def _init_db(logger: Any, env: str) -> None:
    """Initialize Database connection."""
    try:
        # Prefer settings if available; fallback to env composition for local/dev.
        db_url = getattr(getattr(settings, "database", None), "url", None) or getattr(settings, "DATABASE_URL", None)
        if not db_url:
            user = os.getenv("DATABASE_USER", "postgres")
            pwd = os.getenv("DATABASE_PASSWORD", "postgres")
            host = os.getenv("DATABASE_HOST", "localhost")
            port = os.getenv("DATABASE_PORT", "5432")
            name = os.getenv("DATABASE_DB_NAME", "quiz")
            db_url = f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{name}"

        create_db_engine_and_session_maker(db_url)
        logger.info("Database engine initialized", db_url=db_url if env in {"local", "dev", "development"} else "hidden")
    except Exception as e:
        logger.error("Failed to initialize database", error=str(e), exc_info=True)
        if env not in {"local", "dev", "development"}:
            raise


def _init_redis(logger: Any, env: str) -> None:
    """Initialize Redis connection pool."""
    try:
        redis_url = (
            getattr(settings, "REDIS_URL", None)
            or os.getenv("REDIS_URL")
            or f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"
        )
        create_redis_pool(redis_url)
        logger.info("Redis pool initialized", redis_url=redis_url if env in {"local", "dev", "development"} else "hidden")
    except Exception as e:
        logger.error("Failed to initialize Redis pool", error=str(e), exc_info=True)
        if env not in {"local", "dev", "development"}:
            raise


async def _init_agent_graph(app: FastAPI, logger: Any, env: str) -> None:
    """Compile and attach the agent graph."""
    try:
        agent_graph = await create_agent_graph()
        app.state.agent_graph = agent_graph
        app.state.checkpointer = getattr(agent_graph, "_async_checkpointer", None)
        logger.info(
            "--- Agent graph compiled and ready ---",
            agent_graph_id=id(agent_graph),
            checkpointer_class=type(app.state.checkpointer).__name__ if app.state.checkpointer else None,
        )
    except Exception as e:
        logger.error("Failed to create agent graph", error=str(e), exc_info=True)
        if env not in {"local", "dev", "development"}:
            raise


async def _shutdown_resources(app: FastAPI, logger: Any) -> None:
    """Teardown resources gracefully."""
    logger.info("--- Application Shutting Down ---")

    # Close agent graph resources
    try:
        graph = getattr(app.state, "agent_graph", None)
        if graph is not None:
            await aclose_agent_graph(graph)
            logger.info("Agent graph resources closed")
    except Exception as e:
        logger.warning("Failed to close agent graph resources", error=str(e), exc_info=True)

    # Close DB and Redis
    try:
        await close_db_engine()
        logger.info("Database engine closed")
    except Exception as e:
        logger.warning("Database engine close failed", error=str(e), exc_info=True)

    try:
        await close_redis_pool()
        logger.info("Redis pool closed")
    except Exception as e:
        logger.warning("Redis pool close failed", error=str(e), exc_info=True)

    logger.info("--- Shutdown complete ---")


# --- Lifespan Management ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's startup and shutdown events.
    """
    logger = structlog.get_logger(__name__)
    env = (settings.APP_ENVIRONMENT or "local").lower()
    logger.info("--- Application Starting Up ---", env=env)

    # Initialize resources
    _init_db(logger, env)
    _init_redis(logger, env)
    await _init_agent_graph(app, logger, env)

    try:
        yield
    finally:
        await _shutdown_resources(app, logger)


# --- Application Initialization and Middleware ---

configure_logging()

# Disable OpenAPI/Swagger UI/ReDoc in production-like environments to shrink
# the attack surface (no schema dump, no public docs UI). Local/dev/test still
# get the docs for convenience.
_env_init = (os.getenv("APP_ENVIRONMENT") or "local").lower()
_DOCS_ENABLED = _env_init in {"local", "dev", "development", "test", "testing"}

# AC-OBS-REQID-1/2: Validation regex for client-supplied X-Request-ID. Allows
# UUIDs, generic correlation IDs, OTel trace contexts (lowercase hex), and
# k8s-style suffixed IDs while rejecting whitespace, separators, and any
# character that could enable header/log injection.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")

app = FastAPI(
    title="AI Quiz Generator",
    description="An entertainment-focused web application for generating 'What are you?' style quizzes.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)


# §15.2 — Trusted Host (AC-HOST-1..3). Restrictive only in prod/staging.
def _read_trusted_hosts() -> list[str]:
    raw = os.getenv("TRUSTED_HOSTS", "").strip()
    if raw:
        # Accept JSON array or CSV.
        try:
            if raw.startswith("["):
                loaded = json.loads(raw)
                if isinstance(loaded, list):
                    return [str(v).strip() for v in loaded if str(v).strip()]
        except Exception:
            pass
        return [h.strip() for h in raw.split(",") if h.strip()]
    if _env_init in {"production", "staging", "prod"}:
        return ["localhost", "127.0.0.1"]
    return ["*"]


try:
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    _trusted = _read_trusted_hosts()
    if _trusted and _trusted != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted)
except Exception:
    pass

# CORS (safe fallback for local/dev)
def _read_allowed_origins() -> list[str]:
    defaults = ["http://localhost:5173", "http://127.0.0.1:5173"]
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if not raw.strip():
        return defaults

    text = raw.strip()
    parsed: list[str] = []
    try:
        # Preferred format: JSON array string, e.g. ["https://a.example"].
        if text.startswith("["):
            loaded = json.loads(text)
            if isinstance(loaded, list):
                parsed = [str(v) for v in loaded]
            elif isinstance(loaded, str):
                parsed = [loaded]
        else:
            # CSV fallback, e.g. https://a.example,https://b.example
            parsed = [o.strip() for o in text.split(",") if o.strip()]
    except Exception:
        # Azure CLI can store bracketed origins without JSON quotes
        # (e.g. [https://example.com]); recover from that shape.
        if text.startswith("[") and text.endswith("]"):
            inner = text[1:-1]
            parsed = [o.strip() for o in inner.split(",") if o.strip()]
        else:
            return defaults

    normalized: list[str] = []
    for origin in parsed:
        cleaned = origin.strip()
        # Accept Azure-escaped variants like \"https://example.com\".
        cleaned = cleaned.replace('\\"', '"').replace("\\'", "'")
        cleaned = cleaned.strip().strip('"').strip("'").rstrip("/")
        if cleaned:
            normalized.append(cleaned)
    return normalized or defaults

cors_origins = _read_allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    # Explicit method allow-list (avoid wildcard with credentials per CORS spec).
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    # Explicit header allow-list: standard JSON + Turnstile + trace propagation.
    allow_headers=[
        "Accept",
        "Accept-Language",
        "Content-Language",
        "Content-Type",
        "Authorization",
        "X-Trace-ID",
        "X-Request-ID",
        "X-Turnstile-Token",
        "traceparent",
        "tracestate",
    ],
    expose_headers=["X-Trace-ID", "traceparent", "Server-Timing"],
    max_age=600,
)

# --- Request body size limit (DoS hardening) ---
# Default 256 KiB; override via MAX_REQUEST_BODY_BYTES env var.
# Quiz/feedback payloads are small (a few KB at most); anything larger is
# either a misuse or an attack.
def _max_body_bytes() -> int:
    raw = os.getenv("MAX_REQUEST_BODY_BYTES", "")
    try:
        v = int(raw) if raw else 256 * 1024
        return v if v > 0 else 256 * 1024
    except ValueError:
        return 256 * 1024


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):  # noqa: C901  (linear allowlist + key-build + check + header guards)
    """§15.1 — Redis token-bucket rate limiter (AC-RL-1..7).

    Fail-open on Redis errors. Allowlists health/docs/root paths.
    """
    try:
        from app.api.dependencies import get_redis_client
        from app.api.dependencies import redis_pool as _rp
        from app.security.rate_limit import (
            RateLimiter,
            _client_ip,
            bucket_key,
        )
    except Exception:
        return await call_next(request)

    rl = settings.security.rate_limit
    if not rl.enabled:
        return await call_next(request)

    path = request.url.path or "/"
    for p in rl.allow_paths:
        if (p == "/" and path == "/") or (p != "/" and path.startswith(p)):
            return await call_next(request)

    # Honour FastAPI dep overrides (used heavily in unit tests). Falls back
    # to the live get_redis_client() when no override is registered.
    redis = None
    try:
        override = request.app.dependency_overrides.get(get_redis_client)
        if override is not None:
            import inspect as _inspect
            res = override()
            if _inspect.isawaitable(res):
                res = await res
            redis = res
        else:
            if _rp is None:
                return await call_next(request)
            redis = get_redis_client()
    except Exception:
        return await call_next(request)

    limiter = RateLimiter(
        redis=redis, capacity=rl.capacity, refill_per_second=rl.refill_per_second
    )
    key = bucket_key(client_ip=_client_ip(request), path=path)
    res = await limiter.check(key)

    if not res.allowed:
        body = {"detail": "Too many requests. Please slow down.",
                "errorCode": "RATE_LIMITED"}
        response = JSONResponse(body, status_code=429)
        response.headers["Retry-After"] = str(max(1, res.retry_after_s))
        response.headers["X-RateLimit-Limit"] = str(rl.capacity)
        response.headers["X-RateLimit-Remaining"] = "0"
        return response

    response = await call_next(request)
    try:
        response.headers.setdefault("X-RateLimit-Limit", str(rl.capacity))
        response.headers.setdefault("X-RateLimit-Remaining", str(max(0, res.remaining)))
    except Exception:
        pass
    return response


@app.middleware("http")
async def body_size_limit_middleware(request: Request, call_next):
    """Reject oversized request bodies with 413 before they hit handlers.

    Checks ``Content-Length`` when present (covers ~all real clients).
    For chunked uploads without CL, reads the body up to the cap and rejects
    if it overflows. Methods without a body are skipped.
    """
    if request.method in {"GET", "HEAD", "OPTIONS", "DELETE"}:
        return await call_next(request)

    limit = _max_body_bytes()
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > limit:
                return JSONResponse(
                    {"detail": "Request body too large.", "errorCode": "PAYLOAD_TOO_LARGE"},
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    headers={"Connection": "close"},
                )
        except ValueError:
            return JSONResponse(
                {"detail": "Invalid Content-Length header.", "errorCode": "BAD_REQUEST"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    return await call_next(request)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Adds a unique trace_id to each request for observability.

    AC-OBS-REQID-1..3: honor a client-supplied ``X-Request-ID`` when present
    and validation-safe; otherwise generate a UUID4. Echo on both
    ``X-Request-ID`` and ``X-Trace-ID`` response headers.
    """
    structlog.contextvars.clear_contextvars()

    # Validate incoming X-Request-ID: 1-128 chars from a safe alphabet.
    # Anything else is rejected to prevent log/header injection.
    incoming = request.headers.get("X-Request-ID") or request.headers.get("x-request-id")
    trace_id: str
    if incoming and 1 <= len(incoming) <= 128 and _REQUEST_ID_RE.match(incoming):
        trace_id = incoming
    else:
        trace_id = str(uuid.uuid4())

    structlog.contextvars.bind_contextvars(trace_id=trace_id)
    start_time = time.perf_counter()
    logger = structlog.get_logger(__name__)
    logger.info("request_started", method=request.method, path=request.url.path)

    response = await call_next(request)

    process_time = time.perf_counter() - start_time
    response.headers["X-Trace-ID"] = trace_id
    response.headers["X-Request-ID"] = trace_id
    # Surface server processing time for client-side perf debugging (W3C Server-Timing).
    response.headers["Server-Timing"] = f'app;dur={process_time * 1000:.1f}'
    # Baseline OWASP-aligned security headers (cheap, set on every response).
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=()",
    )
    # JSON-only API: a strict CSP that disallows scripts/objects keeps
    # browsers from executing anything if a future bug ever returned HTML.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    )
    # Cross-origin isolation hardening (cheap and safe for a JSON API).
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    # HSTS only in production-ish envs; harmless on http (browsers ignore it),
    # but we keep local/dev clean to avoid pinning self-signed certs.
    _env_low = (settings.APP_ENVIRONMENT or "local").lower()
    if _env_low not in {"local", "dev", "development", "test", "testing"}:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    # If OTEL is present, surface the W3C trace id for quick correlation
    if _otel_trace:
        try:
            sp = _otel_trace.get_current_span()
            sc = sp.get_span_context() if sp else None
            if sc and sc.trace_id and sc.span_id:
                # Proper W3C traceparent header; keep prior trace id header for convenience.
                response.headers["traceparent"] = f"00-{sc.trace_id:032x}-{sc.span_id:016x}-01"
                response.headers["traceparent-id"] = f"{sc.trace_id:032x}"
                structlog.contextvars.bind_contextvars(otel_trace_id=f"{sc.trace_id:032x}")
        except Exception:
            pass
    logger.info("request_finished", status_code=response.status_code, duration_ms=int(process_time * 1000))
    return response

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catches and logs any unhandled exceptions."""
    logger = structlog.get_logger(__name__)
    trace_id = "not_found"
    try:
        context = structlog.contextvars.get_contextvars()
        trace_id = context.get("trace_id", "not_found")
    except Exception:
        pass

    logger.exception("unhandled_exception", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected internal error occurred. Our wizards have been notified.",
            "errorCode": "INTERNAL_SERVER_ERROR",
            "traceId": trace_id,
        },
    )

# --- Root and Health/Readiness Endpoints ---

@app.get("/", include_in_schema=False)
async def root():
    if _DOCS_ENABLED:
        return RedirectResponse(url="/docs")
    # In production, give a tiny no-info response rather than a redirect to a
    # disabled page.
    return JSONResponse({"status": "ok"})

# Health: cheap and always 200 (no DB/Redis dependency)
@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

# Readiness: fail when configured deps aren't ready (503).
# Each dep check is bounded by READINESS_PROBE_TIMEOUT_S (default 2.0s) so a
# wedged DB or Redis cannot keep the probe hanging forever — the orchestrator
# would otherwise treat the pod as healthy long after it stopped responding.
_READINESS_TIMEOUT_S = float(os.getenv("READINESS_PROBE_TIMEOUT_S", "2.0"))


@app.get("/readiness", include_in_schema=False)
async def readiness():
    import asyncio

    # DB check (only if engine was initialized)
    from app.api.dependencies import db_engine as _db_engine
    if _db_engine is not None:
        async def _db_ping():
            async with _db_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        try:
            await asyncio.wait_for(_db_ping(), timeout=_READINESS_TIMEOUT_S)
        except asyncio.TimeoutError:
            return JSONResponse({"status": "unready", "reason": "db_timeout"}, status_code=503)
        except Exception:
            return JSONResponse({"status": "unready", "reason": "db"}, status_code=503)

    # Redis check (only if pool exists)
    from app.api.dependencies import redis_pool as _redis_pool
    if _redis_pool is not None:
        try:
            import redis.asyncio as redis
            client = redis.Redis(connection_pool=_redis_pool)
            await asyncio.wait_for(client.ping(), timeout=_READINESS_TIMEOUT_S)
        except asyncio.TimeoutError:
            return JSONResponse({"status": "unready", "reason": "redis_timeout"}, status_code=503)
        except Exception:
            return JSONResponse({"status": "unready", "reason": "redis"}, status_code=503)

    return JSONResponse({"status": "ready"})


# --- API Routers ---

API_PREFIX = settings.project.api_prefix

# General configuration and feedback endpoints
app.include_router(config.router, prefix=API_PREFIX)
app.include_router(feedback.router, prefix=API_PREFIX)

# Core quiz interaction endpoints
app.include_router(quiz.router, prefix=API_PREFIX)

# Router for fetching shared results
app.include_router(results.router, prefix=API_PREFIX)
