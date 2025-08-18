"""
API Dependencies

This module defines reusable dependencies for the FastAPI application, primarily for
managing connections to external services like the database and Redis cache.

The resources (engine, pools) are initialized and closed via the `lifespan`
event handler in `main.py`.
"""

from typing import AsyncGenerator

import httpx
import redis.asyncio as redis
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# These will be initialized in the lifespan event handler
db_engine = None
db_session_maker = None
redis_pool = None


def create_db_engine_and_session_maker(db_url: str):
    """Creates the SQLAlchemy engine and session factory."""
    global db_engine, db_session_maker
    db_engine = create_async_engine(db_url, pool_size=10, max_overflow=5)
    db_session_maker = async_sessionmaker(
        bind=db_engine,
        expire_on_commit=False,
    )


def create_redis_pool(redis_url: str):
    """Creates the Redis connection pool."""
    global redis_pool
    redis_pool = redis.ConnectionPool.from_url(redis_url, decode_responses=True)


async def close_db_engine():
    """Closes the SQLAlchemy engine's connections."""
    if db_engine:
        await db_engine.dispose()


async def close_redis_pool():
    """Closes the Redis connection pool."""
    if redis_pool:
        await redis_pool.disconnect()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a new SQLAlchemy `AsyncSession`
    for each request.
    """
    if not db_session_maker:
        raise RuntimeError("Database session factory is not initialized.")
    async with db_session_maker() as session:
        yield session


async def get_redis_client() -> redis.Redis:
    """
    FastAPI dependency that provides a Redis client from the connection pool.
    """
    if not redis_pool:
        raise RuntimeError("Redis pool is not initialized.")
    return redis.Redis(connection_pool=redis_pool)


async def verify_turnstile(request: Request) -> bool:
    """
    FastAPI dependency to verify a Cloudflare Turnstile token.

    This should be used on endpoints that need CAPTCHA protection. It expects
    the Turnstile token to be in the request body as 'turnstile_token'.
    """
    try:
        data = await request.json()
        token = data.get("turnstile_token")
        client_ip = request.client.host

        if not token:
            raise HTTPException(status_code=400, detail="Turnstile token not provided.")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                json={
                    "secret": settings.TURNSTILE_SECRET_KEY,
                    "response": token,
                    "remoteip": client_ip,
                },
            )
            response.raise_for_status()
            result = response.json()

        if not result.get("success"):
            raise HTTPException(status_code=401, detail="Invalid Turnstile token.")

        return True

    except HTTPException as e:
        raise e
    except Exception:
        raise HTTPException(status_code=500, detail="Could not verify Turnstile token.")
