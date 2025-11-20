# tests/integration/test_quiz_start.py

import uuid
import json
import pytest
from sqlalchemy import select

from app.main import API_PREFIX
from app.models.db import SessionHistory  # adjust name if needed
from tests.helpers.sample_payloads import start_quiz_payload


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_returns_synopsis_and_persists_state(
    client,
    sqlite_db_session,   # NEW: pull in the same session fixture used by override_db_dependency
):
    """
    Verifies that starting a quiz:
    1. Returns 201 Created.
    2. Returns a valid synopsis payload from the FakeAgentGraph.
    3. Persists the initial session row to the database.
    """
    api = API_PREFIX.rstrip("/")
    payload = start_quiz_payload(topic="Cats")

    # 1. Call Endpoint
    resp = await client.post(f"{api}/quiz/start?_a=test&_k=test", json=payload)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    body = resp.json()
    quiz_id = body.get("quizId")
    assert quiz_id
    assert uuid.UUID(quiz_id).version == 4

    # 2. Check Response Payload
    ip = body.get("initialPayload")
    assert ip is not None
    assert ip["type"] == "synopsis"
    assert "title" in ip["data"]
    assert "summary" in ip["data"]

    # 3. Check DB Persistence (canonical state)
    result = await sqlite_db_session.execute(
        select(SessionHistory).where(SessionHistory.session_id == uuid.UUID(quiz_id))
    )
    session_row = result.scalar_one_or_none()

    assert session_row is not None, "Expected session row to be persisted to DB"
    assert str(session_row.session_id) == quiz_id
    assert session_row.category == "Cats"
    assert session_row.category_synopsis is not None
    # Optional: align synopsis title with the payload
    assert session_row.category_synopsis.get("title") == ip["data"]["title"]
