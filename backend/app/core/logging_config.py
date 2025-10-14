# logging_config.py

import logging
import os
import sys
from typing import List, Set
import structlog


def _whitelist_processor(allow_loggers: Set[str], allow_events: Set[str]):
    """Drop anything that isn't from an allowed logger OR allowed event."""
    def _proc(logger, method_name, event_dict):
        name = event_dict.get("logger") or ""  # stdlib->structlog bridge puts logger name here
        evt = event_dict.get("event") or ""
        if (name in allow_loggers) or (evt in allow_events):
            return event_dict
        raise structlog.DropEvent
    return _proc


def configure_logging():
    """Configure structured JSON logging with perf-friendly 'LOG_PROFILE=perf'."""
    env = (os.getenv("APP_ENVIRONMENT", "local") or "local").lower()
    is_verbose_env = env in {"local", "dev", "development", "test"}

    # ---- NEW: pick a profile (trace=default in dev; perf otherwise/when set) ----
    profile = (os.getenv("LOG_PROFILE") or ("trace" if is_verbose_env else "perf")).lower()
    perf_mode = profile == "perf"

    # ---------------------------------------------------------------------
    # SDK/library verbosity toggles
    # ---------------------------------------------------------------------
    # Stop SDKs from chatting in perf mode (and generally tone them down).
    os.environ["OPENAI_LOG"] = "info" if perf_mode else os.environ.get("OPENAI_LOG", "debug")
    os.environ["LITELLM_LOG"] = "WARNING" if perf_mode else os.environ.get("LITELLM_LOG", "DEBUG")
    os.environ["LITELLM_DEBUG"] = "0"       # hard off
    os.environ["LITELLM_DISABLE_BACKGROUND_WORKER"] = "1"

    # ---------------------------------------------------------------------
    # Root logger + stdlib->structlog bridge
    # ---------------------------------------------------------------------
    level = logging.INFO if perf_mode else (logging.DEBUG if is_verbose_env else logging.INFO)
    for name in ("openai", "openai._base_client", "httpx"):
        logging.getLogger(name).setLevel(logging.ERROR)
        logging.getLogger(name).propagate = False
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    # Keep the pre-chain short in perf mode (callsite & stacks are expensive)
    callsite_pre_chain: List = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]
    if not perf_mode:
        try:
            callsite_pre_chain.extend([
                structlog.processors.CallsiteParameterAdder({
                    structlog.processors.CallsiteParameter.PATHNAME,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                    structlog.processors.CallsiteParameter.LINENO,
                })
            ])
        except Exception:
            pass

    # Final renderer: omit stack/trace formatting in perf mode
    stdlib_processors = (
        [
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ] if not perf_mode else [
            structlog.processors.JSONRenderer()
        ]
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=callsite_pre_chain,
        processors=stdlib_processors,
    )

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level)
    root.addHandler(handler)
    root.setLevel(level)

    # ---------------------------------------------------------------------
    # structlog configuration
    # ---------------------------------------------------------------------
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    # ---- NEW: drop everything except LLM + agent timing in perf mode ----
    if perf_mode:
        # Let *events* drive what passes through. We’ll only allow the three we care about.
        allowed_loggers = {
            "logging_config",  # so the one-time startup config line prints
        }
        # overridable via env: LOG_ALLOWED_LOGGERS="a,b,c"
        env_loggers = (os.getenv("LOG_ALLOWED_LOGGERS") or "").strip()
        if env_loggers:
            allowed_loggers |= {x.strip() for x in env_loggers.split(",") if x.strip()}

        allowed_events = {
            # exactly what we want in the terminal right now
            "logging_configured",   # one-time config printout at boot
            "llm_prompt",           # prompt payload going out
            "llm_response",         # response coming back (+ duration)
        }
        # overridable via env: LOG_ALLOWED_EVENTS="x,y,z"
        env_events = (os.getenv("LOG_ALLOWED_EVENTS") or "").strip()
        if env_events:
            allowed_events |= {x.strip() for x in env_events.split(",") if x.strip()}

        processors.append(_whitelist_processor(allowed_loggers, allowed_events))
    else:
        # trace mode: keep callsite + exception formatting
        try:
            processors.append(structlog.processors.CallsiteParameterAdder({
                structlog.processors.CallsiteParameter.PATHNAME,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            }))
        except Exception:
            pass
        processors.extend([
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ])

    processors.append(structlog.stdlib.ProcessorFormatter.wrap_for_formatter)

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
        wrapper_class=structlog.stdlib.BoundLogger,
    )

    # ---------------------------------------------------------------------
    # Library logger levels (quiet in perf mode)
    # ---------------------------------------------------------------------
    def set_level(name: str, lvl: int, *, propagate: bool | None = None):
        try:
            lg = logging.getLogger(name)
            lg.setLevel(lvl)
            if propagate is not None:
                lg.propagate = propagate
        except Exception:
            pass

    if perf_mode:
        # Silence noisy libs everywhere
        for n in ["LiteLLM", "litellm", "openai", "httpx", "httpcore", "urllib3",
                  "sqlalchemy.engine", "uvicorn", "uvicorn.error", "uvicorn.access", "asyncio"]:
            set_level(n, logging.WARNING, propagate=False)
        # Only our app stays at INFO
        for n in ["app", "app.api", "app.agent", "app.services"]:
            set_level(n, logging.INFO)
    else:
        # previous behavior
        if is_verbose_env:
            set_level("uvicorn", logging.INFO)
            set_level("uvicorn.error", logging.INFO)
            set_level("uvicorn.access", logging.INFO)
            set_level("httpx", logging.DEBUG)
            set_level("httpcore", logging.DEBUG)
            set_level("urllib3", logging.DEBUG)
            set_level("sqlalchemy.engine", logging.INFO)
            set_level("sqlalchemy.pool", logging.WARNING)
            set_level("asyncio", logging.INFO)
            set_level("litellm", logging.DEBUG)
            set_level("LiteLLM", logging.DEBUG)
            set_level("openai", logging.DEBUG)
            for n in ["app", "app.api", "app.agent", "app.services"]:
                set_level(n, logging.DEBUG)
        else:
            set_level("httpx", logging.WARNING)
            set_level("httpcore", logging.WARNING)
            set_level("urllib3", logging.WARNING)
            set_level("sqlalchemy.engine", logging.WARNING)
            set_level("litellm", logging.INFO)
            set_level("LiteLLM", logging.INFO)
            set_level("openai", logging.INFO)

    structlog.get_logger(__name__).info(
        "logging_configured",
        environment=env,
        profile=profile,
        level=logging.getLevelName(level),
        openai_log=os.getenv("OPENAI_LOG"),
        litellm_log=os.getenv("LITELLM_LOG"),
    )
