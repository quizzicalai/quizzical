import logging
import sys
import structlog

def configure_logging():
    """Configures structlog for structured JSON output."""

    processors = [
        structlog.contextvars.merge_contextvars, # Merges trace_id from middleware
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )