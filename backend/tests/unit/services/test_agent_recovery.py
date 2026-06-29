"""Crash-recovery sweeper orchestration (sweep_once / recovery_loop)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.api import dependencies as deps
from app.services import agent_recovery as ar


def _app_with_graph():
    return SimpleNamespace(state=SimpleNamespace(agent_graph=object()))


@pytest.mark.asyncio
async def test_sweep_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(ar.settings.security.agent_recovery, "enabled", False, raising=False)
    assert await ar.sweep_once(_app_with_graph()) == 0


@pytest.mark.asyncio
async def test_sweep_noop_without_agent_graph(monkeypatch):
    monkeypatch.setattr(ar.settings.security.agent_recovery, "enabled", True, raising=False)
    app = SimpleNamespace(state=SimpleNamespace(agent_graph=None))
    assert await ar.sweep_once(app) == 0


@pytest.mark.asyncio
async def test_sweep_claims_and_recovers(monkeypatch):
    monkeypatch.setattr(ar.settings.security.agent_recovery, "enabled", True, raising=False)
    qid = uuid.uuid4()

    class _FakeRepo:
        def __init__(self, _db):
            pass

        async def fail_exhausted(self, **_kw):
            return []

        async def claim_stale(self, **_kw):
            return [qid]

    class _Sess:
        async def commit(self):
            return None

    class _Ctx:
        async def __aenter__(self):
            return _Sess()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr("app.services.database.QuizJobRepository", _FakeRepo)
    monkeypatch.setattr(deps, "async_session_factory", lambda: _Ctx(), raising=False)
    monkeypatch.setattr(deps, "get_redis_client", lambda: object(), raising=False)

    recovered: list = []

    async def _fake_recover(quiz_id, agent_graph, redis_client):
        recovered.append(quiz_id)

    monkeypatch.setattr(ar, "_recover_one", _fake_recover)

    n = await ar.sweep_once(_app_with_graph())
    assert n == 1
    assert recovered == [qid]


@pytest.mark.asyncio
async def test_sweep_no_claims_returns_zero(monkeypatch):
    monkeypatch.setattr(ar.settings.security.agent_recovery, "enabled", True, raising=False)

    class _FakeRepo:
        def __init__(self, _db):
            pass

        async def fail_exhausted(self, **_kw):
            return []

        async def claim_stale(self, **_kw):
            return []

    class _Sess:
        async def commit(self):
            return None

    class _Ctx:
        async def __aenter__(self):
            return _Sess()

        async def __aexit__(self, *_a):
            return False

    called = {"recover": 0}

    async def _fake_recover(*_a, **_k):
        called["recover"] += 1

    monkeypatch.setattr("app.services.database.QuizJobRepository", _FakeRepo)
    monkeypatch.setattr(deps, "async_session_factory", lambda: _Ctx(), raising=False)
    monkeypatch.setattr(ar, "_recover_one", _fake_recover)

    assert await ar.sweep_once(_app_with_graph()) == 0
    assert called["recover"] == 0
