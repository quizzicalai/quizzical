# backend/tests/unit/services/test_image_pipeline_tx.py
"""§16.4 — AC-IMG-TX-1..2: image pipeline DB transaction hygiene."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.services import image_pipeline as ip


class _FakeSession:
    """Tracks execute/commit/rollback calls; raises on execute when configured."""

    def __init__(self, *, raise_on_execute: bool = False) -> None:
        self.raise_on_execute = raise_on_execute
        self.execute_calls: list[Any] = []
        self.commit_calls: int = 0
        self.rollback_calls: int = 0

    async def execute(self, *a, **kw):
        self.execute_calls.append((a, kw))
        if self.raise_on_execute:
            raise RuntimeError("simulated DB failure")
        return None

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


@pytest.fixture
def patched_session_ctx(monkeypatch):
    """Replace ``_db_session_ctx`` with one yielding our controllable fake."""
    state: dict[str, Any] = {"session": None}

    @asynccontextmanager
    async def _ctx():
        yield state["session"]

    monkeypatch.setattr(ip, "_db_session_ctx", _ctx)
    return state


# ---------------------------------------------------------------------------
# AC-IMG-TX-1: rollback called when execute raises (each helper)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_character_url_rolls_back_on_failure(patched_session_ctx):
    sess = _FakeSession(raise_on_execute=True)
    patched_session_ctx["session"] = sess

    # MUST NOT raise.
    await ip._persist_character_url(name="Alice", url="https://x/a.png")

    assert len(sess.execute_calls) == 1
    assert sess.commit_calls == 0
    assert sess.rollback_calls == 1


@pytest.mark.asyncio
async def test_persist_character_set_rolls_back_on_failure(patched_session_ctx):
    sess = _FakeSession(raise_on_execute=True)
    patched_session_ctx["session"] = sess

    await ip._refresh_character_set_image(
        session_id=uuid.uuid4(), name="Bob", url="https://x/b.png"
    )

    assert sess.commit_calls == 0
    assert sess.rollback_calls == 1


@pytest.mark.asyncio
async def test_persist_synopsis_image_rolls_back_on_failure(patched_session_ctx):
    sess = _FakeSession(raise_on_execute=True)
    patched_session_ctx["session"] = sess

    await ip._persist_synopsis_image(session_id=uuid.uuid4(), url="https://x/s.png")

    assert sess.commit_calls == 0
    assert sess.rollback_calls == 1


@pytest.mark.asyncio
async def test_persist_result_image_rolls_back_on_failure(patched_session_ctx):
    sess = _FakeSession(raise_on_execute=True)
    patched_session_ctx["session"] = sess

    await ip._persist_result_image(session_id=uuid.uuid4(), url="https://x/r.png")

    assert sess.commit_calls == 0
    assert sess.rollback_calls == 1


# ---------------------------------------------------------------------------
# Sanity: success path commits and does NOT roll back.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_character_url_commits_on_success(patched_session_ctx):
    sess = _FakeSession(raise_on_execute=False)
    patched_session_ctx["session"] = sess

    await ip._persist_character_url(name="Carol", url="https://x/c.png")

    assert sess.commit_calls == 1
    assert sess.rollback_calls == 0


# ---------------------------------------------------------------------------
# AC-IMG-TX-2: helpers no-op when factory is unset (yields None).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_helpers_noop_when_session_none(monkeypatch):
    @asynccontextmanager
    async def _none_ctx():
        yield None

    monkeypatch.setattr(ip, "_db_session_ctx", _none_ctx)

    # All four helpers must complete without error and without touching DB.
    await ip._persist_character_url(name="x", url="https://x/x.png")
    await ip._refresh_character_set_image(session_id=uuid.uuid4(), name="x", url="https://x/x.png")
    await ip._persist_synopsis_image(session_id=uuid.uuid4(), url="https://x/x.png")
    await ip._persist_result_image(session_id=uuid.uuid4(), url="https://x/x.png")
