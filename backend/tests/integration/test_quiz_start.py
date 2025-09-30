import json
import uuid
import pytest

from app.main import API_PREFIX
from tests.fixtures.agent_graph_fixtures import use_fake_agent_graph
from tests.fixtures.redis_fixtures import override_redis_dep, fake_cache_store  # DI + cache state visibility
# llm_fixtures is autouse in its own module; no need to import it here


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_quiz_start_returns_synopsis_and_optionally_characters(async_client, fake_cache_store):
    api = API_PREFIX.rstrip("/")
    payload = {"category": "Cats", "cf-turnstile-response": "test-token"}

    resp = await async_client.post(f"{api}/quiz/start?_a=dev&_k=dev", json=payload)
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

    # Assert state persisted in our shared fake Redis store
    key = f"quiz_session:{quiz_id}"
    saved = fake_cache_store.get(key)
    assert saved, "Expected quiz state to be saved to Redis"
    if isinstance(saved, bytes):
        saved = saved.decode("utf-8")
    saved_obj = json.loads(saved)
    assert saved_obj.get("session_id") == quiz_id
    assert saved_obj.get("category_synopsis") is not None
