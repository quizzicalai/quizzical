"""Hitlist #1 — recovery loop must bound re-spend even when a re-run dies BEFORE
``mark_running`` (degraded Redis / malformed state blob / transient DB).

Before the fix, ``claim_stale`` bumped only the heartbeat and ``attempts`` was
incremented later inside the re-run's ``mark_running``. A ``_recover_one`` that
raised during the Redis state-load (before ``mark_running``) left ``attempts``
flat while freshly bumping the heartbeat, so the row stayed ``running`` and was
re-claimed every ``stale_after_s`` FOREVER — an infinite re-claim + cost-bleed.

The fix bumps ``attempts`` AT CLAIM TIME inside ``claim_stale`` and wraps the
state-load so a load fault can't raise past the bump. These tests prove the
recovery loop now TERMINATES (job marked failed) after ``max_attempts`` sweeps
instead of looping forever.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import tests.fixtures.db_fixtures  # noqa: F401  (registers sqlite compat shims)
from app.api import dependencies as deps
from app.models.db import QuizJob
from app.services import agent_recovery as ar
from app.services.database import QuizJobRepository

pytestmark = pytest.mark.anyio


@pytest_asyncio.fixture
async def jobs_db(tmp_path: Path, monkeypatch):
    """Real file-backed sqlite engine carrying only the quiz_jobs table, wired as
    the recovery sweeper's session factory."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}"
    engine = create_async_engine(url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(QuizJob.__table__.create)
    monkeypatch.setattr(deps, "async_session_factory", factory, raising=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_stale_running(factory, qid):
    async with factory() as s:
        await QuizJobRepository(s).mark_running(qid)  # attempts -> 1
        await s.commit()
    async with factory() as s:
        job = await s.get(QuizJob, qid)
        job.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=999)
        await s.commit()


def _app():
    from types import SimpleNamespace
    return SimpleNamespace(state=SimpleNamespace(agent_graph=object()))


async def test_state_load_failure_is_failed_out_after_max_attempts(
    jobs_db, monkeypatch
):
    """A _recover_one whose Redis state-load RAISES every sweep must be marked
    failed after max_attempts (not re-claimed forever)."""
    monkeypatch.setattr(ar.settings.security.agent_recovery, "enabled", True, raising=False)
    monkeypatch.setattr(ar.settings.security.agent_recovery, "stale_after_s", 180, raising=False)
    monkeypatch.setattr(ar.settings.security.agent_recovery, "max_attempts", 3, raising=False)
    monkeypatch.setattr(ar.settings.security.agent_recovery, "batch", 5, raising=False)
    monkeypatch.setattr(deps, "get_redis_client", lambda: object(), raising=False)

    # Redis state-load raises on EVERY recovery attempt (degraded Redis).
    # _recover_one imports CacheRepository from app.services.redis_cache lazily,
    # so patch it at the source module.
    class _ExplodingCache:
        def __init__(self, _redis):
            pass

        async def get_quiz_state(self, _qid):
            raise RuntimeError("redis blob corrupt")

    monkeypatch.setattr(
        "app.services.redis_cache.CacheRepository", _ExplodingCache, raising=True
    )

    # No DB snapshot either -> _recover_one marks failed / lets attempts climb.
    async def _no_db_state(_db, _qid):
        return None

    monkeypatch.setattr(
        "app.api.endpoints.quiz._rehydrate_state_from_db", _no_db_state, raising=True
    )

    qid = uuid.uuid4()
    await _seed_stale_running(jobs_db, qid)

    # Drive several sweeps. Each sweep: fail_exhausted -> claim_stale (attempts++)
    # -> _recover_one (state-load raises, no DB state -> mark_failed). The loop
    # must terminate with the row 'failed' and never re-spend forever.
    statuses = []
    for _ in range(6):
        # Re-stale the heartbeat so the next sweep would re-claim a still-running
        # row (proves the loop does NOT spin forever).
        async with jobs_db() as s:
            job = await s.get(QuizJob, qid)
            if job.status == "running":
                job.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=999)
                await s.commit()
        await ar.sweep_once(_app())
        async with jobs_db() as s:
            statuses.append((await s.get(QuizJob, qid)).status)

    assert statuses[-1] == "failed", f"recovery must terminate, got {statuses}"


async def test_claim_stale_increments_attempts_at_claim_time(jobs_db):
    """Unit-level proof of the core fix: claim_stale bumps attempts so the
    counter advances even if the re-run never reaches mark_running."""
    qid = uuid.uuid4()
    await _seed_stale_running(jobs_db, qid)  # attempts == 1

    async with jobs_db() as s:
        repo = QuizJobRepository(s)
        claimed = await repo.claim_stale(stale_after_s=180, max_attempts=3, limit=10)
        await s.commit()
    assert claimed == [qid]

    async with jobs_db() as s:
        job = await s.get(QuizJob, qid)
    assert job.attempts == 2, "claim_stale must increment attempts at claim time"


async def test_full_sweep_loop_marks_failed_via_fail_exhausted(jobs_db, monkeypatch):
    """End-to-end via fail_exhausted: a row whose attempts climb to max_attempts
    through repeated claims (re-runs dying pre-mark_running) is failed-out by the
    next sweep's fail_exhausted, terminating the loop."""
    monkeypatch.setattr(ar.settings.security.agent_recovery, "enabled", True, raising=False)
    monkeypatch.setattr(ar.settings.security.agent_recovery, "stale_after_s", 180, raising=False)
    monkeypatch.setattr(ar.settings.security.agent_recovery, "max_attempts", 3, raising=False)
    monkeypatch.setattr(deps, "get_redis_client", lambda: object(), raising=False)

    # _recover_one is a no-op that does NOT reach mark_running and does NOT mark
    # the row terminal (simulates a re-run dying before any status write). The
    # only thing advancing attempts is claim_stale's bump.
    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(ar, "_recover_one", _noop, raising=True)

    qid = uuid.uuid4()
    await _seed_stale_running(jobs_db, qid)  # attempts == 1

    for _ in range(6):
        async with jobs_db() as s:
            job = await s.get(QuizJob, qid)
            if job is not None and job.status == "running":
                job.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=999)
                await s.commit()
        await ar.sweep_once(_app())

    async with jobs_db() as s:
        job = await s.get(QuizJob, qid)
    assert job.status == "failed", "attempts climbing via claim must trip fail_exhausted"
    assert job.attempts >= 3
