"""Iter I: get_quiz_status must update last_served_index atomically.

Previously the endpoint read state, mutated `last_served_index`, then called
`save_quiz_state` which is a full-overwrite SET. Any concurrent write performed
by a background agent task between the read and write was silently clobbered.

Fix: use `update_quiz_state_atomically` so only the pointer field is merged
under a Redis WATCH/MULTI optimistic transaction.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport


class _StubCache:
    """Captures which cache method got called by the endpoint."""

    def __init__(self, state: dict) -> None:
        self._state = state
        self.save_called_with: list[dict] = []
        self.update_called_with: list[dict] = []

    async def get_quiz_state(self, _qid):
        from app.agent.schemas import AgentGraphStateModel

        return AgentGraphStateModel.model_validate(self._state)

    async def save_quiz_state(self, state):
        # Track full-overwrite calls. Tests below assert this is NOT used
        # for the last_served_index pointer update.
        self.save_called_with.append(dict(state))

    async def update_quiz_state_atomically(self, _qid, new_data, ttl_seconds=3600):
        from app.agent.schemas import AgentGraphStateModel

        self.update_called_with.append(dict(new_data))
        merged = {**self._state, **new_data}
        return AgentGraphStateModel.model_validate(merged)


@pytest.fixture()
def status_app(monkeypatch) -> tuple[FastAPI, _StubCache, uuid.UUID]:
    from app.api import dependencies as deps
    from app.api.endpoints import quiz as quiz_module

    quiz_id = uuid.uuid4()
    seed_state: dict[str, Any] = {
        "session_id": str(quiz_id),
        "category": "Cats",
        "trace_id": "t-iterI",
        "synopsis": {"title": "S", "summary": "S"},
        "generated_questions": [
            {"question_text": "Q1?", "options": [{"text": "a"}, {"text": "b"}]}
        ],
        "quiz_history": [],
        "final_result": None,
        "ready_for_questions": True,
        "baseline_count": 1,
        "baseline_ready": True,
        "generated_characters": [],
        "ideal_archetypes": [],
        "messages": [],
        "error_count": 0,
        "is_error": False,
        "last_served_index": None,
    }
    stub = _StubCache(seed_state)

    monkeypatch.setattr(quiz_module, "CacheRepository", lambda _client: stub)

    async def _fake_redis():
        return object()

    app = FastAPI()
    app.include_router(quiz_module.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_redis_client] = lambda: object()

    return app, stub, quiz_id


@pytest.mark.asyncio
async def test_status_uses_atomic_update_for_pointer(status_app) -> None:
    app, stub, quiz_id = status_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/quiz/status/{quiz_id}", params={"known_questions_count": 0})

    assert resp.status_code == 200, resp.text
    # Pointer update must go through the atomic merge path...
    assert stub.update_called_with, "expected update_quiz_state_atomically to be invoked"
    last_call = stub.update_called_with[-1]
    assert last_call.get("last_served_index") == 0
    # ...and must NOT use the full-overwrite save path for this pointer.
    assert stub.save_called_with == [], (
        f"save_quiz_state must not be used for the pointer update; got {stub.save_called_with!r}"
    )
