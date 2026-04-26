"""Iter F — get_db_session must rollback on unhandled exceptions.

The current dependency in ``app/api/dependencies.py`` is::

    async with async_session_factory() as session:
        yield session

``AsyncSession.__aexit__`` only calls ``close()``, not ``rollback()``. If
a downstream endpoint raises *after* issuing writes but *before*
committing (and without its own try/except wrapping ``db.rollback()``),
SQLAlchemy emits a ``GarbageCollectorRollback`` warning and the
transaction state is implementation-defined. Worse, when used with a
real PostgreSQL pool this can leak row-level locks until the connection
is recycled.

Standard FastAPI + SQLAlchemy idiom: explicitly roll back on any
exception that escapes the ``yield``::

    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_get_db_session_rolls_back_on_exception(monkeypatch) -> None:
    from app.api import dependencies as deps

    rollback_calls: list[bool] = []
    close_calls: list[bool] = []

    class _SpySession:
        async def __aenter__(self) -> "_SpySession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            close_calls.append(True)
            return None

        async def rollback(self) -> None:
            rollback_calls.append(True)

    def _factory() -> _SpySession:
        return _SpySession()

    monkeypatch.setattr(deps, "async_session_factory", _factory)

    gen = deps.get_db_session()
    session = await gen.__anext__()
    assert isinstance(session, _SpySession)

    # Simulate the endpoint raising an unexpected error mid-request.
    with pytest.raises(RuntimeError, match="boom"):
        await gen.athrow(RuntimeError("boom"))

    assert rollback_calls == [True], (
        "get_db_session must call session.rollback() when an exception "
        f"escapes the yielded context; got rollback_calls={rollback_calls!r}"
    )
    assert close_calls == [True], (
        "session must still be closed via __aexit__ after rollback"
    )


@pytest.mark.asyncio
async def test_get_db_session_does_not_rollback_on_clean_exit(monkeypatch) -> None:
    from app.api import dependencies as deps

    rollback_calls: list[bool] = []

    class _SpySession:
        async def __aenter__(self) -> "_SpySession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def rollback(self) -> None:
            rollback_calls.append(True)

    monkeypatch.setattr(deps, "async_session_factory", lambda: _SpySession())

    gen = deps.get_db_session()
    await gen.__anext__()
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    assert rollback_calls == [], (
        f"clean exit must not rollback; got rollback_calls={rollback_calls!r}"
    )


@pytest.mark.asyncio
async def test_get_db_session_works_end_to_end_with_real_sqlite() -> None:
    """Integration: the rollback hook must not break the happy path against
    a real (in-memory) AsyncSession + engine."""
    from app.api import dependencies as deps

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    original_factory = deps.async_session_factory
    deps.async_session_factory = factory  # type: ignore[assignment]
    try:
        gen = deps.get_db_session()
        session = await gen.__anext__()
        # Trivial query proves the session is usable.
        from sqlalchemy import text

        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
    finally:
        deps.async_session_factory = original_factory
        await engine.dispose()
