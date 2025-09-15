# logging_config.py

import logging
import os
import sys
from typing import List

import structlog


def configure_logging():
    """Configure structured JSON logging with verbose output in local/dev."""
    env = (os.getenv("APP_ENVIRONMENT", "local") or "local").lower()
    is_verbose_env = env in {"local", "dev", "development", "test"}

    # ---------------------------------------------------------------------
    # SDK/library verbosity toggles (safe, local/dev only)
    # ---------------------------------------------------------------------
    if is_verbose_env:
        # OpenAI SDK verbose logs (request/response details)
        os.environ.setdefault("OPENAI_LOG", "debug")
        # LiteLLM internal routing/adapter logs
        os.environ.setdefault("LITELLM_LOG", "DEBUG")

    # ---------------------------------------------------------------------
    # Root logger + stdlib->structlog bridge
    # ---------------------------------------------------------------------
    level = logging.DEBUG if is_verbose_env else logging.INFO

    # Build a ProcessorFormatter that lets stdlib logs render via structlog
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    # Add callsite info in local/dev if available (structlog >=23)
    callsite_pre_chain: List = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]
    try:
        callsite_pre_chain.extend([
            structlog.processors.CallsiteParameterAdder(
                {
                    structlog.processors.CallsiteParameter.PATHNAME,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                    structlog.processors.CallsiteParameter.LINENO,
                }
            )
        ])
    except Exception:
        # Older structlog: skip callsite enrichment
        pass

    # This formatter hands off to the final JSON renderer
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=callsite_pre_chain,
        processors=[
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )

    # Replace root handlers to avoid dupes from basicConfig
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level)
    root.addHandler(handler)
    root.setLevel(level)

    # ---------------------------------------------------------------------
    # structlog configuration (handlers go through stdlib ProcessorFormatter)
    # ---------------------------------------------------------------------
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            timestamper,
            # callsite (best-effort)
            *([
                structlog.processors.CallsiteParameterAdder(
                    {
                        structlog.processors.CallsiteParameter.PATHNAME,
                        structlog.processors.CallsiteParameter.FUNC_NAME,
                        structlog.processors.CallsiteParameter.LINENO,
                    }
                )
            ] if is_verbose_env else []),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
        wrapper_class=structlog.stdlib.BoundLogger,
    )

    # ---------------------------------------------------------------------
    # Library logger levels (turn up the signal in local/dev)
    # ---------------------------------------------------------------------
    def set_level(name: str, lvl: int):
        try:
            logging.getLogger(name).setLevel(lvl)
        except Exception:
            pass

    if is_verbose_env:
        # Web servers / clients
        set_level("uvicorn", logging.INFO)
        set_level("uvicorn.error", logging.INFO)
        set_level("uvicorn.access", logging.INFO)
        set_level("httpx", logging.DEBUG)
        set_level("httpcore", logging.DEBUG)
        set_level("urllib3", logging.DEBUG)

        # Databases / async
        set_level("sqlalchemy.engine", logging.INFO)   # show SQL (without params); bump to DEBUG for more
        set_level("sqlalchemy.pool", logging.WARNING)
        set_level("asyncio", logging.INFO)

        # LLM frameworks / SDKs
        set_level("litellm", logging.DEBUG)
        set_level("openai", logging.DEBUG)

        # Your app modules (examples)
        set_level("app", logging.DEBUG)
        set_level("app.api", logging.DEBUG)
        set_level("app.agent", logging.DEBUG)
        set_level("app.services", logging.DEBUG)
    else:
        # Quieter defaults outside local/dev
        set_level("httpx", logging.WARNING)
        set_level("httpcore", logging.WARNING)
        set_level("urllib3", logging.WARNING)
        set_level("sqlalchemy.engine", logging.WARNING)
        set_level("litellm", logging.INFO)
        set_level("openai", logging.INFO)

    # Final marker
    structlog.get_logger(__name__).info(
        "logging_configured",
        environment=env,
        level=logging.getLevelName(level),
        openai_log=os.getenv("OPENAI_LOG"),
        litellm_log=os.getenv("LITELLM_LOG"),
    )
