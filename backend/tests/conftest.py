"""
Shared pytest fixtures for the FastAPI backend tests.

What this file does:
- Pins a safe env before importing the app.
- Injects a FakeAgentGraph during lifespan (opt-in via `use_fake_agent_graph` fixture).
- Supplies a minimal async Redis clone backed by a shared dict (`fake_cache_store`).
- Bypasses Turnstile.
- Provides an in-memory SQLite AsyncSession override (harmless if unused).
- Exposes an httpx.AsyncClient bound to the FastAPI app with lifespan on.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool

# --------------------------------------------------------------------------------------
# Pin safe environment BEFORE importing application code
# --------------------------------------------------------------------------------------
os.environ.setdefault("APP_ENVIRONMENT", "local")
os.environ.setdefault("USE_MEMORY_SAVER", "1")      # prefer in-memory checkpointer code path
os.environ.setdefault("ENABLE_TURNSTILE", "false")  # hard-bypass in tests
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# --------------------------------------------------------------------------------------
# Ensure `backend/` (app root) is importable
# --------------------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[1]  # .../backend
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# --------------------------------------------------------------------------------------
# Import app + DI
# --------------------------------------------------------------------------------------
from app.main import app as fastapi_app  # noqa: E402
import app.main as main_mod              # noqa: E402
from app.api.dependencies import get_db_session, get_redis_client, verify_turnstile  # noqa: E402

# ======================================================================================
# Fake Agent Graph used by app.lifespan startup
# ======================================================================================

class _FakeStateSnapshot:
    def __init__(self, values: dict) -> None:
        self.values = values

class _FakeAgentGraph:
    """
    Very small fake of the real agent graph:

      - ainvoke: ensures a *non-empty* category_synopsis is present and stores state per thread_id.
      - astream: yields a couple ticks and injects generated_characters once.
      - aget_state: returns last stored state for thread_id.

    This is enough to satisfy the /quiz flow without hitting LLMs or tools.
    """
    def __init__(self) -> None:
        self._store: Dict[str, dict] = {}

    async def ainvoke(self, state: dict, config: dict) -> dict:
        thread_id = str(config.get("configurable", {}).get("thread_id") or "thread")
        s = dict(state)

        # IMPORTANT FIX: set synopsis if missing OR falsy (setdefault was not enough)
        if not s.get("category_synopsis"):
            cat = s.get("category") or "Test Category"
            s["category_synopsis"] = {
                "title": f"Quiz: {cat}",
                "summary": "A short test synopsis to satisfy the API contract.",
            }

        # Some endpoints look for these keys; keep shape stable
        s.setdefault("generated_questions", [])
        s.setdefault("ready_for_questions", False)
        s.setdefault("baseline_ready", False)

        self._store[thread_id] = s
        return s

    async def astream(self, state: dict, config: dict):
        thread_id = str(config.get("configurable", {}).get("thread_id") or "thread")
        yield {"tick": 1}
        s = self._store.get(thread_id, dict(state))
        if not s.get("generated_characters"):
            s["generated_characters"] = [
                {
                    "name": "The Optimist",
                    "short_description": "Bright outlook",
                    "profile_text": "Always sees the good.",
                },
                {
                    "name": "The Analyst",
                    "short_description": "Thinks deeply",
                    "profile_text": "Loves data and logic.",
                },
            ]
            self._store[thread_id] = s
        yield {"tick": 2}

    async def aget_state(self, config: dict) -> _FakeStateSnapshot:
        thread_id = str(config.get("configurable", {}).get("thread_id") or "thread")
        return _FakeStateSnapshot(values=self._store.get(thread_id, {}))

# ======================================================================================
# Opt-in monkeypatching to use the FakeAgentGraph during app lifespan
# ======================================================================================
@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture(scope="function")
def use_fake_agent_graph(monkeypatch):
    """
    When a test uses this fixture (e.g., via @pytest.mark.usefixtures('use_fake_agent_graph')),
    the app will build our _FakeAgentGraph during startup.
    """
    async def _fake_create_agent_graph():
        return _FakeAgentGraph()

    async def _fake_aclose_agent_graph(_graph) -> None:
        return None

    monkeypatch.setattr(main_mod, "create_agent_graph", _fake_create_agent_graph, raising=True)
    monkeypatch.setattr(main_mod, "aclose_agent_graph", _fake_aclose_agent_graph, raising=True)
    yield

# ======================================================================================
# Minimal async Redis clone (get/set + WATCH/MULTI/EXEC pipeline)
# ======================================================================================

try:
    from redis.exceptions import WatchError  # used by CacheRepository
except Exception:  # pragma: no cover
    class WatchError(RuntimeError):
        pass

class _FakePipeline:
    def __init__(self, parent: "_FakeRedis") -> None:
        self._parent = parent
        self._watched_key: Optional[str] = None
        self._watched_ver: Optional[int] = None
        self._queued: List[Tuple[str, Tuple[Any, ...], dict]] = []
        self._in_multi = False

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.reset()

    async def watch(self, key: str) -> None:
        self._watched_key = key
        self._watched_ver = self._parent._versions.get(key, 0)

    async def unwatch(self) -> None:
        self._watched_key = None
        self._watched_ver = None

    async def get(self, key: str):
        return self._parent._kv.get(key)

    def multi(self) -> None:
        self._in_multi = True

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        self._queued.append(("set", (key, value), {"ex": ex}))

    async def execute(self):
        # Conflict detection on watched key
        if self._watched_key is not None:
            cur_ver = self._parent._versions.get(self._watched_key, 0)
            if self._watched_ver is None or cur_ver != self._watched_ver:
                raise WatchError("Watched key modified")

        for op, args, kwargs in self._queued:
            if op == "set":
                key, value = args
                self._parent._kv[key] = value
                self._parent._versions[key] = self._parent._versions.get(key, 0) + 1

        # Reset after exec
        self._in_multi = False
        self._queued.clear()
        await self.unwatch()
        return True

    def reset(self) -> None:
        self._watched_key = None
        self._watched_ver = None
        self._queued.clear()
        self._in_multi = False

class _FakeRedis:
    def __init__(self) -> None:
        self._kv: Dict[str, Any] = {}
        self._versions: Dict[str, int] = {}

    async def get(self, key: str):
        return self._kv.get(key)

    async def set(self, key: str, value: Any, ex: Optional[int] = None):
        self._kv[key] = value
        self._versions[key] = self._versions.get(key, 0) + 1
        return True

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

# Shared dict so tests can inspect cache state directly
@pytest.fixture(scope="function")
def fake_cache_store() -> Dict[str, Any]:
    return {}

@pytest.fixture(scope="function")
def fake_redis(fake_cache_store: Dict[str, Any]) -> _FakeRedis:
    r = _FakeRedis()
    # Share the kv dict so white-box tests can read/write easily
    r._kv = fake_cache_store  # type: ignore[attr-defined]
    return r

@pytest.fixture(autouse=True)
def _reset_fake_cache(fake_cache_store: Dict[str, Any]):
    fake_cache_store.clear()
    yield
    fake_cache_store.clear()

# Wire Redis DI
@pytest.fixture(autouse=True)
def _override_redis_client(fake_redis: _FakeRedis):
    async def _dep():
        return fake_redis
    fastapi_app.dependency_overrides[get_redis_client] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_redis_client, None)

# ======================================================================================
# Turnstile bypass
# ======================================================================================

@pytest.fixture(autouse=True)
def _override_turnstile():
    async def _ok(*_a, **_k) -> bool:
        return True
    fastapi_app.dependency_overrides[verify_turnstile] = _ok
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(verify_turnstile, None)

# ======================================================================================
# Optional: Async SQLAlchemy session override (SQLite in-memory)
# ======================================================================================

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.models.db import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
_test_engine = create_async_engine(
    TEST_DATABASE_URL,
    future=True,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

@asynccontextmanager
async def _test_session_ctx() -> AsyncGenerator[AsyncSession, None]:
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        async with _TestingSessionLocal(bind=conn) as session:
            yield session
        await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with _test_session_ctx() as s:
        yield s

@pytest_asyncio.fixture(scope="function")
async def _override_db_dep(db_session: AsyncSession):
    async def _dep() -> AsyncGenerator[AsyncSession, None]:
        yield db_session
    fastapi_app.dependency_overrides[get_db_session] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)

# ======================================================================================
# httpx client bound to our app with lifespan on
# ======================================================================================

@pytest_asyncio.fixture(scope="function")
async def client(_override_db_dep) -> AsyncGenerator[AsyncClient, None]:
    async with fastapi_app.router.lifespan_context(fastapi_app):
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c

# Safety: always clear overrides
@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    fastapi_app.dependency_overrides.clear()
