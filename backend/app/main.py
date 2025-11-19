"""
Main FastAPI Application
"""
import json
import os
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
app = FastAPI(
    title="AI Quiz Generator",
    description="An entertainment-focused web application for generating 'What are you?' style quizzes.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (safe fallback for local/dev)
def _read_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if not raw.strip():
        # safe local default
        return ["http://localhost:5173", "http://127.0.0.1:5173"]
    try:
        # allow JSON array or comma-separated string
        return json.loads(raw) if raw.strip().startswith("[") else [o.strip() for o in raw.split(",") if o.strip()]
    except Exception:
        return ["http://localhost:5173", "http://127.0.0.1:5173"]

cors_origins = _read_allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Adds a unique trace_id to each request for observability."""
    structlog.contextvars.clear_contextvars()
    trace_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(trace_id=trace_id)
    start_time = time.perf_counter()
    logger = structlog.get_logger(__name__)
    logger.info("request_started", method=request.method, path=request.url.path)

    response = await call_next(request)

    process_time = time.perf_counter() - start_time
    response.headers["X-Trace-ID"] = trace_id
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
    return RedirectResponse(url="/docs")

# Health: cheap and always 200 (no DB/Redis dependency)
@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

# Readiness: fail when configured deps aren't ready (503)
@app.get("/readiness", include_in_schema=False)
async def readiness():
    # DB check (only if engine was initialized)
    from app.api.dependencies import db_engine as _db_engine
    if _db_engine is not None:
        try:
            async with _db_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unready", "reason": "db"}, status_code=503)

    # Redis check (only if pool exists)
    from app.api.dependencies import redis_pool as _redis_pool
    if _redis_pool is not None:
        try:
            import redis.asyncio as redis
            client = redis.Redis(connection_pool=_redis_pool)
            await client.ping()
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
