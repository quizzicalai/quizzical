# logging_config.py
import logging
import os
import sys
import random
from typing import Iterable, List, Set, Dict
import structlog

# ============================================================
# Public knobs other modules may import
# ============================================================
# Keep a usable default at import time; configure_logging() will refresh it.
SLOW_MS_LLM = int(os.getenv("LLM_SLOW_MS", "2000"))  # ms


# ============================================================
# Env helpers
# ============================================================
def _csv_env(name: str, default: Iterable[str] = ()):
    raw = os.getenv(name, "")
    if not raw:
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "t", "yes", "y", "on"}


def _level_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").upper()
    try:
        return getattr(logging, raw) if raw else default
    except Exception:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _parse_sample_map(raw: str) -> Dict[str, float]:
    """
    Parse "eventA=0.2,eventB=1.0" -> {"eventA":0.2,"eventB":1.0}
    """
    if not raw:
        return {}
    out: Dict[str, float] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k.strip()] = float(v.strip())
        except Exception:
            continue
    return out


# ============================================================
# Processors (small logs, big signal)
# ============================================================
def _format_exc_on_error(logger, method_name, event_dict):
    """
    Attach formatted exception info only for error/critical logs.
    Keeps success logs tiny.
    """
    lvl = (event_dict.get("level") or "").lower()
    if lvl in {"error", "critical"} or event_dict.get("exc_info"):
        return structlog.processors.format_exc_info(logger, method_name, event_dict)
    return event_dict


def _whitelist_processor(
    allow_loggers: Set[str],
    allow_events: Set[str],
    allow_prefixes: Iterable[str],
):
    """
    Drop anything not from allowed logger/event/prefix,
    EXCEPT: always pass ERROR/CRITICAL from anywhere.
    """
    prefixes = tuple(allow_prefixes)

    def _proc(logger, method_name, event_dict):
        name = event_dict.get("logger") or ""
        evt = event_dict.get("event") or ""
        lvl = (event_dict.get("level") or "").lower()

        # Always keep errors
        if lvl in {"error", "critical"} or event_dict.get("exc_info"):
            return event_dict

        # explicit allows
        if name in allow_loggers or evt in allow_events:
            return event_dict

        # prefix match (e.g., "app.")
        if prefixes and name.startswith(prefixes):
            return event_dict

        raise structlog.DropEvent

    return _proc


def _sampling_processor(sample_default: float, sample_map: Dict[str, float]):
    """
    Probabilistically drop high-frequency INFO/DEBUG logs by event name.
    Errors/critical always pass.
    """
    rnd = random.Random()  # local RNG; no need to seed

    def _proc(logger, method_name, event_dict):
        level = (event_dict.get("level") or "").lower()
        if level in {"error", "critical"} or event_dict.get("exc_info"):
            return event_dict  # never sample errors

        evt = (event_dict.get("event") or "").strip()
        p = sample_map.get(evt, sample_default)
        if p >= 1.0:
            return event_dict
        if p <= 0.0:
            raise structlog.DropEvent
        if rnd.random() <= p:
            return event_dict
        raise structlog.DropEvent

    return _proc


# ============================================================
# Main entrypoint
# ============================================================
def configure_logging():
    """
    Structured JSON logging with two profiles:
      - LOG_PROFILE=perf  : minimal logs (whitelist + sampling); stacks only on errors
      - LOG_PROFILE=trace : verbose logs with callsite + full stacks
    Env knobs (most already in your compose):
      APP_ENVIRONMENT, LOG_PROFILE, LOG_LEVEL_ROOT, LOG_LEVEL_APP, LOG_LEVEL_LIBS
      LOG_ALLOWED_LOGGERS, LOG_ALLOWED_EVENTS, LOG_ALLOWED_LOGGER_PREFIXES
      LOG_SAMPLE_DEFAULT, LOG_SAMPLE_EVENTS
      LLM_SLOW_MS
    """
    global SLOW_MS_LLM  # keep the public knob fresh

    # ---- Base envs
    environment = (os.getenv("APP_ENVIRONMENT") or "local").lower()
    default_profile = "trace" if environment in {"local", "dev", "development", "test"} else "perf"
    profile = (os.getenv("LOG_PROFILE") or default_profile).lower()
    perf_mode = profile == "perf"

    # ---- Top-level levels
    root_level = _level_env("LOG_LEVEL_ROOT", logging.INFO if perf_mode else logging.DEBUG)
    app_level = _level_env("LOG_LEVEL_APP", logging.INFO if perf_mode else logging.DEBUG)
    libs_level = _level_env("LOG_LEVEL_LIBS", logging.WARNING if perf_mode else logging.INFO)

    # ---- Allow lists for perf profile
    allow_loggers = set(_csv_env("LOG_ALLOWED_LOGGERS", default=["logging_config"]))
    allow_events = set(_csv_env("LOG_ALLOWED_EVENTS", default=[
        # keep a few core events visible by default
        "logging_configured",
        "llm.call.start", "llm.call.done", "llm.call.slow", "llm.call.error",
        "llm.stream.start", "llm.stream.done", "llm.stream.error",
        "llm.responses.parse_err", "llm.responses.validation_err",
        "llm.invoke_structured.ok", "llm.invoke_structured.fail",
    ]))
    allow_prefixes = _csv_env("LOG_ALLOWED_LOGGER_PREFIXES", default=["app."])

    # ---- Sampling (perf mode)
    sample_default = float(os.getenv("LOG_SAMPLE_DEFAULT", "1.0"))  # 1.0 = keep all
    sample_map = _parse_sample_map(os.getenv("LOG_SAMPLE_EVENTS", ""))

    # ---- Slow thresholds for other modules
    SLOW_MS_LLM = _int_env("LLM_SLOW_MS", SLOW_MS_LLM)

    # ---- SDK/library specific toggles (safe defaults)
    os.environ["OPENAI_LOG"] = os.getenv("OPENAI_LOG", "info" if perf_mode else "debug")
    os.environ["LITELLM_LOG"] = os.getenv("LITELLM_LOG", "WARNING" if perf_mode else "DEBUG")
    os.environ["LITELLM_DEBUG"] = os.getenv("LITELLM_DEBUG", "0")
    os.environ["LITELLM_DISABLE_BACKGROUND_WORKER"] = os.getenv("LITELLM_DISABLE_BACKGROUND_WORKER", "1")

    # ----------------------------
    # stdlib root + bridge
    # ----------------------------
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    callsite_pre_chain: List = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]
    if not perf_mode:
        try:
            callsite_pre_chain.append(
                structlog.processors.CallsiteParameterAdder({
                    structlog.processors.CallsiteParameter.PATHNAME,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                    structlog.processors.CallsiteParameter.LINENO,
                })
            )
        except Exception:
            pass

    # Perf: render JSON + stack only on error. Trace: always include stack info.
    stdlib_processors = (
        [
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ] if not perf_mode else [
            _format_exc_on_error,              # stacks only for errors
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
    handler.setLevel(root_level)
    root.addHandler(handler)
    root.setLevel(root_level)

    # ----------------------------
    # structlog config
    # ----------------------------
    processors: List = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    if perf_mode:
        # Keep only the signals we care about, then sample the rest.
        processors.append(_whitelist_processor(allow_loggers, allow_events, allow_prefixes))
        processors.append(_sampling_processor(sample_default, sample_map))
    else:
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

    # ----------------------------
    # Library logger tuning
    # ----------------------------
    def set_level(name: str, lvl: int, *, propagate: bool | None = None):
        lg = logging.getLogger(name)
        lg.setLevel(lvl)
        if propagate is not None:
            lg.propagate = propagate

    # Set broad library levels
    for n in [
        "uvicorn", "uvicorn.error", "uvicorn.access",
        "httpx", "httpcore", "urllib3",
        "sqlalchemy.engine", "sqlalchemy.pool",
        "asyncio",
        "litellm", "LiteLLM",
        "openai",
    ]:
        set_level(n, libs_level, propagate=False)

    # App namespaces
    for n in ["app", "app.api", "app.agent", "app.services", "logging_config"]:
        set_level(n, app_level)

    # Final confirmation (tiny and structured)
    structlog.get_logger("logging_config").info(
        "logging_configured",
        environment=environment,
        profile=profile,
        root_level=logging.getLevelName(root_level),
        app_level=logging.getLevelName(app_level),
        libs_level=logging.getLevelName(libs_level),
        allow_loggers=sorted(allow_loggers),
        allow_events=sorted(allow_events),
        allow_prefixes=allow_prefixes,
        sample_default=sample_default,
        sample_events=sample_map,
        slow_ms_llm=SLOW_MS_LLM,
        openai_log=os.getenv("OPENAI_LOG"),
        litellm_log=os.getenv("LITELLM_LOG"),
    )
