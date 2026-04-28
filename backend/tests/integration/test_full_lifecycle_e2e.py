"""§17 — Full quiz lifecycle integration test (Iter 5).

End-to-end FE↔BE contract validation driven through the real ASGI app.
Simulates how the frontend talks to the backend across the quiz lifecycle:

  POST /api/v1/quiz/start
  POST /api/v1/quiz/proceed
  POST /api/v1/quiz/next   (×N)
  GET  /api/v1/quiz/status

Background agent work is captured (not executed) so each request hits the
real handler stack, while we assert the §17 scalability guarantees:

  * Server-Timing header carries ``app;dur=…`` on EVERY response (including
    202 Processing, 200 OK, and 422 Validation Error).
  * X-Trace-ID is always echoed for FE log correlation.
  * The LLM concurrency limiter wraps real /quiz/start work and returns to
    ``in_flight == 0`` once the request resolves.
"""

from __future__ import annotations

import re
import uuid

import pytest

from app.main import API_PREFIX
from app.services.llm_concurrency import (
    get_global_limiter,
    reset_global_limiter_for_tests,
)
from tests.fixtures.redis_fixtures import seed_quiz_state
from tests.helpers.sample_payloads import (
    next_question_payload,
    proceed_payload,
    start_quiz_payload,
)
from tests.helpers.state_builders import make_questions_state, make_synopsis_state


_API = API_PREFIX.rstrip("/")
_SERVER_TIMING_RE = re.compile(r"app;dur=\d+(\.\d+)?")


@pytest.fixture(autouse=True)
def _reset_limiter():
    reset_global_limiter_for_tests()
    yield
    reset_global_limiter_for_tests()


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_full_quiz_lifecycle_with_server_timing(
    client,
    fake_redis,
    capture_background_tasks,
):
    """End-to-end FE↔BE flow: start → proceed → next×N → status."""
    # ─── 1. /quiz/start ────────────────────────────────────────────────
    start_resp = await client.post(
        f"{_API}/quiz/start?_a=test&_k=test",
        json=start_quiz_payload(topic="Astronomy"),
    )
    assert start_resp.status_code == 201, start_resp.text
    assert _SERVER_TIMING_RE.search(start_resp.headers.get("Server-Timing", ""))
    assert start_resp.headers.get("X-Trace-ID")

    quiz_id_str = start_resp.json()["quizId"]
    quiz_id = uuid.UUID(quiz_id_str)
    assert quiz_id.version == 4

    # /quiz/start scheduled a background task to generate the synopsis.
    # Seed redis with the post-synopsis state so /quiz/proceed has data.
    syn_state = make_synopsis_state(quiz_id=quiz_id, category="Astronomy")
    syn_state["ready_for_questions"] = False
    seed_quiz_state(fake_redis, quiz_id, syn_state)

    # The /quiz/start handler held a single-flight session lock that the
    # captured (un-run) background task would normally release. Clear it
    # explicitly so the next FE request isn't rejected as SESSION_BUSY.
    await fake_redis.delete(f"qlock:{quiz_id_str}")

    # ─── 2. /quiz/proceed ──────────────────────────────────────────────
    proc_resp = await client.post(
        f"{_API}/quiz/proceed",
        json=proceed_payload(quiz_id),
    )
    assert proc_resp.status_code == 202, proc_resp.text
    assert _SERVER_TIMING_RE.search(proc_resp.headers.get("Server-Timing", ""))
    assert proc_resp.headers.get("X-Trace-ID")

    # Re-seed redis with a question-ready state so /quiz/next has somewhere
    # to land (the captured bg task would have done this for real).
    q_state = make_questions_state(
        quiz_id=quiz_id,
        category="Astronomy",
        questions=["What orbits the sun?", "What is a light-year?"],
        baseline_count=2,
        answers=[],
    )
    seed_quiz_state(fake_redis, quiz_id, q_state)
    await fake_redis.delete(f"qlock:{quiz_id_str}")

    # ─── 3. /quiz/next loop (FE answer cycle) ──────────────────────────
    for idx in (0, 1):
        nxt_resp = await client.post(
            f"{_API}/quiz/next",
            json=next_question_payload(quiz_id, index=idx, option_idx=0),
        )
        assert nxt_resp.status_code == 202, nxt_resp.text
        assert _SERVER_TIMING_RE.search(nxt_resp.headers.get("Server-Timing", ""))
        assert nxt_resp.headers.get("X-Trace-ID")
        # /quiz/next also takes the session lock; release between iterations.
        await fake_redis.delete(f"qlock:{quiz_id_str}")

    # ─── 4. /quiz/status (the FE polls this between answers) ───────────
    status_resp = await client.get(
        f"{_API}/quiz/status/{quiz_id}",
        params={"known_questions_count": 2},
    )
    assert status_resp.status_code == 200
    assert _SERVER_TIMING_RE.search(status_resp.headers.get("Server-Timing", ""))
    assert status_resp.headers.get("X-Trace-ID")


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_server_timing_present_on_validation_errors(client):
    """AC-SCALE-TIMING-1: even error responses surface Server-Timing."""
    # Empty body → 422 from FastAPI request validation.
    resp = await client.post(f"{_API}/quiz/start?_a=test&_k=test", json={})
    assert resp.status_code == 422
    assert _SERVER_TIMING_RE.search(resp.headers.get("Server-Timing", ""))
    assert resp.headers.get("X-Trace-ID")


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_limiter_drains_after_request(client, capture_background_tasks):
    """AC-SCALE-LLM-3 + AC-SCALE-LLM-6: in_flight returns to 0 post-request."""
    limiter = get_global_limiter()
    assert limiter.metrics()["in_flight"] == 0

    resp = await client.post(
        f"{_API}/quiz/start?_a=test&_k=test",
        json=start_quiz_payload(topic="Drain"),
    )
    assert resp.status_code == 201, resp.text

    # By the time the response is returned the limiter slot is free.
    assert limiter.metrics()["in_flight"] == 0
