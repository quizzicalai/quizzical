# backend/app/main.py
"""
Main FastAPI Application
"""
import os
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.dependencies import (
    close_db_engine,
    close_redis_pool,
    create_db_engine_and_session_maker,
    create_redis_pool,
)
from app.agent.graph import create_agent_graph, aclose_agent_graph
from app.api.endpoints import assets, config, feedback, quiz, results
from app.core.config import settings
from app.core.logging_config import configure_logging

# --- Lifespan Management ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's startup and shutdown events.
    """
    logger = structlog.get_logger(__name__)
    env = (settings.APP_ENVIRONMENT or "local").lower()
    logger.info("--- Application Starting Up ---", env=env)

    # Initialize database connections
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

        create_db_engine_and_session_maker(db_url)  # if your helper reads settings, drop the arg
        logger.info("Database engine initialized", db_url=db_url if env in {"local","dev","development"} else "hidden")
    except Exception as e:
        logger.error("Failed to initialize database", error=str(e), exc_info=True)
        if env not in {"local", "dev", "development"}:
            raise

    # Initialize Redis pool
    try:
        redis_url = getattr(settings, "REDIS_URL", None) or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        create_redis_pool(redis_url)
        logger.info("Redis pool initialized", redis_url=redis_url if env in {"local","dev","development"} else "hidden")
    except Exception as e:
        logger.error("Failed to initialize Redis pool", error=str(e), exc_info=True)
        if env not in {"local", "dev", "development"}:
            raise

    # Compile the agent graph
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

    try:
        yield
    finally:
        # --- Shutdown Logic ---
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


# --- Application Initialization and Middleware ---

configure_logging()
app = FastAPI(
    title="AI Quiz Generator",
    description="An entertainment-focused web application for generating 'What are you?' style quizzes.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (safe fallback for local/dev)
cors_origins = getattr(getattr(settings, "cors", None), "origins", None) or ["*"]
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
    logger.info("request_finished", status_code=response.status_code, duration_ms=int(process_time * 1000))
    return response

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catches and logs any unhandled exceptions."""
    logger = structlog.get_logger(__name__)
    # Use structlog contextvars to surface trace_id
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

# --- Root and Health Endpoints ---

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}


# --- API Routers ---

API_PREFIX = settings.project.api_prefix

# General configuration and feedback endpoints
app.include_router(config.router, prefix=API_PREFIX)
app.include_router(feedback.router, prefix=API_PREFIX)

# Core quiz interaction endpoints
app.include_router(quiz.router, prefix=API_PREFIX)

# Router for fetching shared results
app.include_router(results.router, prefix=API_PREFIX)

# Assets (like character images) can remain top-level
app.include_router(assets.router, prefix=API_PREFIX)
