# backend/tests/integration/test_quiz_proceed_lock.py
"""§16.3 — AC-LOCK-PROCEED-1..3: single-flight lock at /quiz/proceed."""
from __future__ import annotations

import uuid

import pytest

from app.main import API_PREFIX
from app.security import session_lock
from tests.helpers.sample_payloads import proceed_payload
from tests.helpers.state_builders import make_synopsis_state
from tests.fixtures.redis_fixtures import seed_quiz_state


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "override_db_dependency")
async def test_proceed_returns_409_session_busy_when_lock_held(
    monkeypatch, client, fake_cache_store, fake_redis
):
    """AC-LOCK-PROCEED-2: Lock held by another in-flight request → 409 SESSION_BUSY."""
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    # Seed a valid session state so the 404 path is not taken.
    state = make_synopsis_state(quiz_id=quiz_id, category="Dogs")
    state["ready_for_questions"] = False
    seed_quiz_state(fake_redis, quiz_id, state)

    # Force ``acquire`` to behave as if another request holds the lock.
    async def _busy_acquire(*_a, **_k):
        return None

    monkeypatch.setattr(session_lock, "acquire", _busy_acquire)

    resp = await client.post(f"{api}/quiz/proceed", json=proceed_payload(quiz_id))

    assert resp.status_code == 409
    body = resp.json()
    assert body["errorCode"] == "SESSION_BUSY"
    assert "another request" in body["detail"].lower()

    # Critical: state was NOT mutated (gate must remain closed).
    import json as _json
    after = _json.loads(fake_cache_store.get(f"quiz_session:{quiz_id}"))
    assert after["ready_for_questions"] is False


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "override_db_dependency")
async def test_proceed_releases_lock_on_success(
    monkeypatch, client, fake_redis, capture_background_tasks
):
    """AC-LOCK-PROCEED-1: Lock not held → handler proceeds and releases on completion."""
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()
    state = make_synopsis_state(quiz_id=quiz_id, category="Dogs")
    state["ready_for_questions"] = False
    seed_quiz_state(fake_redis, quiz_id, state)

    released: list[tuple[str, str]] = []
    real_release = session_lock.release

    async def _spy_release(redis, sid, token):
        released.append((sid, token))
        return await real_release(redis, sid, token)

    monkeypatch.setattr(session_lock, "release", _spy_release)

    resp = await client.post(f"{api}/quiz/proceed", json=proceed_payload(quiz_id))
    assert resp.status_code == 202

    # Lock must have been released exactly once with the same session_id.
    assert len(released) == 1
    assert released[0][0] == str(quiz_id)
    assert released[0][1] and released[0][1] != session_lock.FAIL_OPEN_TOKEN


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "override_db_dependency")
async def test_proceed_fail_open_when_redis_unreachable(
    monkeypatch, client, fake_redis, capture_background_tasks
):
    """AC-LOCK-PROCEED-3: Redis unreachable → fail-open token, handler proceeds."""
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()
    state = make_synopsis_state(quiz_id=quiz_id, category="Cats")
    state["ready_for_questions"] = False
    seed_quiz_state(fake_redis, quiz_id, state)

    async def _failopen_acquire(*_a, **_k):
        return session_lock.FAIL_OPEN_TOKEN

    monkeypatch.setattr(session_lock, "acquire", _failopen_acquire)

    resp = await client.post(f"{api}/quiz/proceed", json=proceed_payload(quiz_id))
    assert resp.status_code == 202


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_next_409_includes_session_busy_errorcode(
    monkeypatch, client, fake_redis
):
    """AC-LOCK-2 (refresh) — verify /quiz/next 409 body now flat-includes errorCode."""
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()
    state = make_synopsis_state(quiz_id=quiz_id, category="Dogs")
    seed_quiz_state(fake_redis, quiz_id, state)

    async def _busy(*_a, **_k):
        return None

    monkeypatch.setattr(session_lock, "acquire", _busy)

    resp = await client.post(
        f"{api}/quiz/next",
        json={"quizId": str(quiz_id), "questionIndex": 0, "answer": "x"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body.get("errorCode") == "SESSION_BUSY"
