# backend/tests/conftest.py
"""
Shared pytest fixtures for the QuizzicalAI FastAPI backend.

Goals:
- Safe env pinned BEFORE importing the FastAPI app.
- Stub agent graph during lifespan; no real network/services.
- Fake Redis (async) with minimal get/set/pipeline + WATCH/MULTI/EXEC semantics.
- Turnstile bypass via dependency override.
- Async HTTP client (httpx) with ASGITransport(lifespan="on").
- Optional in-memory SQLite AsyncSession override (harmless if unused).
- Helpers: expose/clear the fake cache store per test for isolation.
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

# --------------------------------------------------------------------------------------
# Pin safe environment BEFORE importing application code
# --------------------------------------------------------------------------------------
os.environ.setdefault("APP_ENVIRONMENT", "local")
os.environ.setdefault("USE_MEMORY_SAVER", "1")     # force in-memory LangGraph checkpointer
os.environ.setdefault("ENABLE_TURNSTILE", "false") # hard bypass in tests (we also override dep)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")  # harmless default

# --------------------------------------------------------------------------------------
# Ensure `backend/` is on sys.path so `from app...` imports resolve
# --------------------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[1]  # .../backend
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# --------------------------------------------------------------------------------------
# Import the FastAPI app and DI hooks
# --------------------------------------------------------------------------------------
from fastapi import BackgroundTasks
from app.main import app as fastapi_app
import app.main as main_mod
from app.api.dependencies import get_db_session, get_redis_client, verify_turnstile

# ======================================================================================
# Fake Agent Graph (duck-typed) used by app.lifespan startup
# ======================================================================================

class _FakeStateSnapshot:
    def __init__(self, values: dict) -> None:
        self.values = values

class _FakeAgentGraph:
    """
    Minimal duck-typed agent graph:
      - ainvoke: ensures a synopsis exists and stores state by thread_id
      - astream: yields a few ticks and (once) injects generated_characters
      - aget_state: returns last stored state for thread_id
    This is enough for basic /quiz flows in tests without real LLM/tooling.
    """
    def __init__(self) -> None:
        self._store: Dict[str, dict] = {}

    async def ainvoke(self, state: dict, config: dict) -> dict:
        thread_id = str(config.get("configurable", {}).get("thread_id") or "thread")
        s = dict(state)
        s.setdefault("category_synopsis", {"title": f"Quiz: {s.get('category','')}", "summary": "Test synopsis."})
        self._store[thread_id] = s
        return s

    async def astream(self, state: dict, config: dict):
        thread_id = str(config.get("configurable", {}).get("thread_id") or "thread")
        # tick 1: no characters yet
        yield {"tick": 1}
        # tick 2: inject some characters exactly once
        s = self._store.get(thread_id, dict(state))
        if not s.get("generated_characters"):
            s["generated_characters"] = [
                {"name": "The Optimist", "short_description": "Bright outlook", "profile_text": "Always sees the good."},
                {"name": "The Analyst", "short_description": "Thinks deeply", "profile_text": "Loves data and logic."},
            ]
            self._store[thread_id] = s
        yield {"tick": 2}

    async def aget_state(self, config: dict) -> _FakeStateSnapshot:
        thread_id = str(config.get("configurable", {}).get("thread_id") or "thread")
        return _FakeStateSnapshot(values=self._store.get(thread_id, {}))

# ======================================================================================
# Session-scoped monkeypatch of app.lifespan hooks (no real DB/Redis; stub graph)
# ======================================================================================

@pytest.fixture(scope="session", autouse=True)
def _patch_startup_teardown() -> None:
    """
    Replace heavy startup hooks in main.py with no-ops and inject a fake agent graph.
    Avoids pytest's function-scoped monkeypatch to prevent scope mismatch.
    """
    from pytest import MonkeyPatch
    mp = MonkeyPatch()

    # No-op the heavy resource initializers (DB engine, Redis pool)
    mp.setattr(main_mod, "create_db_engine_and_session_maker", lambda *a, **k: None, raising=True)
    mp.setattr(main_mod, "create_redis_pool", lambda *a, **k: None, raising=True)

    # Provide a fake graph for lifespan to attach to app.state.agent_graph
    async def _fake_create_agent_graph():
        return _FakeAgentGraph()

    async def _fake_aclose_agent_graph(_graph) -> None:
        return None

    mp.setattr(main_mod, "create_agent_graph", _fake_create_agent_graph, raising=True)
    mp.setattr(main_mod, "aclose_agent_graph", _fake_aclose_agent_graph, raising=True)

    try:
        yield
    finally:
        mp.undo()

# ======================================================================================
# Minimal in-memory Redis (async) with pipeline + WATCH/MULTI/EXEC semantics
# ======================================================================================

try:
    from redis.exceptions import WatchError  # used by CacheRepository
except Exception:  # pragma: no cover - redis is in deps; this is belt & suspenders
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
        # acts like redis pipeline get under WATCH
        return self._parent._kv.get(key)

    def multi(self) -> None:
        self._in_multi = True

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        # queue the write; applied on execute()
        self._queued.append(("set", (key, value), {"ex": ex}))

    async def execute(self):
        # conflict detection
        if self._watched_key is not None:
            cur_ver = self._parent._versions.get(self._watched_key, 0)
            if self._watched_ver is None or cur_ver != self._watched_ver:
                # Someone else modified the key
                raise WatchError("Watched key modified")

        # apply queued ops
        for op, args, kwargs in self._queued:
            if op == "set":
                key, value = args
                # apply write
                self._parent._kv[key] = value
                self._parent._versions[key] = self._parent._versions.get(key, 0) + 1

        # reset multi/queue after exec
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
    """
    Very small async Redis clone:
      - get/set
      - pipeline() with watch/get/multi/set/execute/unwatch/reset
    TTL is accepted but ignored (sufficient for tests).
    """
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

# Expose the raw store for white-box assertions when desired
@pytest.fixture(scope="function")
def fake_cache_store() -> Dict[str, Any]:
    return {}

@pytest.fixture(scope="function")
def fake_redis(fake_cache_store: Dict[str, Any]) -> _FakeRedis:
    r = _FakeRedis()
    # Share the dict reference so tests can inspect keys (optional)
    r._kv = fake_cache_store  # type: ignore[attr-defined]
    return r

@pytest.fixture(autouse=True)
def reset_fake_cache(fake_cache_store: Dict[str, Any]):
    fake_cache_store.clear()
    yield
    fake_cache_store.clear()

# Override the DI provider for Redis for every test
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
# Turnstile bypass (always returns True)
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
# Safe even if your endpoints don't request a DB session today.
# ======================================================================================

# Only import SQLAlchemy when needed to avoid extra deps in very minimal test runs.
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import event
from app.models.db import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
_test_engine = create_async_engine(TEST_DATABASE_URL, future=True)
_TestingSessionLocal = async_sessionmaker(bind=_test_engine, expire_on_commit=False, autoflush=False, autocommit=False)

@asynccontextmanager
async def _test_session_ctx() -> AsyncGenerator[AsyncSession, None]:
    conn = await _test_engine.connect()
    created = False
    try:
        # SQLite will ignore PG-only types/indexes (pgvector); tolerate failures.
        await conn.run_sync(Base.metadata.create_all)
        created = True
    except Exception:
        created = False

    trans = await conn.begin()
    session = _TestingSessionLocal(bind=conn)

    # SAVEPOINT pattern so app-level commit() doesn't end isolation
    await session.begin_nested()

    @event.listens_for(session.sync_session, "after_transaction_end")
    def _restart_nested(sess, trans_):
        if trans_.nested and not trans_.connection.invalidated:
            sess.begin_nested()

    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        if created:
            try:
                await conn.run_sync(Base.metadata.drop_all)
            except Exception:
                pass
        await conn.close()

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
# HTTP client bound to the FastAPI app with lifespan events enabled
# ======================================================================================

@pytest_asyncio.fixture(scope="function")
async def client(_override_db_dep) -> AsyncGenerator[AsyncClient, None]:
    """
    httpx AsyncClient configured to route to our app in-process.
    Lifespan is enabled so startup/shutdown (with our patches) run per test.
    """
    transport = ASGITransport(app=fastapi_app, lifespan="on")
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

# --------------------------------------------------------------------------------------
# Safety: clear any straggling dependency overrides automatically
# --------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    fastapi_app.dependency_overrides.clear()
