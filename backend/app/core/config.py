"""
Settings loader (Azure-first with YAML fallback), compatible with PromptManager.

Load order:
  1) Azure App Configuration (blob key `quizzical:appsettings` or hierarchical keys prefixed `quizzical:`)
  2) Local YAML at backend/appconfig.local.yaml (override path with APP_CONFIG_LOCAL_PATH)
  3) Hardcoded defaults in this file (and DEFAULT_PROMPTS in prompts.py for prompts)

No non-secret config exists outside: appconfig.local.yaml, this file, and agent/prompts.py.
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
    origins: List[str] = ["http://localhost:5173"]

class ProjectConfig(BaseModel):
    api_prefix: str = "/api"

class QuizConfig(BaseModel):
    min_characters: int = 4
    max_characters: int = 6
    baseline_questions_n: int = 5
    max_options_m: int = 4
    max_total_questions: int = 20

    @field_validator("max_characters")
    @classmethod
    def _bounds(cls, v: int, info: ValidationInfo) -> int:
        if "min_characters" in info.data and v < info.data["min_characters"]:
            raise ValueError("max_characters must be >= min_characters")
        return v

class AgentConfig(BaseModel):
    max_retries: int = 3

class Settings(BaseModel):
    app: AppInfo = AppInfo()
    feature_flags: FeatureFlags = FeatureFlags()
    cors: CorsConfig = CorsConfig()
    project: ProjectConfig = ProjectConfig()
    quiz: QuizConfig = QuizConfig()
    agent: AgentConfig = AgentConfig()
    llm_tools: Dict[str, ModelConfig] = Field(default_factory=dict)
    llm_prompts: Dict[str, PromptConfig] = Field(default_factory=dict)

    # -----------------------------
    # Compatibility / convenience
    # -----------------------------
    @property
    def APP_ENVIRONMENT(self) -> str:
        """
        Backwards-compatible alias used elsewhere in the codebase.
        Mirrors self.app.environment.
        """
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
        return os.getenv("REDIS_URL", "redis://localhost:6379/0")


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
            "min_characters": 4, "max_characters": 6,
            "baseline_questions_n": 5, "max_options_m": 4,
            "max_total_questions": 20,
        },
        "agent": {"max_retries": 3},
        "llm": {
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
        }
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
        from azure.core.exceptions import ClientAuthenticationError

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
                try:
                    data = json.loads(val)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    try:
                        data = yaml.safe_load(val) or {}
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
            
            parsed: Any
            try:
                parsed = json.loads(val)
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

        if not data:
            return None
        return data
    except ClientAuthenticationError:
        log.warning("Azure authentication failed; falling back.")
        return None
    except Exception as e:
        log.warning("Azure App Config client unavailable; skipping.", error=str(e))
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
# Normalization utilities
# =======================

def _ensure_quizzical_root(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Azure hierarchical keys include 'quizzical' at root; blob may already be rooted or not."""
    if "quizzical" in raw and isinstance(raw["quizzical"], dict):
        return raw
    # If blob already matches inner structure (starts at app/quiz/llm), wrap it
    keys = {"app", "feature_flags", "quiz", "agent", "llm", "llm_tools", "llm_prompts", "cors", "project"}
    if any(k in raw for k in keys):
        return {"quizzical": raw}
    # Otherwise, return as-is (caller will deep-merge with defaults)
    return raw

def _lift_llm_maps(q: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert nested quizzical.llm.{tools,prompts} into top-level llm_tools/llm_prompts
    (internal structure is preserved).
    """
    result = dict(q)
    llm = result.get("llm", {})
    if isinstance(llm, dict):
        if "tools" in llm:
            result["llm_tools"] = llm["tools"]
        if "prompts" in llm:
            result["llm_prompts"] = llm["prompts"]
        result.pop("llm", None)
    result.setdefault("llm_tools", {})
    result.setdefault("llm_prompts", {})
    return result

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
    q = root.get("quizzical", {})
    q = _lift_llm_maps(q)

    # Build llm_tools map
    tools_raw = q.get("llm_tools", {}) or {}
    tools: Dict[str, ModelConfig] = {}
    for name, cfg in tools_raw.items():
        try:
            tools[name] = ModelConfig(**cfg)
        except ValidationError as ve:
            raise ValueError(f"Invalid llm_tools.{name}: {ve}") from ve

    # Build llm_prompts map
    prompts_raw = q.get("llm_prompts", {}) or {}
    prompts: Dict[str, PromptConfig] = {}
    for name, cfg in prompts_raw.items():
        try:
            prompts[name] = PromptConfig(**cfg)
        except ValidationError as ve:
            raise ValueError(f"Invalid llm_prompts.{name}: {ve}") from ve

    settings = Settings(
        app=AppInfo(**(q.get("app") or {})),
        feature_flags=FeatureFlags(**(q.get("feature_flags") or {})),
        cors=CorsConfig(**(q.get("cors") or {})),
        project=ProjectConfig(**(q.get("project") or {})),
        quiz=QuizConfig(**(q.get("quiz") or {})),
        agent=AgentConfig(**(q.get("agent") or {})),
        llm_tools=tools,
        llm_prompts=prompts,
    )
    return settings


# ============
# Public API
# ============

@lru_cache
def get_settings() -> Settings:
    """
    Load settings from:
      1) Azure App Config (blob `quizzical:appsettings` / `quizzical:settings` or hierarchical `quizzical:*`)
      2) Local YAML at backend/appconfig.local.yaml (or APP_CONFIG_LOCAL_PATH)
      3) Hardcoded defaults in this file

    Any missing keys are filled from defaults to keep app stable.
    """
    # 1) Azure
    azure_raw = _load_from_azure_app_config()
    if azure_raw:
        log.info("Using Azure App Configuration")
        merged = _deep_merge(_DEFAULTS, _ensure_quizzical_root(azure_raw))
        return _to_settings_model(merged)

    # 2) Local YAML
    yaml_raw = _load_from_yaml()
    if yaml_raw:
        log.info("Using local YAML config")
        merged = _deep_merge(_DEFAULTS, _ensure_quizzical_root(yaml_raw))
        return _to_settings_model(merged)

    # 3) Defaults
    log.warning("Using hardcoded defaults (no Azure/YAML found)")
    return _to_settings_model(_DEFAULTS)

# Backwards-compatible alias for consumers
settings: Settings = get_settings()