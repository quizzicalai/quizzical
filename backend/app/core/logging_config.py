# logging_config.py

import logging
import os
import sys
import structlog

def configure_logging():
    """Configures structlog for structured JSON output with verbose local logs."""
    env = os.getenv("APP_ENVIRONMENT", "local").lower()
    level = logging.DEBUG if env == "local" else logging.INFO

    # Make stdlib logging match the level
    logging.basicConfig(level=level, stream=sys.stdout)

    processors = [
        structlog.contextvars.merge_contextvars,  # Merges trace_id from middleware
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
