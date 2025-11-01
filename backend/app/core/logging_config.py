# backend/app/core/logging_config.py
from __future__ import annotations

import logging
import os
import sys
import random
from collections.abc import Mapping, Sequence
from logging.handlers import RotatingFileHandler
from typing import Iterable, List, Set, Dict, Any

import structlog

# ============================================================
# Public knobs other modules may import
# ============================================================
SLOW_MS_LLM = int(os.getenv("LLM_SLOW_MS", "2000"))  # ms

# ============================================================
# Env helpers
# ============================================================
def _csv_env(name: str, default: Iterable[str] = ()) -> List[str]:
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
# Redaction (defense-in-depth)
# ============================================================
SENSITIVE_KEYS = {
    "authorization",
    "api-key",
    "apikey",
    "openai-api-key",
    "cf-turnstile-response",
    "password",
    "secret",
    "token",
}
_REDACTION = "******"


def _redact_in_str(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    s = v
    for k in SENSITIVE_KEYS:
        # redact both literal key mentions and upper-case variants
        s = s.replace(k, _REDACTION).replace(k.upper(), _REDACTION)
    return s


def _redact_in_mapping(m: Mapping) -> dict:
    """Return a shallow-redacted copy of a mapping without stringifying it."""
    out = {}
    for k, v in m.items():
        kl = str(k).lower()
        if kl in SENSITIVE_KEYS:
            out[k] = _REDACTION
            continue
        if isinstance(v, Mapping):
            out[k] = _redact_in_mapping(v)
        elif isinstance(v, Sequence) and not isinstance(v, (str, bytes, bytearray)):
            out[k] = [
                _redact_in_mapping(x) if isinstance(x, Mapping) else _redact_in_str(x) for x in v
            ]
        else:
            out[k] = _redact_in_str(v)
    return out


def redact_processor(logger, method_name, event_dict):
    """
    Structlog processor that redacts sensitive keys/values while keeping the
    event as a dict (so ProcessorFormatter can operate). This prevents
    "'str' object has no attribute 'copy'" crashes.
    """
    if isinstance(event_dict, Mapping):
        return _redact_in_mapping(event_dict)
    return event_dict


class RedactFilter(logging.Filter):
    """
    Best-effort redaction for *foreign* stdlib string messages.
    Critically: does NOT coerce structlog dict events to strings.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Leave dict messages (structlog) alone; they'll be handled by redact_processor.
            if isinstance(record.msg, Mapping):
                return True
            # Scrub plain string messages.
            if isinstance(record.msg, str):
                record.msg = _redact_in_str(record.msg)
            # Scrub %-format args if present.
            if isinstance(record.args, tuple) and record.args:
                record.args = tuple(_redact_in_str(a) for a in record.args)
        except Exception:
            pass
        return True


# ============================================================
# Helpers
# ============================================================
def _as_struct_logger(logger_like: Any):
    """
    Ensure we have a structlog logger we can safely attach key/value fields to.
    Accepts either a structlog logger or a stdlib Logger.
    """
    if isinstance(logger_like, logging.Logger):
        return structlog.get_logger(logger_like.name)
    return logger_like if logger_like is not None else structlog.get_logger("logging_config")


# ============================================================
# OTEL bootstrap (optional; no-op if not configured)
# ============================================================
def _configure_azure_otel_if_available(logger: Any) -> bool:
    """
    If AZURE_MONITOR_CONNECTION_STRING (or APPLICATIONINSIGHTS_CONNECTION_STRING)
    is set, initialize Azure Monitor OTel. Returns True when configured; False otherwise.
    Also tries to disable OTel's logging exporter to avoid noisy log events.
    """
    lg = _as_struct_logger(logger)
    conn = os.getenv("AZURE_MONITOR_CONNECTION_STRING") or os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        return False
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor  # type: ignore
        # Prefer disabling the logging exporter if supported by the installed version.
        try:
            configure_azure_monitor(logging_exporter_enabled=False)  # newer versions
            lg.info("otel_configured", exporter="azure_monitor", logging_exporter=False, enabled=True)
        except TypeError:
            configure_azure_monitor()  # fallback
            lg.info("otel_configured", exporter="azure_monitor", logging_exporter="default", enabled=True)
        return True
    except Exception as e:
        lg.warning("otel_config_failed", error=str(e), exc_info=True)
        return False


# ============================================================
# Main entrypoint
# ============================================================
def configure_logging():
    """
    Structured JSON logging with two profiles:
      - LOG_PROFILE=perf  : minimal logs (whitelist + sampling); stacks only on errors
      - LOG_PROFILE=trace : verbose logs with callsite + full stacks
    Env knobs:
      APP_ENVIRONMENT, LOG_PROFILE, LOG_LEVEL_ROOT, LOG_LEVEL_APP, LOG_LEVEL_LIBS
      LOG_ALLOWED_LOGGERS, LOG_ALLOWED_EVENTS, LOG_ALLOWED_LOGGER_PREFIXES
      LOG_SAMPLE_DEFAULT, LOG_SAMPLE_EVENTS
      LOG_TO_FILE (true for local/dev, false in Azure)
      AZURE_MONITOR_CONNECTION_STRING/APPLICATIONINSIGHTS_CONNECTION_STRING
      LLM_SLOW_MS
    """
    global SLOW_MS_LLM

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
    allow_events = set(
        _csv_env(
            "LOG_ALLOWED_EVENTS",
            default=[
                "logging_configured",
                "llm.call.start",
                "llm.call.done",
                "llm.call.slow",
                "llm.call.error",
                "llm.stream.start",
                "llm.stream.done",
                "llm.stream.error",
                "llm.responses.parse_err",
                "llm.responses.validation_err",
                "llm.invoke_structured.ok",
                "llm.invoke_structured.fail",
            ],
        )
    )
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
                structlog.processors.CallsiteParameterAdder(
                    {
                        structlog.processors.CallsiteParameter.PATHNAME,
                        structlog.processors.CallsiteParameter.FUNC_NAME,
                        structlog.processors.CallsiteParameter.LINENO,
                    }
                )
            )
        except Exception:
            pass

    stdlib_processors = (
        [
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
        if not perf_mode
        else [
            _format_exc_on_error,
            structlog.processors.JSONRenderer(),
        ]
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=callsite_pre_chain,
        processors=stdlib_processors,
    )

    root = logging.getLogger()
    # Remove any pre-existing handlers to avoid duplicates
    for h in list(root.handlers):
        root.removeHandler(h)

    # 1) CONSOLE handler (stdout -> ACA logs)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(root_level)
    console_handler.addFilter(RedactFilter())
    root.addHandler(console_handler)

    # 2) Optional FILE handler (local dev only by default)
    default_log_to_file = environment in {"local", "dev", "development", "test"}
    log_to_file = _bool_env("LOG_TO_FILE", default_log_to_file)

    log_file_path = None
    if log_to_file:
        log_dir = "/logs"
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = os.path.join(log_dir, "app.log")
            file_handler = RotatingFileHandler(
                log_file_path, maxBytes=10 * 1024 * 1024, backupCount=5
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(root_level)
            file_handler.addFilter(RedactFilter())
            root.addHandler(file_handler)
        except Exception as e:
            # IMPORTANT: stdlib-safe formatting here (no structlog K/V).
            root.warning("file_logging_disabled: %s (dir=%s)", str(e), log_dir, exc_info=True)

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
        processors.append(_whitelist_processor(allow_loggers, allow_events, allow_prefixes))
        processors.append(_sampling_processor(sample_default, sample_map))
    else:
        try:
            processors.append(
                structlog.processors.CallsiteParameterAdder(
                    {
                        structlog.processors.CallsiteParameter.PATHNAME,
                        structlog.processors.CallsiteParameter.FUNC_NAME,
                        structlog.processors.CallsiteParameter.LINENO,
                    }
                )
            )
        except Exception:
            pass
        processors.extend(
            [
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
            ]
        )

    # Redact *before* handing off to ProcessorFormatter
    processors.append(redact_processor)

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

    # Quiet noisy libs; keep flexible via LOG_LEVEL_LIBS
    for n in [
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "httpx",
        "httpcore",
        "urllib3",
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "asyncio",
        "litellm",
        "LiteLLM",
        "openai",
        "azure",
        "azure.core.pipeline.policies.http_logging_policy",
    ]:
        set_level(n, libs_level, propagate=False)

    # Silence OpenTelemetry warnings (_FixedFindCallerLogger etc.)
    for n in [
        "opentelemetry",
        "opentelemetry.attributes",
        "opentelemetry.sdk",
        "opentelemetry.instrumentation",
    ]:
        set_level(n, logging.ERROR, propagate=False)

    for n in ["app", "app.api", "app.agent", "app.services", "logging_config"]:
        set_level(n, app_level)

    lg = structlog.get_logger("logging_config")
    lg.info(
        "logging_configured",
        environment=environment,
        profile=profile,
        root_level=logging.getLevelName(root_level),
        app_level=logging.getLevelName(app_level),
        libs_level=logging.getLevelName(libs_level),
        log_file=log_file_path,
        slow_ms_llm=SLOW_MS_LLM,
        log_to_file=log_to_file,
    )

    # ---- Enable Azure Monitor via OTEL if available (use structlog logger)
    _configure_azure_otel_if_available(lg)
