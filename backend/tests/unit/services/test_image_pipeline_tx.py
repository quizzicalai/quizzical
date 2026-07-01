# backend/tests/unit/services/test_image_pipeline_tx.py
"""§16.4 — AC-IMG-TX-1..2: image pipeline DB transaction hygiene."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.services import image_pipeline as ip


class _FakeSession:
    """Tracks execute/commit/rollback calls; raises on execute when configured.

    Models SAVEPOINT semantics via ``begin_nested`` (Hitlist #14 review fix):
    the batched helpers wrap each row's UPDATE in ``async with
    session.begin_nested()``. When a row's execute raises, the exception
    propagates out of the nested CM (the real SAVEPOINT auto-rolls-back that row
    only) and the helper logs-and-continues — so the surviving rows still commit
    at the outer commit. ``committed_rows`` records the params of every execute
    that did NOT raise inside its savepoint, so a test can assert per-row
    durability (which rows actually landed).
    """

    def __init__(
        self,
        *,
        raise_on_execute: bool = False,
        fail_on_nth_execute: int | None = None,
    ) -> None:
        self.raise_on_execute = raise_on_execute
        # 1-based index of the execute that should raise (partial-failure test).
        self.fail_on_nth_execute = fail_on_nth_execute
        self.execute_calls: list[Any] = []
        self.committed_rows: list[Any] = []
        self.commit_calls: int = 0
        self.rollback_calls: int = 0
        self._in_savepoint = False
        self._savepoint_failed = False

    async def execute(self, *a, **kw):
        self.execute_calls.append((a, kw))
        n = len(self.execute_calls)
        should_fail = self.raise_on_execute or (
            self.fail_on_nth_execute is not None and n == self.fail_on_nth_execute
        )
        if should_fail:
            if self._in_savepoint:
                self._savepoint_failed = True
            raise RuntimeError("simulated DB failure")
        # Row succeeded inside its savepoint -> it will land at the outer commit.
        if self._in_savepoint:
            self.committed_rows.append(kw.get("parameters") or (a[1] if len(a) > 1 else None))
        return None

    def begin_nested(self):
        sess = self

        @asynccontextmanager
        async def _cm():
            sess._in_savepoint = True
            sess._savepoint_failed = False
            try:
                yield sess
            finally:
                sess._in_savepoint = False

        return _cm()

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


# ---------------------------------------------------------------------------
# Hitlist #14 — batched helpers reuse ONE session for the whole set and commit
# once (the N+1 fix). Per-row SAVEPOINTs preserve the old per-row durability:
# one failing row rolls back ONLY itself; the rest still commit (review fix).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_character_urls_batch_single_session_one_commit(patched_session_ctx):
    sess = _FakeSession(raise_on_execute=False)
    patched_session_ctx["session"] = sess

    await ip._persist_character_urls_batch(
        [("Alice", "https://x/a.png"), ("Bob", "https://x/b.png")]
    )

    # One session (one connection), one execute per row, exactly one commit.
    assert len(sess.execute_calls) == 2
    assert sess.commit_calls == 1
    assert sess.rollback_calls == 0


@pytest.mark.asyncio
async def test_persist_character_urls_batch_failing_row_does_not_abort_batch(patched_session_ctx):
    """A single failing row must NOT discard the whole batch — its SAVEPOINT
    rolls back only itself, the helper does not raise, and the outer commit is
    still issued so surviving rows land."""
    sess = _FakeSession(raise_on_execute=True)
    patched_session_ctx["session"] = sess

    # MUST NOT raise.
    await ip._persist_character_urls_batch([("Alice", "https://x/a.png")])

    # The outer transaction still commits (no whole-batch rollback).
    assert sess.commit_calls == 1
    assert sess.rollback_calls == 0
    # The failing row did not land.
    assert sess.committed_rows == []


@pytest.mark.asyncio
async def test_persist_character_urls_batch_partial_failure_preserves_durability(patched_session_ctx):
    """Hitlist #14 review fix — batch of 3 where the 2nd row's execute raises:
    rows 1 and 3 ARE persisted and row 2 is not (per-row durability restored),
    and the outer session still commits cleanly."""
    sess = _FakeSession(fail_on_nth_execute=2)
    patched_session_ctx["session"] = sess

    await ip._persist_character_urls_batch(
        [
            ("Alice", "https://x/a.png"),
            ("Bob", "https://x/b.png"),   # this row fails
            ("Carol", "https://x/c.png"),
        ]
    )

    # All three rows attempted; the failing one rolled back only itself.
    assert len(sess.execute_calls) == 3
    assert sess.commit_calls == 1
    assert sess.rollback_calls == 0
    # Rows 1 and 3 persisted; row 2 did NOT.
    persisted_names = [r.get("name") for r in sess.committed_rows]
    assert persisted_names == ["Alice", "Carol"]
    assert "Bob" not in persisted_names


@pytest.mark.asyncio
async def test_persist_character_urls_batch_skips_empty(patched_session_ctx):
    sess = _FakeSession(raise_on_execute=False)
    patched_session_ctx["session"] = sess

    # Empty url entries are filtered; nothing is written.
    await ip._persist_character_urls_batch([("Alice", "")])
    assert len(sess.execute_calls) == 0
    assert sess.commit_calls == 0


@pytest.mark.asyncio
async def test_refresh_character_set_images_batch_one_commit(patched_session_ctx):
    sess = _FakeSession(raise_on_execute=False)
    patched_session_ctx["session"] = sess

    await ip._refresh_character_set_images_batch(
        session_id=uuid.uuid4(),
        items=[("Alice", "https://x/a.png"), ("Bob", "https://x/b.png")],
    )

    assert len(sess.execute_calls) == 2
    assert sess.commit_calls == 1
    assert sess.rollback_calls == 0


@pytest.mark.asyncio
async def test_refresh_character_set_images_batch_failing_row_does_not_abort_batch(patched_session_ctx):
    sess = _FakeSession(raise_on_execute=True)
    patched_session_ctx["session"] = sess

    await ip._refresh_character_set_images_batch(
        session_id=uuid.uuid4(), items=[("Alice", "https://x/a.png")]
    )

    assert sess.commit_calls == 1
    assert sess.rollback_calls == 0
    assert sess.committed_rows == []


@pytest.mark.asyncio
async def test_refresh_character_set_images_batch_partial_failure_preserves_durability(patched_session_ctx):
    """Per-row durability for the JSONB refresh too: 2nd of 3 fails -> rows 1 and
    3 persist, row 2 does not, outer commit still issued."""
    sess = _FakeSession(fail_on_nth_execute=2)
    patched_session_ctx["session"] = sess

    await ip._refresh_character_set_images_batch(
        session_id=uuid.uuid4(),
        items=[
            ("Alice", "https://x/a.png"),
            ("Bob", "https://x/b.png"),   # fails
            ("Carol", "https://x/c.png"),
        ],
    )

    assert len(sess.execute_calls) == 3
    assert sess.commit_calls == 1
    assert sess.rollback_calls == 0
    persisted_names = [r.get("name") for r in sess.committed_rows]
    assert persisted_names == ["Alice", "Carol"]
    assert "Bob" not in persisted_names


@pytest.mark.asyncio
async def test_batched_helpers_noop_when_session_none(monkeypatch):
    @asynccontextmanager
    async def _none_ctx():
        yield None

    monkeypatch.setattr(ip, "_db_session_ctx", _none_ctx)

    await ip._persist_character_urls_batch([("x", "https://x/x.png")])
    await ip._refresh_character_set_images_batch(
        session_id=uuid.uuid4(), items=[("x", "https://x/x.png")]
    )
