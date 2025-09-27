# backend/app/api/dependencies.py
"""
API Dependencies

This module defines reusable dependencies for the FastAPI application and also
exposes the core session factory for use in non-request contexts (e.g., agent tools).

The resources (engine, pools) are initialized and closed via the `lifespan`
event handler in `main.py`.
"""
from typing import AsyncGenerator, Optional

import httpx
import redis.asyncio as redis
import structlog
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
# --- Added (Redis best-practice): retry/backoff + explicit blocking pool + typed exceptions
from redis.backoff import ExponentialBackoff
from redis.retry import Retry
from redis.asyncio.connection import BlockingConnectionPool
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from app.core.config import settings

logger = structlog.get_logger(__name__)

# --- Globals for Lifespan Management ---
# These will be initialized in the lifespan event handler in main.py
db_engine = None
async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
redis_pool: Optional[BlockingConnectionPool] = None


# --- Lifespan Functions (to be called from main.py) ---

# FIX: Renamed function to resolve the AttributeError on application startup.
# This now matches the function name called in `main.py`.
def create_db_engine_and_session_maker(db_url: str):
    global db_engine, async_session_factory
    if db_engine is not None:   # idempotent guard
        return

    kwargs = dict(pool_pre_ping=True)

    if db_url.startswith("sqlite"):
        # Best practice for SQLite in tests (esp. :memory:)
        kwargs.update(
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # IMPORTANT: don't pass pool_size / max_overflow for SQLite
    else:
        kwargs.update(pool_size=10, max_overflow=5)

    db_engine = create_async_engine(db_url, **kwargs)
    async_session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    # Logging added for better observability; no functional change.
    logger.info("DB engine/session factory created", pool_size=10, max_overflow=5)


def create_redis_pool(redis_url: str):
    """Creates the Redis connection pool."""
    global redis_pool
    # Changed to BlockingConnectionPool + timeouts to align with best practices;
    # decode_responses=True is preserved to ensure str I/O for JSON parsing downstream.
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
    """Closes the Redis connection pool."""
    if redis_pool:
        # Keep original behavior; no functional change beyond logging.
        await redis_pool.disconnect()
        logger.info("Redis pool disconnected.")


# --- FastAPI Dependencies ---

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a new SQLAlchemy `AsyncSession`
    for each request.
    """
    if not async_session_factory:
        logger.error("Database session factory is not initialized.")
        raise RuntimeError("Database session factory is not initialized.")
    async with async_session_factory() as session:
        yield session


async def get_redis_client() -> redis.Redis:
    """
    FastAPI dependency that provides a Redis client from the connection pool.
    Adds client-level retry/backoff and health checks for transient faults.
    """
    if not redis_pool:
        logger.error("Redis pool is not initialized.")
        raise RuntimeError("Redis pool is not initialized.")

    client = redis.Redis(
        connection_pool=redis_pool,
        retry=Retry(ExponentialBackoff(), retries=3),
        retry_on_error=(RedisConnectionError, RedisTimeoutError),
        health_check_interval=30,  # ping idle connections before use
        client_name=f"quizzical-backend:{settings.APP_ENVIRONMENT}",
    )
    logger.debug("Redis client created from pool", client_name=f"quizzical-backend:{settings.APP_ENVIRONMENT}")
    return client


async def verify_turnstile(request: Request) -> bool:
    # Hard bypass when disabled (local/tests)
    if not settings.ENABLE_TURNSTILE:
        return True

    try:
        body = await request.body()
        import json
        data = {}
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            pass

        token = data.get("cf-turnstile-response")
        if not token:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Turnstile token not provided.")

        # Local bypass if unconfigured
        env = (settings.APP_ENVIRONMENT or "local").lower()
        secret = (settings.TURNSTILE_SECRET_KEY or "").strip()

        if env in {"local", "dev", "development"} and (not secret or secret == "your_turnstile_secret_key"):
            logger.debug("Local/unconfigured Turnstile: bypassing verification")
            return True

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                json={"secret": secret, "response": token},
            )
            resp.raise_for_status()
            result = resp.json()

        if not result.get("success"):
            from fastapi import HTTPException
            logger.warning("Turnstile verification failed", error_codes=result.get("error-codes"))
            raise HTTPException(status_code=401, detail="Invalid Turnstile token.")
        return True

    except HTTPException:
        raise  # Re-raise HTTPExceptions to let FastAPI handle them
    except Exception as e:
        logger.error("Could not verify Turnstile token", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Could not verify Turnstile token.")
