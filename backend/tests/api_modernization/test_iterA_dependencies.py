"""Iter A — fix double-log in close_redis_pool and drop unused async.

Two issues in app/api/dependencies.py:

1. ``close_redis_pool`` logs ``"Redis pool disconnected."`` twice on
   success: once in the ``try`` block right after ``aclose()`` and once
   in the ``else`` clause. The duplicated record clutters logs and gives
   the misleading impression of two separate close events.

2. ``get_redis_client`` is declared ``async`` but performs no ``await``
   work. It also re-imports ``redis.asyncio`` and several exception
   classes on every call. Hoist imports and rename without losing the
   FastAPI dependency contract (FastAPI accepts both sync and async
   dependency callables).
"""

from __future__ import annotations

import inspect

import pytest


@pytest.mark.asyncio
async def test_close_redis_pool_logs_success_once(monkeypatch, caplog) -> None:
    import logging

    from app.api import dependencies as deps

    class _FakePool:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(deps, "redis_pool", _FakePool())
    caplog.set_level(logging.INFO, logger="app.api.dependencies")

    await deps.close_redis_pool()

    msgs = [
        rec.message
        for rec in caplog.records
        if "disconnect" in rec.message.lower()
    ]
    assert len(msgs) == 1, (
        f"Expected exactly one disconnect log on success, got {len(msgs)}: {msgs}"
    )
    assert deps.redis_pool is None


@pytest.mark.asyncio
async def test_close_redis_pool_clears_pool_on_failure(monkeypatch, caplog) -> None:
    import logging

    from app.api import dependencies as deps

    class _FakePool:
        async def aclose(self) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(deps, "redis_pool", _FakePool())
    caplog.set_level(logging.WARNING, logger="app.api.dependencies")

    await deps.close_redis_pool()
    # Pool reference must be cleared regardless of close outcome.
    assert deps.redis_pool is None
    assert any("disconnect failed" in rec.message.lower() for rec in caplog.records)


def test_get_redis_client_is_synchronous() -> None:
    """get_redis_client does no await work — must not be an async function.

    FastAPI accepts both sync and async dependency callables, so this is a
    pure best-practice fix that also avoids needless event-loop overhead.
    """
    from app.api import dependencies as deps

    assert not inspect.iscoroutinefunction(deps.get_redis_client), (
        "get_redis_client performs no await work; declare it as a regular "
        "synchronous function."
    )


def test_get_redis_client_imports_are_module_level() -> None:
    """The redis-asyncio + retry imports should live at module scope, not
    inside the dependency callable that runs per-request.
    """
    import ast
    import pathlib

    from app.api import dependencies as deps

    src = pathlib.Path(deps.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Walk into the function body for get_redis_client.
    target = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "get_redis_client"
    )
    inner_imports = [
        n
        for n in ast.walk(target)
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    assert not inner_imports, (
        f"get_redis_client must not import inside the function body; found: "
        f"{[ast.dump(n) for n in inner_imports]}"
    )
