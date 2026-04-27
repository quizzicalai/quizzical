"""
API Dependencies

This module defines reusable dependencies for the FastAPI application and also
exposes the core session factory for use in non-request contexts (e.g., agent tools).

The resources (engine, pools) are initialized and closed via the `lifespan`
event handler in `main.py`.
"""
import json
from typing import Any, AsyncGenerator, Optional

import httpx
import redis.asyncio as redis
import structlog
from fastapi import HTTPException, Request
from redis.asyncio import BlockingConnectionPool
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.retry import Retry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings

logger = structlog.get_logger(__name__)

# --- Globals for Lifespan Management ---
db_engine = None
async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
redis_pool: Any = None

# --- Lifespan Functions (to be called from main.py) ---

# FIX: Renamed function to resolve the AttributeError on application startup.
# This now matches the function name called in `main.py`.
def create_db_engine_and_session_maker(db_url: str):
    global db_engine, async_session_factory
    if db_engine is not None:   # idempotent guard
        return db_engine, async_session_factory

    # FIX: Use literal syntax instead of dict() (C408)
    kwargs = {"pool_pre_ping": True}

    if db_url.startswith("sqlite"):
        # Best practice for SQLite in tests (esp. :memory:)
        kwargs.update(
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # IMPORTANT: don't pass pool_size / max_overflow for SQLite
    else:
        # AC-DB-PERF-1..2 — pool sizing comes from settings.database when
        # available, with safe defaults for environments that omit it.
        db_settings = getattr(settings, "database", None)
        pool_size = int(getattr(db_settings, "pool_size", 20)) if db_settings else 20
        max_overflow = int(getattr(db_settings, "max_overflow", 10)) if db_settings else 10
        pool_recycle = int(getattr(db_settings, "pool_recycle_s", 1800)) if db_settings else 1800
        kwargs.update(
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_recycle=pool_recycle,
        )

    db_engine = create_async_engine(db_url, **kwargs)
    async_session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    # Logging added for better observability; no functional change.
    logger.info(
        "DB engine/session factory created",
        pool_size=kwargs.get("pool_size"),
        max_overflow=kwargs.get("max_overflow"),
        pool_recycle_s=kwargs.get("pool_recycle"),
    )
    return db_engine, async_session_factory


def create_redis_pool(redis_url: str):
    """Creates the Redis connection pool."""
    global redis_pool
    # BlockingConnectionPool with bounded waits and per-command timeouts.
    # decode_responses=True is preserved for str I/O downstream.
    redis_pool = BlockingConnectionPool.from_url(
        redis_url,
        decode_responses=True,
        max_connections=50,         # tune per environment
        timeout=10,                 # wait up to 10s for a free connection
        socket_connect_timeout=5,   # connect timeout
        socket_timeout=5,           # per-command read timeout
    )
    # Avoid leaking secrets; only log safe attributes.
    scheme = "rediss" if redis_url.startswith("rediss://") else "redis"
    logger.info(
        "Redis pool created",
        scheme=scheme,
        decode_responses=True,
        max_connections=50,
        timeout=10,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


async def close_db_engine():
    """Closes the SQLAlchemy engine's connections."""
    if db_engine:
        await db_engine.dispose()
        logger.info("Database engine disposed.")


async def close_redis_pool():
    """Close the Redis connection pool and clear the global reference."""
    global redis_pool
    if redis_pool is None:
        return
    try:
        await redis_pool.aclose()
        logger.info("Redis pool disconnected.")
    except Exception:
        logger.warning("Redis pool disconnect failed", exc_info=True)
    finally:
        redis_pool = None


# --- FastAPI Dependencies ---

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a new SQLAlchemy ``AsyncSession``
    for each request.

    On any exception that escapes the yielded scope the session is
    explicitly rolled back before being closed. ``AsyncSession.__aexit__``
    only calls ``close()``, so without this guard a failing endpoint can
    leave the transaction in an indeterminate state (and on PostgreSQL
    can hold row-level locks until the connection is recycled).
    """
    if not async_session_factory:
        logger.error("Database session factory is not initialized.")
        # Surface as 503 so business endpoints fail gracefully when DB is off/unready.
        raise HTTPException(status_code=503, detail="Database not ready")
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            # Best-effort rollback; the actual exception is re-raised so
            # FastAPI's exception handlers still see it.
            try:
                await session.rollback()
            except Exception:
                logger.warning("get_db_session.rollback_failed", exc_info=True)
            raise


def get_redis_client() -> Any:
    """FastAPI dependency that returns a Redis client backed by the shared pool.

    Adds client-level retry/backoff and a health-check interval for transient
    faults. The function is synchronous (no awaits) — FastAPI accepts both
    sync and async dependency callables.
    """
    if not redis_pool:
        logger.error("Redis pool is not initialized.")
        raise HTTPException(status_code=503, detail="Redis not ready")

    client_name = f"quizzical-backend:{settings.APP_ENVIRONMENT}"
    client = redis.Redis(
        connection_pool=redis_pool,
        retry=Retry(ExponentialBackoff(), retries=3),
        retry_on_error=(RedisConnectionError, RedisTimeoutError),
        health_check_interval=30,  # ping idle connections before use
        client_name=client_name,
    )
    logger.debug("Redis client created from pool", client_name=client_name)
    return client


def _validate_turnstile_token(token: Any) -> None:
    """Reject missing / non-string / oversized Turnstile tokens with HTTP 400.

    Real Cloudflare Turnstile tokens are short ASCII strings (~2 KB max per
    docs); 4096 is a safe upper bound that never round-trips attacker-supplied
    megabytes to Cloudflare on our behalf.
    """
    if not token:
        raise HTTPException(status_code=400, detail="Turnstile token not provided.")
    if not isinstance(token, str):
        raise HTTPException(status_code=400, detail="Turnstile token must be a string.")
    if len(token) > 4096:
        raise HTTPException(status_code=400, detail="Turnstile token too large.")


async def verify_turnstile(request: Request) -> bool:
    # Hard bypass when disabled (local/tests)
    if not settings.ENABLE_TURNSTILE:
        return True

    try:
        body = await request.body()
        data: dict[str, Any] = {}
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {}
        # JSON top-level may legitimately be a list/string/number; only dicts
        # can carry a Turnstile token, so coerce others to an empty mapping
        # rather than letting `.get()` raise AttributeError -> HTTP 500.
        if isinstance(parsed, dict):
            data = parsed

        token = data.get("cf-turnstile-response")
        _validate_turnstile_token(token)

        # Local bypass if unconfigured
        env = (settings.APP_ENVIRONMENT or "local").lower()
        secret = (settings.TURNSTILE_SECRET_KEY or "").strip()

        if env in {"local", "dev", "development"} and (not secret or secret == "your_turnstile_secret_key"):
            logger.debug("Local/unconfigured Turnstile: bypassing verification")
            return True

        # Bounded total request budget so an unresponsive Cloudflare
        # endpoint cannot wedge the request worker. ~5s is generous for a
        # token verify call; ~3s connect keeps fast-fail behaviour.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=3.0)
        ) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                json={"secret": secret, "response": token},
            )
            resp.raise_for_status()
            result = resp.json()

        if not result.get("success"):
            logger.warning("Turnstile verification failed", error_codes=result.get("error-codes"))
            raise HTTPException(status_code=401, detail="Invalid Turnstile token.")
        return True

    except HTTPException:
        raise  # Re-raise HTTPExceptions to let FastAPI handle them
    except Exception as e:
        logger.error("Could not verify Turnstile token", error=str(e), exc_info=True)
        # FIX: Use explicit exception chaining (B904)
        raise HTTPException(status_code=500, detail="Could not verify Turnstile token.") from e
