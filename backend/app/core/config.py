# backend/app/core/config.py
"""
Settings loader (Azure-first with YAML fallback) + Secrets (Key Vault first, then .env)

Non-secret config load order:
  1) Azure App Configuration (blob key `quizzical:appsettings` or hierarchical keys prefixed `quizzical:`)
  2) Local YAML at backend/appconfig.local.yaml (override path with APP_CONFIG_LOCAL_PATH)
  3) Hardcoded defaults in this file (and DEFAULT_PROMPTS in prompts.py for prompts)

Secret config (keys/tokens like Turnstile) load order:
  A) Azure Key Vault (if configured)
  B) Local .env (and process environment via os.getenv)

This module exposes a single, cached `settings` object used across the app.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import json
import os
import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_core.core_schema import ValidationInfo

# ---------- logging (graceful if structlog missing) ----------
try:
    import structlog

    log = structlog.get_logger(__name__)
except Exception:  # pragma: no cover
    class _Noop:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
    log = _Noop()  # type: ignore


# =========================
# Pydantic settings shapes
# =========================

class ModelConfig(BaseModel):
    model: str
    temperature: float = 0.3
    max_output_tokens: int = 1024
    timeout_s: int = 20
    json_output: bool = True


class PromptConfig(BaseModel):
    system_prompt: str = Field(min_length=1)
    user_prompt_template: str = Field(min_length=1)


class AppInfo(BaseModel):
    name: str = "Quizzical"
    environment: str = "local"
    debug: bool = True


class FeatureFlags(BaseModel):
    flow_mode: str = "agent"   # "local" | "agent"


class CorsConfig(BaseModel):
    origins: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


class ProjectConfig(BaseModel):
    api_prefix: str = "/api"


class QuizConfig(BaseModel):
    min_characters: int = 4
    max_characters: int = 6
    baseline_questions_n: int = 5
    max_options_m: int = 4
    max_total_questions: int = 20
    # Time budgets used by endpoints/quiz.py
    first_step_timeout_s: float = 30.0
    stream_budget_s: float = 30.0
    # NEW: allows bounded parallelism for character generation; None → auto
    character_concurrency: Optional[int] = None

    @field_validator("max_characters")
    @classmethod
    def _bounds(cls, v: int, info: ValidationInfo) -> int:
        if "min_characters" in info.data and v < info.data["min_characters"]:
            raise ValueError("max_characters must be >= min_characters")
        return v

    @field_validator("character_concurrency")
    @classmethod
    def _cc_valid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("character_concurrency must be >= 1 or null")
        return v


class AgentConfig(BaseModel):
    max_retries: int = 3


class LLMSettings(BaseModel):
    """
    Global LLM-level knobs (separate from per-tool configs).
    """
    # NEW: used by graph.py for asyncio.wait_for in parallel profile generation
    per_call_timeout_s: int = 30


# -------- Secrets (keys/tokens) --------
class TurnstileConfig(BaseModel):
    site_key: Optional[str] = None
    secret_key: Optional[str] = None


class SecurityConfig(BaseModel):
    # Global toggle (e.g., ENABLE_TURNSTILE); default True for prod, can be disabled in local/dev via .env
    enabled: bool = True
    turnstile: TurnstileConfig = TurnstileConfig()


class Settings(BaseModel):
    app: AppInfo = AppInfo()
    feature_flags: FeatureFlags = FeatureFlags()
    cors: CorsConfig = CorsConfig()
    project: ProjectConfig = ProjectConfig()
    quiz: QuizConfig = QuizConfig()
    agent: AgentConfig = AgentConfig()

    # NEW: keep global LLM knobs alongside per-tool configs
    llm: LLMSettings = LLMSettings()
    llm_tools: Dict[str, ModelConfig] = Field(default_factory=dict)
    llm_prompts: Dict[str, PromptConfig] = Field(default_factory=dict)

    security: SecurityConfig = SecurityConfig()

    # -----------------------------
    # Compatibility / convenience
    # -----------------------------
    @property
    def APP_ENVIRONMENT(self) -> str:
        """Backwards-compatible alias used elsewhere in the codebase."""
        try:
            return self.app.environment
        except Exception:
            return "local"

    @property
    def REDIS_URL(self) -> str:
        """
        Backwards-compatible alias used by graph/checkpointer code.
        Environment-first (prod-friendly), with a safe local default.
        """
        url = os.getenv("REDIS_URL")
        if url:
            return url
        host = os.getenv("REDIS_HOST") or os.getenv("REDIS__HOST") or "localhost"
        port = os.getenv("REDIS_PORT") or os.getenv("REDIS__PORT") or "6379"
        db = os.getenv("REDIS_DB") or os.getenv("REDIS__DB") or "0"
        return f"redis://{host}:{port}/{db}"

    @property
    def DATABASE_URL(self) -> Optional[str]:
        """
        Helper to build a DB URL from common env pieces if DATABASE_URL is not set.
        """
        if os.getenv("DATABASE_URL"):
            return os.getenv("DATABASE_URL")
        user = os.getenv("DATABASE_USER") or os.getenv("DATABASE__USER") or "postgres"
        pwd = os.getenv("DATABASE_PASSWORD") or os.getenv("DATABASE__PASSWORD") or "postgres"
        host = os.getenv("DATABASE_HOST") or os.getenv("DATABASE__HOST") or "localhost"
        port = os.getenv("DATABASE_PORT") or os.getenv("DATABASE__PORT") or "5432"
        name = os.getenv("DATABASE_DB_NAME") or os.getenv("DATABASE__DB_NAME") or "quiz"
        return f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{name}"

    # --- Back-compat aliases (to avoid breaking legacy code paths) ---
    @property
    def ENABLE_TURNSTILE(self) -> bool:
        """
        Backwards-compatible alias for legacy code paths.
        Mirrors settings.security.enabled.
        """
        try:
            return bool(self.security.enabled)
        except Exception:
            return True  # default to True in case of partial config

    @property
    def TURNSTILE_SITE_KEY(self) -> Optional[str]:
        """
        Backwards-compatible alias for legacy code that expects a top-level constant.
        Mirrors settings.security.turnstile.site_key.
        """
        try:
            return self.security.turnstile.site_key
        except Exception:
            return None

    @property
    def TURNSTILE_SECRET_KEY(self) -> Optional[str]:
        """
        Backwards-compatible alias for legacy code that expects a top-level constant.
        Mirrors settings.security.turnstile.secret_key.
        """
        try:
            return self.security.turnstile.secret_key
        except Exception:
            return None


# ===========
# Defaults
# ===========

_DEFAULTS: Dict[str, Any] = {
    "quizzical": {
        "app": {"name": "Quizzical", "environment": "local", "debug": True},
        "feature_flags": {"flow_mode": "agent"},
        "cors": {"origins": ["http://localhost:5173", "http://127.0.0.1:5173"]},
        "project": {"api_prefix": "/api"},
        "quiz": {
            "min_characters": 4,
            "max_characters": 6,
            "baseline_questions_n": 5,
            "max_options_m": 4,
            "max_total_questions": 20,
            "first_step_timeout_s": 30.0,
            "stream_budget_s": 30.0,
            # NEW default: let runtime auto-pick based on #archetypes (bounded in graph)
            "character_concurrency": None,
        },
        "agent": {"max_retries": 3},
        "llm": {
            # NEW global knob used by parallel character generation
            "per_call_timeout_s": 30,
            "tools": {
                "initial_planner": {"model": "gpt-4o-mini", "temperature": 0.2, "max_output_tokens": 800, "timeout_s": 18, "json_output": True},
                "character_list_generator": {"model": "gpt-4o-mini", "temperature": 0.3, "max_output_tokens": 1200, "timeout_s": 18, "json_output": True},
                "synopsis_generator": {"model": "gpt-4o-mini", "temperature": 0.2, "max_output_tokens": 600, "timeout_s": 18, "json_output": True},
                "profile_writer": {"model": "gpt-4o-mini", "temperature": 0.4, "max_output_tokens": 1600, "timeout_s": 18, "json_output": True},
                "profile_improver": {"model": "gpt-4o-mini", "temperature": 0.3, "max_output_tokens": 1600, "timeout_s": 18, "json_output": True},
                "character_selector": {"model": "gpt-4o-mini", "temperature": 0.2, "max_output_tokens": 1000, "timeout_s": 18, "json_output": True},
                "question_generator": {"model": "gpt-4o-mini", "temperature": 0.4, "max_output_tokens": 1200, "timeout_s": 18, "json_output": True},
                "next_question_generator": {"model": "gpt-4o-mini", "temperature": 0.4, "max_output_tokens": 800, "timeout_s": 18, "json_output": True},
                "final_profile_writer": {"model": "gpt-4o-mini", "temperature": 0.3, "max_output_tokens": 1000, "timeout_s": 18, "json_output": True},
                "safety_checker": {"model": "gpt-4o-mini", "temperature": 0.0, "max_output_tokens": 200, "timeout_s": 10, "json_output": True},
                "error_analyzer": {"model": "gpt-4o-mini", "temperature": 0.2, "max_output_tokens": 600, "timeout_s": 12, "json_output": True},
                "failure_explainer": {"model": "gpt-4o-mini", "temperature": 0.2, "max_output_tokens": 500, "timeout_s": 12, "json_output": True},
                "image_prompt_enhancer": {"model": "gpt-4o-mini", "temperature": 0.6, "max_output_tokens": 600, "timeout_s": 18, "json_output": True},
            },
            "prompts": {}  # defaults live in agent/prompts.py
        },
        # security defaults are intentionally minimal; secrets overlay later
    }
}


# =============================
# Azure App Configuration load
# =============================

def _load_from_azure_app_config() -> Optional[Dict[str, Any]]:
    """
    Supports:
      - Single blob keys: "quizzical:appsettings" or "quizzical:settings"
      - Hierarchical keys beginning with "quizzical:"
    Returns nested dict (same shape as appconfig.local.yaml) or None.
    """
    endpoint = os.getenv("APP_CONFIG_ENDPOINT")
    conn_str = os.getenv("APP_CONFIG_CONNECTION_STRING")
    label = os.getenv("APP_CONFIG_LABEL", None)

    if not (endpoint or conn_str):
        log.debug("Azure App Config not configured.")
        return None

    try:
        # Lazy import to avoid hard dependency when not used
        from azure.appconfiguration import AzureAppConfigurationClient
        from azure.identity import DefaultAzureCredential

        if conn_str:
            client = AzureAppConfigurationClient.from_connection_string(conn_str)
        else:
            credential = DefaultAzureCredential()
            client = AzureAppConfigurationClient(base_url=endpoint, credential=credential)

        def _get_value(key: str) -> Optional[str]:
            try:
                cs = client.get_configuration_setting(key=key, label=label)
                return cs.value
            except Exception:
                return None

        # 1) Try blob keys first
        for k in ("quizzical:appsettings", "quizzical:settings"):
            val = _get_value(k)
            if val:
                for loader in (json.loads, yaml.safe_load):
                    try:
                        data = loader(val) or {}
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        pass

        # 2) Reconstruct from hierarchical keys
        it = client.list_configuration_settings(key_filter="quizzical:*", label_filter=label)
        data: Dict[str, Any] = {}
        for cs in it:
            key = getattr(cs, "key", "")
            val = getattr(cs, "value", None)
            if not key or not key.startswith("quizzical:") or val is None:
                continue

            try:
                parsed: Any = json.loads(val)
            except Exception:
                try:
                    parsed = yaml.safe_load(val)
                except Exception:
                    parsed = val

            parts = key.split(":")
            cur = data
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = parsed

        return data or None
    except Exception as e:
        log.warning("Azure App Config unavailable or unauthorized; skipping.", error=str(e))
        return None


# ===================
# Local YAML fallback
# ===================

def _load_from_yaml() -> Optional[Dict[str, Any]]:
    # Default location: backend/appconfig.local.yaml (sibling to .env)
    backend_dir = Path(__file__).resolve().parents[2]
    default_path = backend_dir / "appconfig.local.yaml"
    path_str = os.getenv("APP_CONFIG_LOCAL_PATH", str(default_path))
    path = Path(path_str)
    if not path.exists():
        log.debug("Local YAML not found", path=str(path))
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ValueError("YAML root must be a mapping")
            log.info("Loaded local YAML config", path=str(path))
            return data
    except Exception as e:
        log.warning("Failed to read local YAML; ignoring.", path=str(path), error=str(e))
        return None


# =======================
# Secrets: Key Vault / .env
# =======================

def _maybe_load_dotenv() -> None:
    """
    Try to load a .env file from common locations to ensure os.getenv works.
    Safe no-op if python-dotenv isn't installed.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    candidates: List[Path] = []
    backend_dir = Path(__file__).resolve().parents[2]
    candidates.append(backend_dir / ".env")        # backend/.env
    candidates.append(backend_dir.parent / ".env") # repo root .env
    env_path = os.getenv("ENV_FILE")
    if env_path:
        candidates.insert(0, Path(env_path))

    for p in candidates:
        try:
            if p.exists():
                load_dotenv(dotenv_path=str(p), override=False)
                log.info("Loaded .env file", path=str(p))
                break
        except Exception:
            continue


def _load_secrets_from_key_vault() -> Optional[Dict[str, Any]]:
    """
    Load secret values from Azure Key Vault if configured.
    Accepts env aliases:
      - KEYVAULT_URI, KEY_VAULT_URI, AZURE_KEY_VAULT_ENDPOINT
      - KEY_VAULT_NAME  (constructs https://{name}.vault.azure.net)
    Returns a nested dict under {"quizzical": {"security": {...}}} or None.
    """
    uri = (
        os.getenv("KEYVAULT_URI")
        or os.getenv("KEY_VAULT_URI")
        or os.getenv("AZURE_KEY_VAULT_ENDPOINT")
    )
    name = os.getenv("KEY_VAULT_NAME")
    if not uri and name:
        uri = f"https://{name}.vault.azure.net"
    if not uri:
        log.debug("Key Vault not configured.")
        return None

    try:
        from azure.identity import DefaultAzureCredential  # type: ignore
        from azure.keyvault.secrets import SecretClient  # type: ignore

        client = SecretClient(vault_url=uri, credential=DefaultAzureCredential())

        def _get(name: str) -> Optional[str]:
            try:
                s = client.get_secret(name)
                return s.value
            except Exception:
                return None

        # Accepted secret names
        turnstile_site = _get("TURNSTILE_SITE_KEY") or _get("TurnstileSiteKey")
        turnstile_secret = _get("TURNSTILE_SECRET_KEY") or _get("TurnstileSecretKey")

        sec: Dict[str, Any] = {"quizzical": {"security": {"turnstile": {}}}}
        if turnstile_site:
            sec["quizzical"]["security"]["turnstile"]["site_key"] = turnstile_site
        if turnstile_secret:
            sec["quizzical"]["security"]["turnstile"]["secret_key"] = turnstile_secret

        if not sec["quizzical"]["security"]["turnstile"]:
            return None

        log.info("Loaded secrets from Azure Key Vault")
        return sec
    except Exception as e:
        log.warning("Key Vault not available; skipping.", error=str(e))
        return None


def _load_secrets_from_env() -> Dict[str, Any]:
    """
    Load secrets from .env / process environment.
    .env is proactively loaded so os.getenv works reliably in local dev.
    """
    _maybe_load_dotenv()

    # Turnstile keys
    turnstile_site = os.getenv("TURNSTILE_SITE_KEY")
    turnstile_secret = os.getenv("TURNSTILE_SECRET_KEY")

    # Global security toggle (e.g., ENABLE_TURNSTILE=False)
    enabled_env = os.getenv("ENABLE_TURNSTILE")

    sec: Dict[str, Any] = {"quizzical": {"security": {}}}

    if enabled_env is not None:
        sec["quizzical"]["security"]["enabled"] = str(enabled_env).strip().lower() not in {"0", "false", "no"}

    sec.setdefault("quizzical", {}).setdefault("security", {}).setdefault("turnstile", {})
    if turnstile_site:
        sec["quizzical"]["security"]["turnstile"]["site_key"] = turnstile_site
    if turnstile_secret:
        sec["quizzical"]["security"]["turnstile"]["secret_key"] = turnstile_secret

    if sec["quizzical"]["security"].get("turnstile") or ("enabled" in sec["quizzical"]["security"]):
        log.info("Loaded secrets from environment/.env")
    else:
        log.debug("No Turnstile secrets found in environment/.env")
    return sec


# =======================
# Normalization utilities
# =======================

def _ensure_quizzical_root(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Azure hierarchical keys include 'quizzical' at root; blob may already be rooted or not."""
    if "quizzical" in raw and isinstance(raw["quizzical"], dict):
        return raw
    keys = {"app", "feature_flags", "quiz", "agent", "llm", "llm_tools", "llm_prompts", "cors", "project", "security"}
    if any(k in raw for k in keys):
        return {"quizzical": raw}
    return raw


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    def _merge(a: Any, b: Any) -> Any:
        if isinstance(a, dict) and isinstance(b, dict):
            res = dict(a)
            for k, v in b.items():
                res[k] = _merge(res.get(k), v)
            return res
        return b if b is not None else a
    return _merge(base, override)


def _to_settings_model(root: Dict[str, Any]) -> Settings:
    """
    root is expected to have quizzical.* (after normalization).
    """
    q = root.get("quizzical", {}) or {}

    # LLM: split global knobs (per_call_timeout_s) from per-tool/prompt maps
    llm_raw = q.get("llm", {}) or {}
    tools_raw = dict(llm_raw.get("tools", {}) or {})
    prompts_raw = dict(llm_raw.get("prompts", {}) or {})

    # Build llm_tools map
    tools: Dict[str, ModelConfig] = {}
    for name, cfg in tools_raw.items():
        try:
            tools[name] = ModelConfig(**cfg)
        except ValidationError as ve:
            raise ValueError(f"Invalid llm.tools.{name}: {ve}") from ve

    # Build llm_prompts map
    prompts: Dict[str, PromptConfig] = {}
    for name, cfg in prompts_raw.items():
        try:
            prompts[name] = PromptConfig(**cfg)
        except ValidationError as ve:
            raise ValueError(f"Invalid llm.prompts.{name}: {ve}") from ve

    # Global LLM settings: remove 'tools'/'prompts' and validate the rest
    llm_globals = dict(llm_raw)
    llm_globals.pop("tools", None)
    llm_globals.pop("prompts", None)
    llm_settings = LLMSettings(**llm_globals) if llm_globals else LLMSettings()

    return Settings(
        app=AppInfo(**(q.get("app") or {})),
        feature_flags=FeatureFlags(**(q.get("feature_flags") or {})),
        cors=CorsConfig(**(q.get("cors") or {})),
        project=ProjectConfig(**(q.get("project") or {})),
        quiz=QuizConfig(**(q.get("quiz") or {})),
        agent=AgentConfig(**(q.get("agent") or {})),
        llm=llm_settings,
        llm_tools=tools,
        llm_prompts=prompts,
        security=SecurityConfig(**(q.get("security") or {})),
    )


# ============
# Public API
# ============

@lru_cache
def get_settings() -> Settings:
    """
    Non-secrets:
      1) Azure App Config
      2) Local YAML
      3) Hardcoded defaults

    Secrets:
      A) Azure Key Vault
      B) .env / environment variables

    Returns a Settings model with secrets overlaid on top of the base config.
    """
    # ---------- Base (non-secrets) ----------
    azure_raw = _load_from_azure_app_config()
    if azure_raw:
        log.info("Using Azure App Configuration")
        base = _deep_merge(_DEFAULTS, _ensure_quizzical_root(azure_raw))
    else:
        yaml_raw = _load_from_yaml()
        if yaml_raw:
            log.info("Using local YAML config")
            base = _deep_merge(_DEFAULTS, _ensure_quizzical_root(yaml_raw))
        else:
            log.warning("Using hardcoded defaults (no Azure/YAML found)")
            base = _DEFAULTS

    # ---------- Secrets overlay ----------
    merged: Dict[str, Any] = base
    kv = _load_secrets_from_key_vault()
    if kv:
        merged = _deep_merge(merged, kv)

    env_sec = _load_secrets_from_env()
    if env_sec:
        merged = _deep_merge(merged, env_sec)

    return _to_settings_model(merged)


# Backwards-compatible alias for consumers
settings: Settings = get_settings()
