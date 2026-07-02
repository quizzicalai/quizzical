"""Settings: environment / .env loading and dry-run resolution.

The dry-run rule (owner requirement): the app runs in DRY-RUN mode until all
four X OAuth 1.0a user-context keys appear in the environment (.env). With
keys present it goes live, unless SOCIAL_DRY_RUN=true forces dry-run anyway.

`resolve_dry_run` is a pure function so the behavior is unit-testable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# dotenv is optional at import time so pure-logic tests can run without the
# app venv; at runtime it is present via requirements.txt.
try:  # pragma: no cover
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

APP_DIR = Path(__file__).resolve().parent.parent

_FALSY = ("0", "false", "no", "off", "")
_TRUTHY = ("1", "true", "yes", "on")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return default


def resolve_dry_run(dry_run_env: str | None, have_all_x_keys: bool) -> bool:
    """Pure dry-run resolution.

    - No (or incomplete) X keys -> ALWAYS dry-run, whatever the flag says.
    - Keys present -> live, unless SOCIAL_DRY_RUN is explicitly truthy.
    """
    if not have_all_x_keys:
        return True
    return _as_bool(dry_run_env, default=False)


def normalize_pg_dsn(url: str) -> str:
    """SQLAlchemy-style DSNs (postgresql+psycopg://, +asyncpg://) -> asyncpg."""
    for prefix in ("postgresql+psycopg://", "postgresql+asyncpg://", "postgres+psycopg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


@dataclass
class Settings:
    database_url: str = ""
    openai_api_key: str = ""

    # X / Twitter OAuth 1.0a user context (writes) — absent until the owner
    # creates the developer app; see README "FOR THE OWNER".
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_secret: str = ""
    # App-only bearer token — only needed for recent search (paid Basic tier).
    x_bearer_token: str = ""

    dry_run: bool = True

    # Public site + live API used for share links and share-link verification.
    site_base: str = "https://quafel.com"
    api_base: str = "https://api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io"

    # Models. The judge is deliberately a STRONG model: small models are poor
    # judges of "silly vs insensitive" (owner requirement).
    gen_model: str = "gpt-4o-mini"
    judge_model: str = "gpt-4o"
    embed_model: str = "text-embedding-3-small"
    embed_dim: int = 384  # matches VECTOR(384) in the quizzical schema

    # Search provider: auto | x | fixture | none
    search_mode: str = "auto"
    fixture_path: str = ""
    x_search_enabled: bool = False  # requires paid Basic tier (~$200/mo)

    # Current-events flavor via OpenAI web search (pluggable, optional).
    events_enabled: bool = False

    # Cadence + safety rails.
    post_every_hours: float = 12.0
    reply_every_hours: float = 4.0
    replies_per_cycle: int = 1
    max_writes_per_month: int = 450  # X free tier ~500 writes/mo; keep margin
    author_cooldown_days: int = 7    # never pester the same account twice a week

    # Dual-direction discovery: run the trend probe (AI web search) at the
    # start of every reply cycle. Disable to make reply discovery topic-led
    # only (saves one web-search call per cycle).
    reply_trends_enabled: bool = True

    extras: dict = field(default_factory=dict)

    @property
    def have_all_x_keys(self) -> bool:
        return all((self.x_api_key, self.x_api_secret, self.x_access_token, self.x_access_secret))


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    """Load settings from .env (next to the app) + process environment."""
    if load_dotenv is not None:
        path = Path(env_file) if env_file else APP_DIR / ".env"
        if path.exists():
            load_dotenv(path, override=False)

    def env(name: str, default: str = "") -> str:
        return os.environ.get(name, default).strip()

    s = Settings(
        database_url=normalize_pg_dsn(env("SOCIAL_DATABASE_URL") or env("DATABASE_URL")),
        openai_api_key=env("OPENAI_API_KEY"),
        x_api_key=env("X_API_KEY"),
        x_api_secret=env("X_API_SECRET"),
        x_access_token=env("X_ACCESS_TOKEN"),
        x_access_secret=env("X_ACCESS_SECRET"),
        x_bearer_token=env("X_BEARER_TOKEN"),
        site_base=env("SOCIAL_SITE_BASE", "https://quafel.com").rstrip("/"),
        api_base=env(
            "SOCIAL_API_BASE",
            "https://api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io",
        ).rstrip("/"),
        gen_model=env("SOCIAL_GEN_MODEL", "gpt-4o-mini"),
        judge_model=env("SOCIAL_JUDGE_MODEL", "gpt-4o"),
        embed_model=env("SOCIAL_EMBED_MODEL", "text-embedding-3-small"),
        search_mode=env("SOCIAL_SEARCH_MODE", "auto").lower(),
        fixture_path=env("SOCIAL_FIXTURE_PATH"),
        x_search_enabled=_as_bool(os.environ.get("SOCIAL_X_SEARCH_ENABLED"), False),
        events_enabled=_as_bool(os.environ.get("SOCIAL_EVENTS_ENABLED"), False),
        reply_trends_enabled=_as_bool(os.environ.get("SOCIAL_REPLY_TRENDS_ENABLED"), True),
    )
    s.dry_run = resolve_dry_run(os.environ.get("SOCIAL_DRY_RUN"), s.have_all_x_keys)
    return s
