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
from app.api.endpoints import assets, feedback, quiz, config
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

# Configure logging before the app is created to ensure logs are structured.
configure_logging()

# Initialize the FastAPI app with the lifespan manager and metadata.
app = FastAPI(
    title="AI Quiz Generator",
    description="An entertainment-focused web application for generating 'What are you?' style quizzes.",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Middleware Configuration ---

# 1. CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 2. Logging & Observability Middleware
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
        client=f"{request.client.host}:{request.client.port}",
        user_agent=request.headers.get("user-agent", "N/A"),
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

# 3. Global Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catches any unhandled exception and returns a standardized, safe JSON
    error response, preventing stack traces from being leaked.
    """
    trace_id = structlog.contextvars.get_contextvar("trace_id")
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

# --- Root Endpoint ---

@app.get("/", include_in_schema=False)
async def root():
    """Redirects the root URL to the API documentation."""
    return RedirectResponse(url="/docs")

@app.get("/health", tags=["Health"])
async def health_check():
    """A simple endpoint to confirm the API is running."""
    return {"status": "ok"}


# --- Include API Routers ---
app.include_router(quiz.router, prefix="/api/v1", tags=["Quiz"])
app.include_router(feedback.router, prefix="/api/v1", tags=["Feedback"])
app.include_router(assets.router, prefix="/api/v1", tags=["Assets"])
app.include_router(config.router, prefix="/api/v1", tags=["Configuration"])
