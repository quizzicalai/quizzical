"""P9 (2026-07-02) — /quiz/proceed and /quiz/next rehydrate from Postgres on a
Redis miss instead of 404ing.

/quiz/status already rebuilds live state from the durable ``session_history``
+ ``session_questions`` rows when the Redis key expires or is evicted. The
mid-quiz mutation endpoints previously read ONLY Redis: a user who paused past
the 1h TTL (or any eviction) got a terminal 404 in the FE — a mid-quiz
dead-end — even though everything needed to continue is durably in Postgres.

Pinned here:
- Redis-miss → DB-hit  → 202 for both endpoints, with Redis re-primed.
- Redis-miss → DB-miss → 404 (a genuinely unknown quiz stays a 404).
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.main import API_PREFIX
from app.models.db import SessionHistory, SessionQuestions

# Fixtures
from tests.fixtures.agent_graph_fixtures import use_fake_agent_graph  # noqa: F401
from tests.fixtures.background_tasks import capture_background_tasks  # noqa: F401
from tests.fixtures.db_fixtures import override_db_dependency  # noqa: F401
from tests.fixtures.redis_fixtures import (  # noqa: F401
    fake_cache_store,
    fake_redis,
    override_redis_dep,
)

# Helpers
from tests.helpers.sample_payloads import next_question_payload, proceed_payload

api = API_PREFIX.rstrip("/")
pytestmark = pytest.mark.anyio


_BASELINE_BLOB = {
    "questions": [
        {"question_text": "Q1?", "options": [{"text": "a"}, {"text": "b"}]},
        {"question_text": "Q2?", "options": [{"text": "c"}, {"text": "d"}]},
    ]
}


async def _seed_durable_rows(db, quiz_id: uuid.UUID) -> None:
    """Persist the same durable snapshots /quiz/start + the bg agent write."""
    db.add(
        SessionHistory(
            session_id=quiz_id,
            category="Cats",
            category_synopsis={"title": "Quiz: Cats", "summary": "A fun quiz."},
            session_transcript=[],
            character_set=[
                {
                    "name": "Alpha",
                    "short_description": "the upbeat one",
                    "profile_text": "Alpha is relentlessly positive.",
                    "image_url": None,
                }
            ],
            qa_history=[],
        )
    )
    db.add(SessionQuestions(session_id=quiz_id, baseline_questions=_BASELINE_BLOB))
    await db.flush()


def _redis_state(fake_cache_store, quiz_id: uuid.UUID) -> dict:
    raw = fake_cache_store.get(f"quiz_session:{quiz_id}")
    assert raw, f"expected re-primed Redis state for {quiz_id}"
    return json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# /quiz/proceed
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "override_redis_dep",
    "override_db_dependency",
    "capture_background_tasks",
)
async def test_proceed_rehydrates_from_db_on_redis_miss(
    async_client, sqlite_db_session, fake_cache_store, capture_background_tasks
):
    quiz_id = uuid.uuid4()
    await _seed_durable_rows(sqlite_db_session, quiz_id)
    # NOTE: Redis is deliberately NOT seeded — this is the miss path.

    resp = await async_client.post(f"{api}/quiz/proceed", json=proceed_payload(quiz_id))

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "processing"

    # Redis was re-primed from the DB snapshot, with the gate flipped.
    stored = _redis_state(fake_cache_store, quiz_id)
    assert stored["ready_for_questions"] is True
    assert stored["category"] == "Cats"
    assert len(stored["generated_questions"]) == 2

    # The agent continuation was scheduled (rehydrated plan is not precompute).
    assert len(capture_background_tasks) == 1


@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_proceed_404_when_redis_and_db_both_miss(async_client):
    resp = await async_client.post(
        f"{api}/quiz/proceed", json=proceed_payload(uuid.uuid4())
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /quiz/next
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "override_redis_dep",
    "override_db_dependency",
    "capture_background_tasks",
)
async def test_next_rehydrates_from_db_on_redis_miss(
    async_client, sqlite_db_session, fake_cache_store
):
    quiz_id = uuid.uuid4()
    await _seed_durable_rows(sqlite_db_session, quiz_id)
    # NOTE: Redis is deliberately NOT seeded — this is the miss path.

    # Answer question 0 at DISPLAYED slot 0. Options are served in the
    # deterministic shuffled order and the record path de-maps the displayed
    # index back to the original option (AC-ANSWER-ROUNDTRIP-1, PR #66), so the
    # expected recorded text is whichever original option the permutation put
    # at display position 0 — computed via the same shared helper.
    from app.api.endpoints.quiz import _display_option_order

    raw_options = ["a", "b"]
    order = _display_option_order(1, "Q1?", len(raw_options))
    expected_text = raw_options[order[0]]

    payload = next_question_payload(quiz_id, index=0, option_idx=0)
    resp = await async_client.post(f"{api}/quiz/next", json=payload)

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "processing"

    # The answer landed on the REHYDRATED state (proving the atomic update ran
    # against the re-primed Redis key, not a 404 dead-end).
    stored = _redis_state(fake_cache_store, quiz_id)
    history = stored["quiz_history"]
    assert len(history) == 1
    assert history[0]["question_index"] == 0
    assert history[0]["answer_text"] == expected_text


@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_next_404_when_redis_and_db_both_miss(async_client):
    payload = next_question_payload(uuid.uuid4(), index=0, option_idx=0)
    resp = await async_client.post(f"{api}/quiz/next", json=payload)
    assert resp.status_code == 404
