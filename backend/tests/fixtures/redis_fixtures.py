"""
In-memory async Redis fixtures with minimal WATCH/MULTI/EXEC semantics.

Why this exists:
- Your CacheRepository uses:
    - client.get / client.set
    - client.pipeline().__aenter__/watch/get/multi/set/execute/unwatch/reset
- This fake client supports exactly those methods and behaves deterministically.
- TTLs are accepted but ignored — that’s fine for unit tests.

What this provides:
- fake_cache_store: dict shared with the fake client for white-box assertions.
- fake_redis: the async client instance.
- override_redis_dep: dependency override for FastAPI `get_redis_client`.
- seed_quiz_state: helper to store an AgentGraphStateModel-like blob under
  the same key format the app uses (`quiz_session:{uuid}`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid

import pytest

# Ensure `backend/` is importable
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Import pieces only when needed
from app.api.dependencies import get_redis_client  # type: ignore

try:
    from redis.exceptions import WatchError  # pragma: no cover - comes from redis-py
except Exception:  # pragma: no cover - fallback if redis isn't installed
    class WatchError(RuntimeError):
        pass


class _FakePipeline:
    """
    A very small subset of a Redis pipeline required for optimistic concurrency tests.
    """
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
        # Fail if watched key has changed
        if self._watched_key is not None:
            cur_ver = self._parent._versions.get(self._watched_key, 0)
            if self._watched_ver is None or cur_ver != self._watched_ver:
                raise WatchError("Watched key modified")

        # Apply queued writes
        for op, args, kwargs in self._queued:
            if op == "set":
                key, value = args
                self._parent._kv[key] = value
                self._parent._versions[key] = self._parent._versions.get(key, 0) + 1

        # Reset after exec
        await self.unwatch()
        self._queued.clear()
        self._in_multi = False
        return True

    def reset(self) -> None:
        self._queued.clear()
        self._in_multi = False
        self._watched_key = None
        self._watched_ver = None


class _FakeRedis:
    """
    Async Redis lookalike with minimal surface for CacheRepository.
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


# ------------------------
# Fixtures
# ------------------------

@pytest.fixture(scope="function")
def fake_cache_store() -> Dict[str, Any]:
    """
    A shared dict used internally by _FakeRedis for white-box assertions.
    Resets automatically around each test by the `reset_fake_cache_store` helper.
    """
    return {}


@pytest.fixture(scope="function")
def fake_redis(fake_cache_store: Dict[str, Any]) -> _FakeRedis:
    """
    The fake Redis client. Shares its KV store with `fake_cache_store`.
    """
    r = _FakeRedis()
    # Share store for test observability
    r._kv = fake_cache_store  # type: ignore[attr-defined]
    return r


@pytest.fixture(scope="function")
def reset_fake_cache_store(fake_cache_store: Dict[str, Any]):
    """
    Clears the fake cache store before and after a test for isolation.
    """
    fake_cache_store.clear()
    yield
    fake_cache_store.clear()


@pytest.fixture(scope="function")
def override_redis_dep(fake_redis: _FakeRedis):
    """
    Overrides the FastAPI `get_redis_client` dependency to return our fake client.
    Opt-in: include this fixture in a test (or higher-level conftest) to activate.
    """
    # Lazy import to avoid side effects at import time
    from app.main import app as fastapi_app  # type: ignore

    async def _dep():
        return fake_redis

    fastapi_app.dependency_overrides[get_redis_client] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_redis_client, None)


# ------------------------
# Helpers
# ------------------------

def seed_quiz_state(fake_redis: _FakeRedis, session_id: uuid.UUID, state: Dict[str, Any]) -> None:
    """
    Store a pre-canned quiz session state under the exact key format used by the app.

    The app's CacheRepository expects JSON-encoded AgentGraphStateModel payloads.
    In unit tests, we commonly avoid importing models and pass a dict that is
    already compatible with the schema the repository validates against.

    Args:
        fake_redis: The fake redis client fixture.
        session_id: The quiz UUID.
        state: A JSON-serializable dict resembling AgentGraphStateModel.
    """
    key = f"quiz_session:{session_id}"
    # Just store JSON text; CacheRepository will consume it with model_validate_json in app code.
    text = json.dumps(state)
    # We can set directly because _FakeRedis.set is async; use loop via pytest if needed.
    # For convenience, directly mutate the KV (consistent with .set implementation).
    fake_redis._kv[key] = text  # type: ignore[attr-defined]
    fake_redis._versions[key] = fake_redis._versions.get(key, 0) + 1  # type: ignore[attr-defined]
