"""
Main FastAPI Application

This module serves as the entry point for the backend application. It initializes
the FastAPI app, configures middleware, includes API routers, and manages the
application's lifespan (startup and shutdown events).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.dependencies import (
    close_db_engine,
    close_redis_pool,
    create_db_engine_and_session_maker,
    create_redis_pool,
)
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's startup and shutdown events.
    This is the recommended way to manage resources like database and
    cache connection pools.
    """
    print("--- Application Starting Up ---")
    # Initialize resources on startup
    create_db_engine_and_session_maker(settings.DATABASE_URL.get_secret_value())
    create_redis_pool(settings.REDIS_URL.get_secret_value())
    print("--- Database and Redis pools initialized ---")

    yield  # The application is now running

    print("--- Application Shutting Down ---")
    # Clean up resources on shutdown
    await close_db_engine()
    await close_redis_pool()
    print("--- Database and Redis pools closed ---")


# Initialize the FastAPI app with the lifespan manager
app = FastAPI(
    title="AI Quiz Generator",
    description="An entertainment-focused web application for generating 'What are you?' style quizzes.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["Health"])
async def health_check():
    """A simple endpoint to confirm the API is running."""
    return {"status": "ok"}


# Include your API routers here
# from app.api.endpoints import quiz, feedback
# app.include_router(quiz.router)
# app.include_router(feedback.router)
