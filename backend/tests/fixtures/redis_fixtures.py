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

Notes:
- We intentionally **ignore TTLs**. If a future test needs TTL behavior, we can simulate
  expiration by directly mutating `fake_cache_store`.
- Keys/values are stored exactly as your code writes them (often JSON strings).
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

# Imported lazily in override_redis_dep, but the type is useful for clarity
from app.api.dependencies import get_redis_client, verify_turnstile  # type: ignore

try:
    # Provided by redis-py when installed
    from redis.exceptions import WatchError  # pragma: no cover
except Exception:  # pragma: no cover - fallback if redis isn't installed
    class WatchError(RuntimeError):
        """Raised when a watched key changes before EXEC."""
        pass


# --------------------------------------------------------------------------------------
# Fake Redis + Pipeline
# --------------------------------------------------------------------------------------

class _FakePipeline:
    """
    Minimal async pipeline for optimistic concurrency.

    Supported surface:
      - async context manager: __aenter__/__aexit__
      - watch(key) / unwatch()
      - get(key)  (read during watch; mirrors redis-py's pattern used by repos)
      - multi()
      - set(key, value, ex=None)
      - execute()
      - reset()

    Behavior:
      - We track a simple per-key version in the parent client; if the watched key's
        version changes between `watch()` and `execute()`, we raise WatchError.
    """

    def __init__(self, parent: "_FakeRedis") -> None:
        self._parent = parent
        self._watched_key: Optional[str] = None
        self._watched_ver: Optional[int] = None
        self._queued: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []
        self._in_multi = False

    # ---- context mgmt --------------------------------------------------------

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Reset regardless of success/failure to avoid bleeding state across tests
        self.reset()

    # ---- watch/multi ---------------------------------------------------------

    async def watch(self, key: str) -> None:
        self._watched_key = key
        self._watched_ver = self._parent._versions.get(key, 0)

    async def unwatch(self) -> None:
        self._watched_key = None
        self._watched_ver = None

    def multi(self) -> None:
        self._in_multi = True

    # ---- ops (subset) --------------------------------------------------------

    async def get(self, key: str) -> Optional[Union[str, bytes]]:
        # Reads are direct from the parent store
        return self._parent._kv.get(key)

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        # Queue a write for execution under MULTI/EXEC
        self._queued.append(("set", (key, value), {"ex": ex}))

    # ---- exec/reset ----------------------------------------------------------

    async def execute(self) -> bool:
        # Abort if watched key changed
        if self._watched_key is not None:
            cur_ver = self._parent._versions.get(self._watched_key, 0)
            if self._watched_ver is None or cur_ver != self._watched_ver:
                raise WatchError("Watched key modified")

        # Apply queued writes
        for op, args, kwargs in self._queued:
            if op == "set":
                key, value = args
                # TTL ignored intentionally (kwargs['ex'])
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
    Async Redis lookalike with a tiny surface area sufficient for cache tests.

    Supported methods:
      - get(key)
      - set(key, value, ex=None)
      - pipeline() -> _FakePipeline

    Implementation details:
      - `_kv` is the underlying key/value store.
      - `_versions` tracks per-key versions to emulate WATCH consistency checks.
      - Values are stored as-is (commonly JSON strings produced by the app layer).
    """

    def __init__(self) -> None:
        self._kv: Dict[str, Any] = {}
        self._versions: Dict[str, int] = {}

    async def get(self, key: str) -> Optional[Union[str, bytes]]:
        return self._kv.get(key)

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        self._kv[key] = value
        self._versions[key] = self._versions.get(key, 0) + 1
        # TTL `ex` intentionally ignored
        return True

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    # Handy extras that won't break anything (kept minimal and optional)
    async def delete(self, *keys: str) -> int:  # pragma: no cover (unused today)
        deleted = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                deleted += 1
        return deleted

    async def flushdb(self) -> bool:  # pragma: no cover (unused today)
        self._kv.clear()
        self._versions.clear()
        return True


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="function")
def fake_cache_store() -> Dict[str, Any]:
    """
    A dict backing store used by the fake client. Tests can assert on its contents
    directly without going through the client.
    """
    return {}


@pytest.fixture(scope="function")
def fake_redis(fake_cache_store: Dict[str, Any]) -> _FakeRedis:
    """
    A fake Redis client instance that shares its internal KV with `fake_cache_store`.
    """
    r = _FakeRedis()
    # Share store for white-box assertions:
    r._kv = fake_cache_store  # type: ignore[attr-defined]
    return r


@pytest.fixture(scope="function")
def reset_fake_cache_store(fake_cache_store: Dict[str, Any]):
    """
    Clears the fake cache store before and after a test for isolation.
    """
    fake_cache_store.clear()
    try:
        yield
    finally:
        fake_cache_store.clear()


@pytest.fixture(scope="function")
def override_redis_dep(fake_redis: _FakeRedis):
    """
    Overrides FastAPI `get_redis_client` to return our fake client.
    Opt-in: include this fixture in a test (or higher-level conftest) to activate.
    """
    # Lazy import to avoid side effects at import time
    from app.main import app as fastapi_app  # type: ignore

    async def _dep() -> _FakeRedis:
        return fake_redis
    
    async def _ok(*_a, **_k) -> bool:
        # Turnstile always passes in tests that opt into this override
        return True

    fastapi_app.dependency_overrides[get_redis_client] = _dep
    fastapi_app.dependency_overrides[verify_turnstile] = _ok

    try:
        yield
    finally:
        # Remove just our override to play nicely with other tests
        fastapi_app.dependency_overrides.pop(get_redis_client, None)
        fastapi_app.dependency_overrides.pop(verify_turnstile, None)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def seed_quiz_state(fake_redis: _FakeRedis, session_id: uuid.UUID, state: Dict[str, Any]) -> None:
    """
    Store a pre-canned quiz session state under the exact key format used by the app.

    The app's CacheRepository expects JSON-encoded AgentGraphStateModel payloads.
    In tests, we commonly pass a dict that the repository will validate/consume.

    Args:
        fake_redis: The fake redis client fixture.
        session_id: The quiz UUID.
        state: A JSON-serializable dict resembling AgentGraphStateModel.
    """
    key = f"quiz_session:{session_id}"
    text = json.dumps(state)
    # Directly mutate the KV store (equivalent to await fake_redis.set(...), but sync-friendly)
    fake_redis._kv[key] = text  # type: ignore[attr-defined]
    fake_redis._versions[key] = fake_redis._versions.get(key, 0) + 1  # type: ignore[attr-defined]


__all__ = [
    # classes (private by convention, but exported for power users)
    "_FakeRedis",
    "_FakePipeline",
    "WatchError",
    # fixtures
    "fake_cache_store",
    "fake_redis",
    "reset_fake_cache_store",
    "override_redis_dep",
    # helpers
    "seed_quiz_state",
]
