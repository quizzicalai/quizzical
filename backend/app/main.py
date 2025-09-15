# backend/app/main.py
"""
Main FastAPI Application

This module serves as the entry point for the backend application. It initializes
the FastAPI app, configures middleware, includes API routers, and manages the
application's lifespan (startup and shutdown events).
"""
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
# Import the graph factory and closer (no behavior change to the graph itself).
from app.agent.graph import create_agent_graph, aclose_agent_graph
from app.api.endpoints import assets, config, feedback, quiz, results
from app.core.config import settings
from app.core.logging_config import configure_logging

# --- Lifespan Management ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's startup and shutdown events.
    This is the ideal place for initializing resources like database connections,
    and in our case, the compiled agent graph.
    """
    logger = structlog.get_logger(__name__)
    logger.info(
        "--- Application Starting Up ---",
        env=(settings.APP_ENVIRONMENT or "local"),
    )

    # Initialize database and Redis connections first.
    try:
        create_db_engine_and_session_maker(settings.DATABASE_URL)
        create_redis_pool(settings.REDIS_URL)
        logger.info("--- Database and Redis pools initialized ---")
    except Exception as e:
        logger.error("Failed to initialize DB/Redis pools", error=str(e), exc_info=True)
        # In non-local envs, fail fast if infra can't start
        if (settings.APP_ENVIRONMENT or "local").lower() not in {"local", "dev", "development"}:
            raise

    # Compile the agent graph and attach it to the app's state.
    try:
        agent_graph = await create_agent_graph()
        app.state.agent_graph = agent_graph
        # Also expose the checkpointer (if attached by the graph factory) for introspection.
        app.state.checkpointer = getattr(agent_graph, "_async_checkpointer", None)
        logger.info(
            "--- Agent graph compiled and ready ---",
            agent_graph_id=id(agent_graph),
            checkpointer_class=type(app.state.checkpointer).__name__ if app.state.checkpointer else None,
        )
    except Exception as e:
        logger.error(
            "Failed to create agent graph",
            error=str(e),
            exc_info=True,
        )
        # For non-local environments you may choose to raise to fail fast.
        if (settings.APP_ENVIRONMENT or "local").lower() not in {"local", "dev", "development"}:
            raise

    try:
        yield
    finally:
        # --- Shutdown Logic ---
        logger.info("--- Application Shutting Down ---")
        # Close the LangGraph async checkpointer cleanly (if present).
        try:
            graph = getattr(app.state, "agent_graph", None)
            if graph is not None:
                await aclose_agent_graph(graph)
                logger.info("Agent graph resources closed")
        except Exception as e:
            logger.warning("Failed to close agent graph resources", error=str(e), exc_info=True)

        # Close DB/Redis after the graph so any final writes can complete first.
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors["origins"],
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
    trace_id = structlog.contextvars.get_contextvar("trace_id", "not_found")
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
