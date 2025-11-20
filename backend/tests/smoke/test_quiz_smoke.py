# backend/tests/smoke/test_quiz_smoke.py

import json
import uuid

import pytest
from sqlalchemy import select

from app.main import API_PREFIX
from app.models.db import SessionHistory
from app.api.endpoints.quiz import run_agent_in_background
from tests.helpers.sample_payloads import (
    start_quiz_payload,
    proceed_payload,
    status_params,
)


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_smoke_start_proceed_status(
    client,
    sqlite_db_session,
    fake_cache_store,
    capture_background_tasks,
):
    """
    End-to-end smoke test for the quiz flow:

    1. /quiz/start returns a synopsis and persists the session to the DB.
    2. /quiz/proceed opens the gate and schedules the background agent.
    3. /quiz/status initially reports processing while questions are not yet ready.
    """
    api = API_PREFIX.rstrip("/")
    topic = "Smoke Test Topic"

    # -------------------------------------------------------------------------
    # 1) Start the quiz: expect a synopsis and a persisted SessionHistory row.
    # -------------------------------------------------------------------------
    start_body = start_quiz_payload(topic=topic)

    resp = await client.post(f"{api}/quiz/start?_a=test&_k=test", json=start_body)
    assert resp.status_code == 201, f"start failed: {resp.status_code} {resp.text}"

    payload = resp.json()
    quiz_id_str = payload.get("quizId")
    assert quiz_id_str, "Expected quizId in response"
    quiz_id = uuid.UUID(quiz_id_str)

    initial_payload = payload.get("initialPayload")
    assert initial_payload is not None
    assert initial_payload["type"] == "synopsis"
    assert "title" in initial_payload["data"]
    assert "summary" in initial_payload["data"]

    # Verify DB row created for this session
    result = await sqlite_db_session.execute(
        select(SessionHistory).where(SessionHistory.session_id == quiz_id)
    )
    session_row = result.scalar_one_or_none()
    assert session_row is not None, "Expected SessionHistory row to exist"
    assert session_row.category == topic
    assert session_row.category_synopsis is not None

    # -------------------------------------------------------------------------
    # 2) Proceed: open the gate and schedule the background agent.
    # -------------------------------------------------------------------------
    proceed_body = proceed_payload(quiz_id)
    resp2 = await client.post(f"{api}/quiz/proceed", json=proceed_body)
    assert resp2.status_code == 202, f"proceed failed: {resp2.status_code} {resp2.text}"

    body2 = resp2.json()
    assert body2["status"] == "processing"
    assert body2["quizId"] == quiz_id_str

    # Redis state should have the gate open but baseline not yet ready
    key = f"quiz_session:{quiz_id}"
    raw_state = fake_cache_store.get(key)
    assert raw_state, "Expected Redis quiz state after proceed()"

    state = json.loads(raw_state)
    assert state.get("ready_for_questions") is True
    assert state.get("baseline_ready") is False

    # Background task should be scheduled with the right function and state
    assert len(capture_background_tasks) == 1
    func, args, kwargs = capture_background_tasks[0]
    assert func is run_agent_in_background

    task_state = args[0]
    assert task_state["ready_for_questions"] is True
    assert str(task_state["session_id"]) == quiz_id_str

    # -------------------------------------------------------------------------
    # 3) Status: while baseline questions are still being generated,
    #    the API should report processing.
    # -------------------------------------------------------------------------
    resp3 = await client.get(
        f"{api}/quiz/status/{quiz_id}",
        params=status_params(),  # default client-known state
    )
    assert resp3.status_code == 200, f"status failed: {resp3.status_code} {resp3.text}"

    body3 = resp3.json()
    assert body3["status"] == "processing"
    assert body3["quizId"] == quiz_id_str
