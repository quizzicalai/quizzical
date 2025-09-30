# tests/fixtures/redis_fixtures.py
"""
In-memory async Redis fixtures with minimal WATCH/MULTI/EXEC semantics.

What this provides:
- fake_cache_store: dict shared with the fake client for white-box assertions.
- fake_redis: the async client instance.
- override_redis_dep: dependency override for FastAPI `get_redis_client`.
- seed_quiz_state: helper to store an AgentGraphStateModel-like blob under
  the same key format the app uses (`quiz_session:{uuid}`).

Notes:
- TTLs are accepted but ignored (sufficient for unit/integration tests).
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pytest

# Ensure `backend/` is importable
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.api.dependencies import get_redis_client, verify_turnstile  # type: ignore

try:
    from redis.exceptions import WatchError  # pragma: no cover
except Exception:  # pragma: no cover
    class WatchError(RuntimeError):
        """Raised when a watched key changes before EXEC."""
        pass

# --------------------------------------------------------------------------------------
# Fake Redis + Pipeline
# --------------------------------------------------------------------------------------

class _FakePipeline:
    def __init__(self, parent: "_FakeRedis") -> None:
        self._parent = parent
        self._watched_key: Optional[str] = None
        self._watched_ver: Optional[int] = None
        self._queued: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []
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

    def multi(self) -> None:
        self._in_multi = True

    async def get(self, key: str) -> Optional[Union[str, bytes]]:
        return self._parent._kv.get(key)

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        self._queued.append(("set", (key, value), {"ex": ex}))

    async def execute(self) -> bool:
        if self._watched_key is not None:
            cur_ver = self._parent._versions.get(self._watched_key, 0)
            if self._watched_ver is None or cur_ver != self._watched_ver:
                raise WatchError("Watched key modified")

        for op, args, kwargs in self._queued:
            if op == "set":
                key, value = args
                self._parent._kv[key] = value
                self._parent._versions[key] = self._parent._versions.get(key, 0) + 1

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
    def __init__(self) -> None:
        self._kv: Dict[str, Any] = {}
        self._versions: Dict[str, int] = {}

    async def get(self, key: str) -> Optional[Union[str, bytes]]:
        return self._kv.get(key)

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        self._kv[key] = value
        self._versions[key] = self._versions.get(key, 0) + 1
        return True

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def delete(self, *keys: str) -> int:  # pragma: no cover
        deleted = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                deleted += 1
        return deleted

    async def flushdb(self) -> bool:  # pragma: no cover
        self._kv.clear()
        self._versions.clear()
        return True


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="function")
def fake_cache_store() -> Dict[str, Any]:
    return {}

@pytest.fixture(scope="function")
def fake_redis(fake_cache_store: Dict[str, Any]) -> _FakeRedis:
    r = _FakeRedis()
    r._kv = fake_cache_store  # share store for assertions
    return r

@pytest.fixture(scope="function")
def reset_fake_cache_store(fake_cache_store: Dict[str, Any]):
    fake_cache_store.clear()
    try:
        yield
    finally:
        fake_cache_store.clear()

@pytest.fixture(scope="function")
def override_redis_dep(fake_redis: _FakeRedis):
    """
    Override the app's Redis dependency + Turnstile check.
    """
    from app.main import app as fastapi_app  # local import to avoid import-time side effects

    async def _dep() -> _FakeRedis:
        return fake_redis

    async def _ok(*_a, **_k) -> bool:
        return True

    fastapi_app.dependency_overrides[get_redis_client] = _dep
    fastapi_app.dependency_overrides[verify_turnstile] = _ok

    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_redis_client, None)
        fastapi_app.dependency_overrides.pop(verify_turnstile, None)

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def seed_quiz_state(fake_redis: _FakeRedis, session_id: uuid.UUID, state: Dict[str, Any]) -> None:
    key = f"quiz_session:{session_id}"
    text = json.dumps(state)
    fake_redis._kv[key] = text  # type: ignore[attr-defined]
    fake_redis._versions[key] = fake_redis._versions.get(key, 0) + 1  # type: ignore[attr-defined]

__all__ = [
    "_FakeRedis",
    "_FakePipeline",
    "WatchError",
    "fake_cache_store",
    "fake_redis",
    "reset_fake_cache_store",
    "override_redis_dep",
    "seed_quiz_state",
]
