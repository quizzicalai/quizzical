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

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

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

    # ---- optional, used by web_search (and future tools if desired) ----
    effort: Literal["low", "medium", "high"] | None = None   # Responses API reasoning.effort
    allowed_domains: list[str] | None = None                 # domain allow-list
    user_location: "WebUserLocation | None" = None           # approximate location
    include_sources: bool = True                                # include web_search_call.action.sources
    tool_choice: Literal["auto"] | dict[str, Any] = "auto" # Responses API tool choice


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
    origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


class ProjectConfig(BaseModel):
    api_prefix: str = "/api"


class QuizConfig(BaseModel):
    min_characters: int = 4
    max_characters: int = 6
    baseline_questions_n: int = 5
    max_options_m: int = 4
    max_total_questions: int = 20
    min_questions_before_early_finish: int = 6
    early_finish_confidence: float = 0.9
    # Time budgets used by endpoints/quiz.py
    first_step_timeout_s: float = 30.0
    stream_budget_s: float = 30.0
    # Allows bounded parallelism for character generation; None → auto
    character_concurrency: int | None = None
    # Skip the single-shot ``profile_batch_writer`` LLM call when the number of
    # archetypes exceeds this cap — beyond it the structured JSON output
    # routinely overflows the model's max_output_tokens and we waste ~30s on a
    # doomed call before falling back to per-character requests
    # (AC-PERF-CHAR-1).
    batch_max_archetypes: int = 6

    @field_validator("max_characters")
    @classmethod
    def _bounds(cls, v: int, info: ValidationInfo) -> int:
        if "min_characters" in info.data and v < info.data["min_characters"]:
            raise ValueError("max_characters must be >= min_characters")
        return v

    @field_validator("character_concurrency")
    @classmethod
    def _cc_valid(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("character_concurrency must be >= 1 or null")
        return v

    @field_validator("batch_max_archetypes")
    @classmethod
    def _bma_valid(cls, v: int) -> int:
        if v < 0:
            raise ValueError("batch_max_archetypes must be >= 0")
        return v


class AgentConfig(BaseModel):
    max_retries: int = 3


class RetryConfig(BaseModel):
    """§16.1/§16.2 — bounded retry on transient errors.

    ``max_attempts=1`` disables retry entirely (1 try, no retries). Backoff
    is ``min(cap_ms, base_ms * 2 ** (attempt-1))`` plus uniform jitter
    ``[0, base_ms)`` ms.
    """
    max_attempts: int = 3
    base_ms: int = 200
    cap_ms: int = 2000


class LLMResponseCacheConfig(BaseModel):
    """§9.7.8 — LiteLLM Redis-backed response cache configuration.

    Disabled by default: turning it on is a deliberate decision because two
    users feeding the same input would receive identical cached output,
    reducing the variety the quiz experience relies on. When enabled, the
    cache is wired at startup but no tool opts in by default — call sites
    pass ``cache=True`` to ``get_structured_response`` per call.
    """
    enabled: bool = False
    ttl_seconds: int = 3600
    namespace: str = "quizzical:llm"

    @field_validator("ttl_seconds")
    @classmethod
    def _ttl_must_be_positive(cls, v: int) -> int:
        if v is None or int(v) < 1:
            raise ValueError("llm.response_cache.ttl_seconds must be >= 1")
        return int(v)

    @field_validator("namespace")
    @classmethod
    def _namespace_shape(cls, v: str) -> str:
        if not isinstance(v, str) or not v:
            raise ValueError("llm.response_cache.namespace must be a non-empty string")
        if len(v) > 64:
            raise ValueError("llm.response_cache.namespace must be <= 64 chars")
        # Restrict to a small safe set so the value can be embedded in Redis keys
        # without escaping concerns. ASCII-only on purpose.
        for ch in v:
            if not (("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch in ":_-"):
                raise ValueError(
                    "llm.response_cache.namespace may only contain [A-Za-z0-9:_-]"
                )
        return v


class LLMGlobals(BaseModel):
    # Global per-call timeout used by parallel character creation (and reused by question gen).
    per_call_timeout_s: int = 30
    # §16.1 — transient-error retry policy.
    retry: RetryConfig = Field(default_factory=lambda: RetryConfig())
    # §9.7.8 — LiteLLM Redis-backed response cache (off by default).
    response_cache: LLMResponseCacheConfig = Field(default_factory=lambda: LLMResponseCacheConfig())
    # §9.7.6 — hard cap on the size of a single LLM raw response (in bytes,
    # measured against the JSON-serialised payload). Defends against a buggy
    # or compromised provider returning a multi-MB blob that would exhaust
    # memory or stall structured parsing. 256 KiB is generous: typical
    # Responses-API JSON for our largest tools is well under 64 KiB.
    max_response_bytes: int = 262144
    # §17.1 — global LLM concurrency cap (AC-SCALE-LLM-*). Bounds the number
    # of in-flight ``litellm.responses`` calls process-wide. Sized for ~ 4
    # active quiz sessions × ~4 parallel character/question generations.
    max_concurrency: int = 16
    # §17.1 AC-SCALE-LLM-2 — max wait for a concurrency slot before raising
    # ``LLMConcurrencyTimeoutError``. Slightly less than the per-call LLM
    # timeout so semaphore-pressure errors surface before client timeouts.
    acquire_timeout_s: float = 30.0

    @field_validator("max_response_bytes")
    @classmethod
    def _max_response_bytes_must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("llm.max_response_bytes must be >= 1")
        return v

    @field_validator("max_concurrency")
    @classmethod
    def _max_concurrency_must_be_positive(cls, v: int) -> int:
        if v is None or int(v) < 1:
            raise ValueError("llm.max_concurrency must be >= 1")
        return int(v)

    @field_validator("acquire_timeout_s")
    @classmethod
    def _acquire_timeout_must_be_non_negative(cls, v: float) -> float:
        if v is None or float(v) < 0:
            raise ValueError("llm.acquire_timeout_s must be >= 0")
        return float(v)

class WebUserLocation(BaseModel):
    # Matches Responses API "approximate" shape; all fields optional
    type: Literal["approximate"] = "approximate"
    country: str | None = None  # ISO-2 (e.g., "US")
    city: str | None = None
    region: str | None = None
    timezone: str | None = None  # IANA TZ, e.g., "America/Los_Angeles"

# -------- Secrets (keys/tokens) --------
class TurnstileConfig(BaseModel):
    site_key: str | None = None
    secret_key: str | None = None


class SecurityConfig(BaseModel):
    # Global toggle (e.g., ENABLE_TURNSTILE); default True for prod, can be disabled in local/dev via .env
    enabled: bool = True
    turnstile: TurnstileConfig = TurnstileConfig()
    # §15.1 — Redis token-bucket rate limiter
    rate_limit: "RateLimitConfig" = Field(default_factory=lambda: RateLimitConfig())
    # §9.7.4 — per-quiz feedback throttle: prevents spam on a single quiz_id.
    # Default capacity=3, refill 1/60s ≈ "3 fast taps then 1 per minute".
    feedback_rate_limit: "RateLimitConfig" = Field(
        default_factory=lambda: RateLimitConfig(
            capacity=3, refill_per_second=1.0 / 60.0
        )
    )
    # §R16 — per-IP /quiz/start throttle: caps LLM-cost abuse from a single
    # source. Capacity=3, refill 1/30s ≈ "3 fast starts then ~1 every 30s",
    # so a sustained attacker is bounded to ~120 quiz starts per hour.
    # Evaluated BEFORE verify_turnstile so blocked IPs never round-trip to
    # Cloudflare. Fail-open on Redis errors (handled by RateLimiter).
    quiz_start_rate_limit: "RateLimitConfig" = Field(
        default_factory=lambda: RateLimitConfig(
            capacity=3, refill_per_second=1.0 / 30.0
        )
    )
    # §15.2 — Trusted Host allowlist (production-only enforcement by default)
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])
    # §15.4 — single-flight session lock TTL
    session_lock_ttl_s: int = 10


class RateLimitConfig(BaseModel):
    """§15.1 — Redis token-bucket rate limiter."""
    enabled: bool = True
    capacity: int = 30                 # max tokens per bucket
    refill_per_second: float = 1.0     # tokens added per second
    # Allowlisted path prefixes that are never rate-limited.
    allow_paths: list[str] = Field(
        default_factory=lambda: [
            "/health", "/readiness", "/docs", "/redoc", "/openapi.json", "/",
        ]
    )

    # §9.7.3 — fail loudly at startup on misconfiguration.
    @field_validator("capacity")
    @classmethod
    def _capacity_must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("rate_limit.capacity must be >= 1")
        return v

    @field_validator("refill_per_second")
    @classmethod
    def _refill_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("rate_limit.refill_per_second must be > 0")
        return v


SecurityConfig.model_rebuild()

# =========================
# ADDED: Retrieval settings
# =========================
class RetrievalSettings(BaseModel):
    """
    Central policy for any external retrieval (Wikipedia or general web).
    Defaults are strict to minimize retrieval.
    """
    policy: Literal["off", "media_only", "auto"] = "media_only"
    allow_wikipedia: bool = False
    allow_web: bool = False
    max_calls_per_run: int = 0
    allowed_domains: list[str] | None = None


class DatabaseSettings(BaseModel):
    """AC-DB-PERF-1..2 — PostgreSQL connection pool sizing.

    Defaults are conservative; production deployments override via
    ``appconfig.local.yaml`` (or environment) when concurrency demands it.
    """
    pool_size: int = 20
    max_overflow: int = 10
    pool_recycle_s: int = 1800  # recycle connections every 30 min


# ---------------------------------------------------------------------------
# §21 — Precompute (Pre-Computed Topic Knowledge Packs)
# ---------------------------------------------------------------------------


class PrecomputeThresholds(BaseModel):
    """`AC-PRECOMP-LOOKUP-3` — thresholds with documented defaults.

    `match` is the minimum cosine similarity for a vector NN HIT;
    `pass_score` is the evaluator gate that promotes an artefact;
    `strong_trigger_score` triggers a tier escalation when a tier-1 score
    falls below it.
    """

    match: float = 0.86
    pass_score: int = 7
    strong_trigger_score: int = 5


class PrecomputeConfig(BaseModel):
    """`AC-PRECOMP-LOOKUP-1..5` — read-path configuration (Phase 2).

    Default `enabled=False` keeps `/quiz/start` byte-for-byte identical to
    the live-agent path until an operator flips the flag in staging /
    production. Additional sub-sections (`worker`, `image_storage`, etc.)
    land in later phases under the same `quizzical.precompute.*` namespace.
    """

    enabled: bool = False
    thresholds: PrecomputeThresholds = Field(default_factory=PrecomputeThresholds)
    # §21 Phase 3 — write-path knobs.
    daily_budget_usd: float = 5.0
    """`AC-PRECOMP-BUILD-5` — hard daily $-cap; pre-attempt check skips
    when today's spend already meets/exceeds it."""
    tier3_budget_pct: float = 0.75
    """`AC-PRECOMP-COST-6` — Tier-3 (web search) cutoff as a fraction of
    `daily_budget_usd`. At ≥ this share spent today, escalation defers."""
    max_build_attempts: int = 3
    """`AC-PRECOMP-BUILD-2` — total tier attempts (cheap → strong → strong+search)."""
    daytime_concurrency: int = 1
    offpeak_concurrency: int = 4
    offpeak_window_utc: str = "02:00-08:00"
    """`AC-PRECOMP-COST-5` — overnight backfill window (UTC, HH:MM-HH:MM)."""
    flag_quarantine_count: int = 5
    flag_quarantine_window_hours: int = 24
    """`AC-PRECOMP-FLAG-4` — distinct-IP-hash threshold + window for
    auto-quarantine (Phase 6 wires the cascade; the values live here)."""
    restricted_pass_score: int = 9
    """`AC-PRECOMP-SAFETY-2` — `τ_pass` override for `restricted` topics."""
    image_storage: "ImageStorageConfig" = Field(default_factory=lambda: ImageStorageConfig())
    """§21 Phase 5 — image storage provider switch + rehost knobs."""
    per_question_images: bool = False
    """`AC-PRECOMP-COST-7` — opt-in per-question image generation. Default off."""


class ImageStorageConfig(BaseModel):
    """`AC-PRECOMP-IMG-1..3` — image rehost / serving controls.

    `provider=fal` keeps today's behaviour: stored `storage_uri` is the
    upstream FAL CDN URL. `provider=local` rehosts bytes into
    `media_assets.bytes_blob` and serves them via `GET /api/media/{id}`
    with immutable cache + content-hash ETag (Phase 5).

    Azure Blob lands in Phase 12 — until then the provider literal is
    intentionally only the two values."""

    provider: Literal["fal", "local"] = "fal"
    rehost_window_days: int = 7
    """`AC-PRECOMP-IMG-2` — rehost when `expires_at - now ≤ this many days`."""
    cache_control: str = "public, max-age=31536000, immutable"
    """`AC-PRECOMP-IMG-3` / `AC-PRECOMP-PERF-4` — immutable browser cache
    for content-addressed assets."""


class ImageGenSettings(BaseModel):
    """FAL image generation (§7.8). Speed > fidelity; non-blocking.

    Defaults tuned for Phase 7 (AC-IMG-PERF-1..3):
      - ``concurrency`` defaults to 6 to match the maximum character fan-out so
        the FAL semaphore never becomes the bottleneck.
      - ``timeout_s`` defaults to 10.0 — a stuck FAL call can extend quiz
        latency by at most one character's worth before the pipeline fails open.
      - ``num_inference_steps`` stays at 2 (Schnell minimum); style consistency
        is enforced via ``STYLE_ANCHOR`` + deterministic seed in image_tools,
        not via fidelity bumps.
    """
    enabled: bool = True
    provider: Literal["fal"] = "fal"
    model: str = "fal-ai/flux/schnell"
    image_size: dict[str, int] = Field(default_factory=lambda: {"width": 512, "height": 512})
    num_inference_steps: int = 2
    timeout_s: float = 10.0
    concurrency: int = 6
    style_suffix: str = (
        "flat illustrated portrait, soft lighting, muted palette, "
        "consistent illustrated style, no text"
    )
    negative_prompt: str = (
        "text, watermark, logo, signature, blurry, deformed, extra limbs, low quality"
    )
    # §9.7.1 — host allowlist for FAL-returned image URLs. Hosts match by
    # exact equality OR as suffix preceded by a dot (subdomain match).
    # An empty list disables the host check (scheme check still applies).
    url_allowlist: list[str] = Field(
        default_factory=lambda: ["fal.media", "v2.fal.media", "v3.fal.media"]
    )
    # §16.2 — transient-error retry. Defaults are tighter than LLM (cap=1500ms,
    # max_attempts=2) because image gen is fail-open and we don't want a slow
    # FAL outage to extend a quiz's image-fill window beyond a couple seconds.
    retry: RetryConfig = Field(
        default_factory=lambda: RetryConfig(max_attempts=2, base_ms=200, cap_ms=1500)
    )


class Settings(BaseModel):
    app: AppInfo = AppInfo()
    feature_flags: FeatureFlags = FeatureFlags()
    cors: CorsConfig = CorsConfig()
    project: ProjectConfig = ProjectConfig()
    # ADDED: retrieval
    retrieval: RetrievalSettings = RetrievalSettings()
    # ADDED: image generation (FAL)
    image_gen: ImageGenSettings = ImageGenSettings()
    # ADDED (Phase 7): DB pool sizing (§AC-DB-PERF-1..3)
    database: DatabaseSettings = DatabaseSettings()
    # ADDED (§21 Phase 2): Pre-Computed Topic Knowledge Packs read-path
    # config. Default `enabled=False` is mandatory through Phase 5 so the
    # live `/quiz/start` flow remains byte-for-byte unchanged.
    precompute: PrecomputeConfig = PrecomputeConfig()
    quiz: QuizConfig = QuizConfig()
    agent: AgentConfig = AgentConfig()
    llm: LLMGlobals = LLMGlobals()
    llm_tools: dict[str, ModelConfig] = Field(default_factory=dict)
    llm_prompts: dict[str, PromptConfig] = Field(default_factory=dict)
    security: SecurityConfig = SecurityConfig()
    # §17.2 (AC-SCALE-SHUTDOWN-1..4) — max time the lifespan teardown waits
    # for in-flight LLM/agent work to drain before disposing pools. Set to 0
    # to disable the drain wait (useful for unit tests).
    shutdown_grace_s: float = 15.0

    @field_validator("shutdown_grace_s")
    @classmethod
    def _shutdown_grace_must_be_non_negative(cls, v: float) -> float:
        if v is None or float(v) < 0:
            raise ValueError("shutdown_grace_s must be >= 0")
        return float(v)

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
        # Prefer single var if provided; allow composed vars via docker-compose env as a fallback
        url = os.getenv("REDIS_URL")
        if url:
            return url
        host = os.getenv("REDIS_HOST") or os.getenv("REDIS__HOST") or "localhost"
        port = os.getenv("REDIS_PORT") or os.getenv("REDIS__PORT") or "6379"
        db = os.getenv("REDIS_DB") or os.getenv("REDIS__DB") or "0"
        return f"redis://{host}:{port}/{db}"

    @property
    def DATABASE_URL(self) -> str | None:
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
    def TURNSTILE_SITE_KEY(self) -> str | None:
        """
        Backwards-compatible alias for legacy code that expects a top-level constant.
        Mirrors settings.security.turnstile.site_key.
        """
        try:
            return self.security.turnstile.site_key
        except Exception:
            return None

    @property
    def TURNSTILE_SECRET_KEY(self) -> str | None:
        """
        Backwards-compatible alias for legacy code that expects a top-level constant.
        Mirrors settings.security.turnstile.secret_key.
        """
        try:
            return self.security.turnstile.secret_key
        except Exception:
            return None

    # -----------------------------
    # §21 Phase 3 — Precompute operator secrets (env-only).
    # `OPERATOR_TOKEN` gates admin endpoints; `FLAG_HMAC_SECRET` keys
    # community-flag IP hashing. Both fail closed in production when
    # absent or shorter than 32 bytes (`AC-PRECOMP-SEC-9`).
    # -----------------------------
    @property
    def OPERATOR_TOKEN(self) -> str | None:
        return (os.getenv("OPERATOR_TOKEN") or "").strip() or None

    @property
    def FLAG_HMAC_SECRET(self) -> str | None:
        return (os.getenv("FLAG_HMAC_SECRET") or "").strip() or None

    @property
    def PRECOMPUTE_HMAC_SECRET(self) -> str | None:
        """§21 Phase 9 — HMAC key for signed starter-pack archive import.

        Required by the operator-only ``POST /admin/precompute/import``
        endpoint. Returns ``None`` if unset; the endpoint refuses to
        process archives unless this is configured (>=32 bytes).
        """
        return (os.getenv("PRECOMPUTE_HMAC_SECRET") or "").strip() or None

# ===========
# Defaults
# ===========

_DEFAULTS: dict[str, Any] = {
    "quizzical": {
        "app": {"name": "Quizzical", "environment": "local", "debug": True},
        "feature_flags": {"flow_mode": "agent"},
        "cors": {"origins": ["http://localhost:5173", "http://127.0.0.1:5173"]},
        "project": {"api_prefix": "/api"},
        # ADDED: retrieval defaults
        "retrieval": {
            "policy": "media_only",
            "allow_wikipedia": False,
            "allow_web": False,
            "max_calls_per_run": 0,
            "allowed_domains": None,
        },
        "quiz": {
            "min_characters": 4,
            "max_characters": 6,
            "baseline_questions_n": 5,
            "max_options_m": 4,
            "max_total_questions": 20,
            "min_questions_before_early_finish": 6,
            "early_finish_confidence": 0.9,
            "first_step_timeout_s": 30.0,
            "stream_budget_s": 30.0,
            "character_concurrency": None,
        },
        "agent": {"max_retries": 3},
        "llm": {
            # Ensure this exists even without YAML so graph reads settings.llm.per_call_timeout_s.
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
                "decision_maker": {"model": "gpt-4o-mini", "temperature": 0.2, "max_output_tokens": 800, "timeout_s": 18, "json_output": True},
                "web_search": {
                    "model": "o4-mini",           # fast, capable; switch to "gpt-5" for agentic search
                    "temperature": 0.2,
                    "max_output_tokens": 1200,
                    "timeout_s": 20,
                    "json_output": False,
                    "effort": "low",              # "low" | "medium" | "high" (for reasoning models)
                    "allowed_domains": None,      # or ["www.cdc.gov","www.who.int", ...] (no http/https)
                    "user_location": {
                        "type": "approximate",
                        "country": "US",
                        "city": "Seattle",
                        "region": "WA",
                        "timezone": "America/Los_Angeles"
                    },
                    "include_sources": True,
                    "tool_choice": "auto"
                }
            },
            "prompts": {}
        },
    }
}


# =======================
# Normalization utilities
# =======================

def _ensure_quizzical_root(raw: dict[str, Any]) -> dict[str, Any]:
    """Azure hierarchical keys include 'quizzical' at root; blob may already be rooted or not."""
    if "quizzical" in raw and isinstance(raw["quizzical"], dict):
        return raw
    keys = {"app", "feature_flags", "quiz", "agent", "llm", "llm_tools", "llm_prompts", "cors", "project", "security", "retrieval"}
    if any(k in raw for k in keys):
        return {"quizzical": raw}
    return raw


def _lift_llm_maps(q: dict[str, Any]) -> dict[str, Any]:
    """
    Convert nested quizzical.llm.{tools,prompts} into top-level llm_tools/llm_prompts,
    while preserving remaining llm keys (e.g., per_call_timeout_s) so they can map
    into Settings.llm.
    """
    result = dict(q)
    llm = result.get("llm", {})
    if isinstance(llm, dict):
        # Promote maps
        if "tools" in llm:
            result["llm_tools"] = llm["tools"]
        if "prompts" in llm:
            result["llm_prompts"] = llm["prompts"]
        # Preserve any non-map llm keys (e.g., per_call_timeout_s)
        llm_remaining = {k: v for k, v in llm.items() if k not in {"tools", "prompts"}}
        result["llm"] = llm_remaining
    result.setdefault("llm_tools", {})
    result.setdefault("llm_prompts", {})
    result.setdefault("llm", {})
    return result


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    def _merge(a: Any, b: Any) -> Any:
        if isinstance(a, dict) and isinstance(b, dict):
            res = dict(a)
            for k, v in b.items():
                res[k] = _merge(res.get(k), v)
            return res
        return b if b is not None else a
    return _merge(base, override)


def _to_settings_model(root: dict[str, Any]) -> Settings:
    """
    root is expected to have quizzical.* (after normalization).
    """
    q = root.get("quizzical", {})
    q = _lift_llm_maps(q)

    # Build llm_tools map
    tools_raw = q.get("llm_tools", {}) or {}
    tools: dict[str, ModelConfig] = {}
    for name, cfg in tools_raw.items():
        try:
            tools[name] = ModelConfig(**cfg)
        except ValidationError as ve:
            raise ValueError(f"Invalid llm_tools.{name}: {ve}") from ve

    # Build llm_prompts map
    prompts_raw = q.get("llm_prompts", {}) or {}
    prompts: dict[str, PromptConfig] = {}
    for name, cfg in prompts_raw.items():
        try:
            prompts[name] = PromptConfig(**cfg)
        except ValidationError as ve:
            raise ValueError(f"Invalid llm_prompts.{name}: {ve}") from ve

    # LLM globals (e.g., per_call_timeout_s)
    llm_globals = LLMGlobals(**(q.get("llm") or {}))

    return Settings(
        app=AppInfo(**(q.get("app") or {})),
        feature_flags=FeatureFlags(**(q.get("feature_flags") or {})),
        cors=CorsConfig(**(q.get("cors") or {})),
        project=ProjectConfig(**(q.get("project") or {})),
        retrieval=RetrievalSettings(**(q.get("retrieval") or {})),  # ADDED
        image_gen=ImageGenSettings(**(q.get("image_gen") or {})),
        precompute=PrecomputeConfig(**(q.get("precompute") or {})),
        quiz=QuizConfig(**(q.get("quiz") or {})),
        agent=AgentConfig(**(q.get("agent") or {})),
        llm=llm_globals,
        llm_tools=tools,
        llm_prompts=prompts,
        security=SecurityConfig(**(q.get("security") or {})),
    )


# ===================
# Local YAML fallback
# ===================

def _load_from_yaml() -> dict[str, Any] | None:
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

    candidates: list[Path] = []
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


def _load_secrets_from_key_vault() -> dict[str, Any] | None:
    """
    Load secret values from Azure Key Vault if configured.
    Accepts env aliases:
      - KEYVAULT_URI, KEY_VAULT_URI, AZURE_KEY_VAULT_ENDPOINT
      - KEY_VAULT_NAME  (constructs https://{name}.vault.azure.net)
    Returns a nested dict under {"quizzical": {"security": {...}}} or None.
    """

    # DISABLED: To renable, remove this line
    return None

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

        def _get(name: str) -> str | None:
            try:
                s = client.get_secret(name)
                return s.value
            except Exception:
                return None

        # Accepted secret names (allow both UPPER_SNAKE and PascalCase for convenience)
        turnstile_site = _get("TURNSTILE_SITE_KEY") or _get("TurnstileSiteKey")
        turnstile_secret = _get("TURNSTILE_SECRET_KEY") or _get("TurnstileSecretKey")

        sec: dict[str, Any] = {"quizzical": {"security": {"turnstile": {}}}}
        if turnstile_site:
            sec["quizzical"]["security"]["turnstile"]["site_key"] = turnstile_site
        if turnstile_secret:
            sec["quizzical"]["security"]["turnstile"]["secret_key"] = turnstile_secret

        # If nothing found, return None so we continue to .env
        if not sec["quizzical"]["security"]["turnstile"]:
            return None

        log.info("Loaded secrets from Azure Key Vault")
        return sec
    except Exception as e:
        log.warning("Key Vault not available; skipping.", error=str(e))
        return None


def _load_secrets_from_env() -> dict[str, Any]:
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

    sec: dict[str, Any] = {"quizzical": {"security": {}}}

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
    yaml_raw = _load_from_yaml()
    if yaml_raw:
        log.info("Using local YAML config")
        base = _deep_merge(_DEFAULTS, _ensure_quizzical_root(yaml_raw))
    else:
        log.warning("Using hardcoded defaults (no Azure/YAML found)")
        base = _DEFAULTS

    # ---------- Secrets overlay ----------
    merged: dict[str, Any] = base
    kv = _load_secrets_from_key_vault()
    if kv:
        merged = _deep_merge(merged, kv)

    env_sec = _load_secrets_from_env()
    if env_sec:
        merged = _deep_merge(merged, env_sec)

    s = _to_settings_model(merged)

    # If retrieval.allowed_domains is set, propagate it into the web_search tool config unless explicitly set there.
    try:
        if s.retrieval and s.retrieval.allowed_domains:
            ws = s.llm_tools.get("web_search")
            if ws and not ws.allowed_domains:
                ws.allowed_domains = list(s.retrieval.allowed_domains)
    except Exception:
        pass

    return s


# Backwards-compatible alias for consumers
settings: Settings = get_settings()
