"""Durable-jobs / crash-recovery hardening (audit P1, branch feat/durable-jobs-hardening).

These tests exercise the previously-untested centerpiece of the reliability
remediation end-to-end:

1. HEARTBEAT is emitted during a run and cancelled at the end (no concurrent
   re-claim of a slow-but-alive run).
2. claim_stale grants a stale row exactly once (repo-level proof lives in
   test_quiz_job_repo.py; here we prove the runner marks the row so the sweeper
   has something to claim).
3. Recovery SKIPS an already-finalized quiz (run_agent_in_background +
   _recover_one short-circuit instead of re-finalizing with fresh paid calls).
4. The quiz_jobs ROW exists for the crash-before-first-write window (the bg
   runner's mark_running, and the handler's synchronous pre-schedule mark).
5. get_quiz_status surfaces a terminal FAILED job instead of polling forever.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Importing the shared db fixtures registers the SQLite compat shims
# (PGUUID -> TEXT etc.) needed for QuizJob.__table__.create to compile.
import tests.fixtures.db_fixtures  # noqa: F401
from app.api import dependencies as deps
from app.api.endpoints import quiz as quiz_mod
from app.models.db import QuizJob
from app.services.database import QuizJobRepository
from app.services.redis_cache import CacheRepository

pytestmark = pytest.mark.anyio


@pytest_asyncio.fixture
async def bg_db(tmp_path: Path, monkeypatch):
    """A dedicated file-backed sqlite engine for the background runner.

    The background runner spawns a concurrent heartbeat task and each
    ``_quiz_job_update`` / ``_persist_*`` helper opens + commits its OWN session.
    The shared ``sqlite_db_session`` fixture wraps everything in one savepoint on
    one StaticPool connection, which cannot service those concurrent, separately-
    committing sessions. A real file-backed engine (one connection per session)
    mirrors production semantics and lets the durable writes actually land.

    We patch BOTH the hand-pumped ``get_db_session`` (used by _quiz_job_update /
    _persist_*) and ``deps.async_session_factory`` (used by _recover_one /
    _load_state_with_final_result) to this engine.
    """
    url = f"sqlite+aiosqlite:///{tmp_path / 'bg.db'}"
    engine = create_async_engine(url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    # Create only the quiz_jobs table — the runner's other persistence
    # (_persist_*) and the recovery DB-rehydrate are not reached in these tests
    # (Redis always holds the live state), and the full schema carries
    # Postgres-only ``::jsonb`` server-defaults that don't compile on a plain
    # sqlite engine (the shared fixture installs a sanitizing event hook; this
    # dedicated engine deliberately does not).
    async with engine.begin() as conn:
        await conn.run_sync(QuizJob.__table__.create)

    async def _gen():
        async with factory() as session:
            yield session

    monkeypatch.setattr(quiz_mod, "get_db_session", _gen, raising=True)
    monkeypatch.setattr(deps, "async_session_factory", factory, raising=False)

    try:
        yield factory
    finally:
        await engine.dispose()


def _seed_state(category="Cats", *, final_result=None):
    qid = uuid.uuid4()
    state = quiz_mod._build_initial_graph_state(qid, str(uuid.uuid4()), category)
    state["ready_for_questions"] = True
    if final_result is not None:
        state["final_result"] = final_result
    return qid, state


# ===========================================================================
# Hole #4 + #1 + #2 — runner marks the durable row running, heartbeats, then
# succeeds. The "running" row is exactly what the sweeper claims; without it a
# crash-before-first-write strands the quiz.
# ===========================================================================
async def _get_job(factory, qid):
    async with factory() as s:
        return await s.get(QuizJob, qid)


async def test_run_agent_marks_job_running_then_succeeded_and_heartbeats(
    bg_db, fake_redis, monkeypatch
):
    qid, state = _seed_state()
    await CacheRepository(fake_redis).save_quiz_state(state)

    # Force a fast heartbeat cadence and capture that the loop actually fired.
    monkeypatch.setattr(quiz_mod, "_heartbeat_interval_s", lambda: 0.01)
    beats: list = []
    real_hb = QuizJobRepository.heartbeat

    async def _spy_hb(self, quiz_id):
        beats.append(quiz_id)
        return await real_hb(self, quiz_id)

    monkeypatch.setattr(QuizJobRepository, "heartbeat", _spy_hb, raising=True)

    # A graph whose stream is slow enough for >=1 heartbeat to fire mid-run.
    from tests.fixtures.agent_graph_fixtures import FakeAgentGraph

    graph = FakeAgentGraph()
    orig_astream = graph.astream

    async def _slow_astream(s, config):
        async for tick in orig_astream(s, config):
            await asyncio.sleep(0.03)
            yield tick

    graph.astream = _slow_astream  # type: ignore[assignment]

    await quiz_mod.run_agent_in_background(state, fake_redis, graph)

    job = await _get_job(bg_db, qid)
    assert job is not None, "the durable row must exist for the sweeper to claim"
    assert job.status == "succeeded"
    assert job.attempts == 1
    assert beats, "the heartbeat loop must emit at least one beat during the run"


async def test_run_agent_marks_job_failed_on_stream_error(bg_db, fake_redis):
    qid, state = _seed_state()
    await CacheRepository(fake_redis).save_quiz_state(state)

    class _BoomGraph:
        async def astream(self, s, config):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover

        async def aget_state(self, config):  # pragma: no cover - not reached
            raise AssertionError

    await quiz_mod.run_agent_in_background(state, fake_redis, _BoomGraph())

    job = await _get_job(bg_db, qid)
    assert job is not None
    assert job.status == "failed"  # deterministic failure -> not auto-retried
    assert "kaboom" in (job.last_error or "")


# ===========================================================================
# Hole #3 — recovery / re-run SKIPS an already-finalized quiz.
# ===========================================================================
async def _mark_running(factory, qid):
    async with factory() as s:
        await QuizJobRepository(s).mark_running(qid)
        await s.commit()


async def test_run_agent_skips_when_already_finalized(bg_db, fake_redis):
    """A re-run of a quiz whose state already has final_result must NOT re-stream
    the graph (which would re-finalize with fresh paid calls + overwrite the
    user's result). It marks the job succeeded and returns."""
    final = {"title": "You are a Cat", "description": "Independent.", "image_url": ""}
    qid, state = _seed_state(final_result=final)
    await CacheRepository(fake_redis).save_quiz_state(state)
    # Pre-create a "running" row (as if the original run crashed before succeeded).
    await _mark_running(bg_db, qid)

    streamed = {"n": 0}

    class _SpyGraph:
        async def astream(self, s, config):
            streamed["n"] += 1
            yield {"tick": 1}

        async def aget_state(self, config):  # pragma: no cover
            raise AssertionError

    await quiz_mod.run_agent_in_background(state, fake_redis, _SpyGraph())

    assert streamed["n"] == 0, "must not re-stream an already-finalized quiz"
    job = await _get_job(bg_db, qid)
    assert job.status == "succeeded"


async def test_recover_one_skips_already_finalized(bg_db, fake_redis, monkeypatch):
    """_recover_one short-circuits a finalized quiz: marks succeeded, never calls
    run_agent_in_background."""
    final = {"title": "Done", "description": "Already finished.", "image_url": ""}
    qid, state = _seed_state(final_result=final)
    await CacheRepository(fake_redis).save_quiz_state(state)
    await _mark_running(bg_db, qid)

    ran = {"n": 0}

    async def _should_not_run(*_a, **_k):
        ran["n"] += 1

    monkeypatch.setattr(quiz_mod, "run_agent_in_background", _should_not_run, raising=True)

    from app.services import agent_recovery as ar

    await ar._recover_one(qid, object(), fake_redis)

    assert ran["n"] == 0, "recovery must not re-run a finalized quiz"
    job = await _get_job(bg_db, qid)
    assert job.status == "succeeded"


async def test_recover_one_reruns_unfinished(bg_db, fake_redis, monkeypatch):
    """Sanity: a genuinely-unfinished quiz IS re-run (the short-circuit is not
    over-broad)."""
    qid, state = _seed_state()  # no final_result
    await CacheRepository(fake_redis).save_quiz_state(state)

    reran = {"n": 0}

    async def _rerun(passed_state, redis_client, agent_graph):
        reran["n"] += 1

    monkeypatch.setattr(quiz_mod, "run_agent_in_background", _rerun, raising=True)

    from app.services import agent_recovery as ar

    await ar._recover_one(qid, object(), fake_redis)
    assert reran["n"] == 1


# ===========================================================================
# Hole #5 — get_quiz_status surfaces a terminal failed job (no infinite poll).
# ===========================================================================
async def test_status_surfaces_failed_job_as_terminal(
    sqlite_db_session, fake_redis, monkeypatch
):
    from fastapi import HTTPException

    qid, state = _seed_state()
    # State has no questions yet and no final_result -> would normally be
    # "processing". With a FAILED job row it must instead be terminal.
    state["generated_questions"] = []
    await CacheRepository(fake_redis).save_quiz_state(state)
    repo = QuizJobRepository(sqlite_db_session)
    await repo.mark_running(qid)  # row must exist; mark_failed is an UPDATE
    await repo.mark_failed(qid, "deterministic boom")
    await sqlite_db_session.commit()

    with pytest.raises(HTTPException) as ei:
        await quiz_mod.get_quiz_status(
            qid, fake_redis, sqlite_db_session, known_questions_count=0
        )
    assert ei.value.status_code == 422


async def test_status_running_job_still_processes(
    sqlite_db_session, fake_redis
):
    """A 'running' job (the happy path) must keep returning processing, never the
    terminal 422 — the failed-job check must not break the normal flow."""
    qid, state = _seed_state()
    state["generated_questions"] = []
    await CacheRepository(fake_redis).save_quiz_state(state)
    await QuizJobRepository(sqlite_db_session).mark_running(qid)
    await sqlite_db_session.commit()

    resp = await quiz_mod.get_quiz_status(
        qid, fake_redis, sqlite_db_session, known_questions_count=0
    )
    assert getattr(resp, "status", None) == "processing"


async def test_status_no_job_row_still_processes(sqlite_db_session, fake_redis):
    """No job row at all (e.g. precompute short-circuit path) -> processing, not
    an error."""
    qid, state = _seed_state()
    state["generated_questions"] = []
    await CacheRepository(fake_redis).save_quiz_state(state)

    resp = await quiz_mod.get_quiz_status(
        qid, fake_redis, sqlite_db_session, known_questions_count=0
    )
    assert getattr(resp, "status", None) == "processing"


# ===========================================================================
# Hole #4 — the handler creates the durable row SYNCHRONOUSLY before scheduling
# (so a crash before the bg task's first write is still recoverable).
# ===========================================================================
async def test_ensure_job_row_before_schedule_commits_running_row(sqlite_db_session):
    qid = uuid.uuid4()
    await quiz_mod._ensure_job_row_before_schedule(sqlite_db_session, qid)
    job = await sqlite_db_session.get(QuizJob, qid)
    assert job is not None
    assert job.status == "running"
