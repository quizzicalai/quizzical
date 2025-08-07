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
from app.api.endpoints import assets, config, feedback, quiz, results
from app.core.config import settings
from app.core.logging_config import configure_logging

# --- Lifespan Management ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's startup and shutdown events. This is the
    recommended way to manage resources like database and cache connection pools.
    """
    logger = structlog.get_logger(__name__)
    logger.info("--- Application Starting Up ---")
    
    # Initialize resources on startup
    create_db_engine_and_session_maker(settings.DATABASE_URL.get_secret_value())
    create_redis_pool(settings.REDIS_URL.get_secret_value())
    logger.info("--- Database and Redis pools initialized ---")

    yield  # The application is now running

    logger.info("--- Application Shutting Down ---")
    # Clean up resources on shutdown
    await close_db_engine()
    await close_redis_pool()
    logger.info("--- Database and Redis pools closed ---")


# --- Application Initialization ---

configure_logging()
app = FastAPI(
    title="Quizzical AI",
    description="An entertainment-focused web application for generating 'What are you?' style quizzes using a sophisticated AI agent.",
    version="1.0.0",
    lifespan=lifespan,
)


# --- Middleware Configuration ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """
    Binds a unique trace_id to the structlog context for every request and
    logs the request details, response status, and duration.
    """
    structlog.contextvars.clear_contextvars()
    trace_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(trace_id=trace_id)

    start_time = time.perf_counter()
    logger = structlog.get_logger(__name__)
    logger.info(
        "request_started",
        method=request.method,
        path=request.url.path,
        client_host=request.client.host if request.client else "N/A",
    )

    response = await call_next(request)
    
    process_time = time.perf_counter() - start_time
    response.headers["X-Trace-ID"] = trace_id
    logger.info(
        "request_finished",
        status_code=response.status_code,
        duration_ms=int(process_time * 1000),
    )
    
    return response

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catches any unhandled exception and returns a standardized, safe JSON
    error response, preventing stack traces from being leaked.
    """
    trace_id = structlog.contextvars.get_contextvar("trace_id", "not_set")
    logger.exception(
        "unhandled_exception",
        error=str(exc),
        path=request.url.path,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected internal error occurred. Our wizards have been notified.",
            "errorCode": "INTERNAL_SERVER_ERROR",
            "traceId": trace_id,
        },
    )


# --- Root & Health Endpoints ---

@app.get("/", include_in_schema=False)
async def root():
    """Redirects the root URL to the API documentation."""
    return RedirectResponse(url="/docs")

@app.get("/health", tags=["Health Check"])
async def health_check():
    """A simple endpoint to confirm the API is running."""
    return {"status": "ok"}


# --- API Routers ---

API_PREFIX = "/api/v1"

# Endpoints are organized by their resource type for clarity.
app.include_router(quiz.router, prefix=f"{API_PREFIX}/quiz", tags=["Quiz"])
app.include_router(feedback.router, prefix=f"{API_PREFIX}/feedback", tags=["Feedback"])
app.include_router(assets.router, prefix=f"{API_PREFIX}", tags=["Assets"])
app.include_router(config.router, prefix=API_PREFIX, tags=["Configuration"])

# NEW: Include the router for fetching shareable results.
# This makes the `GET /api/v1/result/{resultId}` endpoint available to the frontend.
app.include_router(results.router, prefix=f"{API_PREFIX}/result", tags=["Results"])