"""§21 Phase 4 — `/quiz/proceed` short-circuits when /quiz/start populated
state with pre-baked baseline questions (precompute origin).

Verifies that the LangGraph agent is NOT invoked, that
``_persist_baseline_questions`` is scheduled instead, and that the
``precompute.proceed.short_circuit`` telemetry event fires.
"""

from __future__ import annotations

import json
import uuid

import pytest
import structlog

from app.api.endpoints.quiz import (
    _persist_baseline_questions,
    run_agent_in_background,
)
from app.main import API_PREFIX
from tests.fixtures.redis_fixtures import seed_quiz_state
from tests.helpers.sample_payloads import proceed_payload
from tests.helpers.state_builders import make_synopsis_state


def _baseline_qs(n: int = 5) -> list[dict]:
    return [
        {
            "question_text": f"Q{i + 1}?",
            "options": [{"text": f"Opt{i}-{j}"} for j in range(4)],
        }
        for i in range(n)
    ]


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "override_db_dependency"
)
async def test_proceed_short_circuits_with_pre_baked_baseline(
    client, fake_cache_store, fake_redis, capture_background_tasks
):
    """AC-PRECOMP-PROCEED-1 — when state was created via the /quiz/start
    precompute short-circuit (``agent_plan.source='precompute'`` and
    ``baseline_ready=True``), /proceed must skip the agent and schedule
    persistence of the pre-baked questions instead."""
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    state = make_synopsis_state(quiz_id=quiz_id, category="Greek God")
    state["ready_for_questions"] = False
    state["baseline_ready"] = True
    state["baseline_count"] = 5
    state["generated_questions"] = _baseline_qs(5)
    state["agent_plan"] = {
        "title": "Which Greek god are you?",
        "synopsis": "Olympian archetypes.",
        "ideal_archetypes": ["Athena", "Apollo", "Artemis", "Hermes"],
        "source": "precompute",
        "pack_id": str(uuid.uuid4()),
    }
    seed_quiz_state(fake_redis, quiz_id, state)

    with structlog.testing.capture_logs() as captured:
        resp = await client.post(f"{api}/quiz/proceed", json=proceed_payload(quiz_id))

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "processing"

    # Redis state was updated to mark ready_for_questions=True.
    after = json.loads(fake_cache_store.get(f"quiz_session:{quiz_id}"))
    assert after["ready_for_questions"] is True
    assert after["baseline_ready"] is True
    assert len(after["generated_questions"]) == 5

    # Short-circuit telemetry fires; agent is NOT scheduled.
    events = [e.get("event") for e in captured]
    assert "precompute.proceed.short_circuit" in events

    # Exactly one background task scheduled, and it's the persistence helper
    # (NOT the agent runner).
    assert len(capture_background_tasks) == 1
    func, _args, _kwargs = capture_background_tasks[0]
    assert func is _persist_baseline_questions
    assert func is not run_agent_in_background


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "override_db_dependency"
)
async def test_proceed_falls_through_when_not_precompute_origin(
    client, fake_redis, capture_background_tasks
):
    """A non-precompute session (no ``agent_plan.source='precompute'``)
    must NOT short-circuit even if baseline_ready is somehow already
    True — the live agent path stays the source of truth."""
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    state = make_synopsis_state(quiz_id=quiz_id, category="Cats")
    state["ready_for_questions"] = False
    # No agent_plan.source field at all → not a precompute origin.
    seed_quiz_state(fake_redis, quiz_id, state)

    resp = await client.post(f"{api}/quiz/proceed", json=proceed_payload(quiz_id))
    assert resp.status_code == 202

    assert len(capture_background_tasks) == 1
    func, _args, _kwargs = capture_background_tasks[0]
    assert func is run_agent_in_background
