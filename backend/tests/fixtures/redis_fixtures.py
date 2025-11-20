# backend/tests/fixtures/redis_fixtures.py
"""
In-memory async Redis fixtures with minimal WATCH/MULTI/EXEC semantics.
Updated to handle complex serialization (UUIDs, Pydantic models) in seed_quiz_state.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pytest
from fastapi.encoders import jsonable_encoder  # NEW: For robust serialization

# Ensure `backend/` is importable
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.api.dependencies import get_redis_client, verify_turnstile

try:
    from redis.exceptions import WatchError
except Exception:
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

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                deleted += 1
        return deleted

    async def flushdb(self) -> bool:
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
    r._kv = fake_cache_store
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
    Override the app's Redis dependency.
    """
    from app.main import app as fastapi_app

    async def _dep() -> _FakeRedis:
        return fake_redis

    fastapi_app.dependency_overrides[get_redis_client] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_redis_client, None)

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def seed_quiz_state(fake_redis: _FakeRedis, session_id: uuid.UUID, state: Dict[str, Any]) -> None:
    """
    Seeds the fake Redis with a quiz state.
    Uses jsonable_encoder to handle UUIDs and Pydantic models (like HumanMessage) 
    before JSON serialization.
    """
    key = f"quiz_session:{session_id}"
    
    # Safe serialization of UUIDs and Pydantic models inside the dict
    safe_state = jsonable_encoder(state)
    text = json.dumps(safe_state)
    
    fake_redis._kv[key] = text
    fake_redis._versions[key] = fake_redis._versions.get(key, 0) + 1

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