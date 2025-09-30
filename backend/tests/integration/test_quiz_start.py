# backend/tests/integration/test_quiz_start.py
import json
import uuid
import pytest
import httpx

from app.main import app, API_PREFIX
from tests.fixtures.agent_graph_fixtures import use_fake_agent_graph
from tests.fixtures.redis_fixtures import override_redis_dep, fake_cache_store  # <-- use shared fixtures
import tests.fixtures.llm_fixtures  # <-- activates the fake LLM fixture

try:
    from asgi_lifespan import LifespanManager
except Exception:
    LifespanManager = None

@pytest.fixture(scope="function")
async def client(override_redis_dep):
    if LifespanManager is not None:
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                yield c
    else:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph")
async def test_quiz_start_returns_synopsis_and_optionally_characters(client, fake_cache_store):
    api = API_PREFIX.rstrip("/")
    payload = {"category": "Cats", "cf-turnstile-response": "test-token"}

    resp = await client.post(f"{api}/quiz/start?_a=dev&_k=dev", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    quiz_id = body["quizId"]
    assert uuid.UUID(quiz_id).version == 4

    ip = body["initialPayload"]
    assert ip["type"] == "synopsis"
    assert ip["data"]["title"]
    assert isinstance(ip["data"]["summary"], str)

    cp = body.get("charactersPayload")
    if cp is not None:
        assert cp["type"] == "characters"
        chars = cp.get("data") or []
        assert isinstance(chars, list)
        if chars:
            assert "name" in chars[0] and "profileText" in chars[0]

    # Assert state persisted in our shared store
    key = f"quiz_session:{quiz_id}"
    saved = fake_cache_store.get(key)
    assert saved, "Expected quiz state to be saved to Redis"
    if isinstance(saved, bytes):
        saved = saved.decode("utf-8")
    saved_obj = json.loads(saved)
    assert saved_obj.get("session_id") == quiz_id
    assert saved_obj.get("category_synopsis") is not None
