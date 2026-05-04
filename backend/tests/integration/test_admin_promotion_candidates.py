"""Integration tests for `GET /admin/precompute/promotion-candidates`.

Seeds completed sessions with varying judge scores / sentiments / pack
states and asserts the endpoint returns only the promotion-eligible rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.db import (
    SessionHistory,
    SessionQuestions,
    Topic,
    TopicPack,
    UserSentimentEnum,
)

pytestmark = pytest.mark.anyio

_TOKEN = "z" * 64


@pytest.fixture
def operator_token(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", _TOKEN)
    yield _TOKEN
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)


URL = f"{settings.project.api_prefix}/admin/precompute/promotion-candidates"


def _baseline_qs(n: int = 5) -> list[dict]:
    return [
        {
            "question_text": f"Q{i + 1}?",
            "options": [{"text": f"Opt{i}-{j}"} for j in range(4)],
        }
        for i in range(n)
    ]


def _characters(n: int = 4) -> list[dict]:
    return [
        {
            "name": f"Char{i}",
            "short_description": f"short {i}",
            "profile_text": f"long profile body {i}, multiple sentences.",
        }
        for i in range(n)
    ]


async def _seed_session(
    db,
    *,
    category: str,
    completed_offset_h: float = 1.0,
    judge_score: int | None = 9,
    sentiment: UserSentimentEnum | None = None,
    add_baseline: bool = True,
    is_completed: bool = True,
    final_result: dict | None = None,
) -> uuid.UUID:
    sid = uuid.uuid4()
    session_row = SessionHistory(
        session_id=sid,
        category=category,
        category_synopsis={"title": f"Title for {category}", "summary": "S"},
        session_transcript=[],
        character_set=_characters(),
        final_result=final_result if final_result is not None else {
            "title": f"You are X-{category}",
            "description": "desc",
        },
        judge_plan_score=judge_score,
        user_sentiment=sentiment,
        is_completed=is_completed,
        completed_at=(
            datetime.now(timezone.utc) - timedelta(hours=completed_offset_h)
        )
        if is_completed
        else None,
    )
    db.add(session_row)
    await db.flush()
    if add_baseline:
        sq = SessionQuestions(
            session_id=sid,
            baseline_questions={"questions": _baseline_qs()},
        )
        db.add(sq)
    await db.commit()
    return sid


async def test_returns_eligible_session(client, operator_token, sqlite_db_session):
    await _seed_session(sqlite_db_session, category="Brand New Topic Alpha")
    r = await client.get(
        URL,
        headers={"Authorization": f"Bearer {_TOKEN}"},
        params={"since_hours": 24, "limit": 50, "min_judge_score": 7},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    cand = body["candidates"][0]
    assert cand["slug"] == "brand-new-topic-alpha"
    assert cand["display_name"] == "Brand New Topic Alpha"
    assert cand["synopsis"]["title"] == "Title for Brand New Topic Alpha"
    assert len(cand["characters"]) == 4
    assert len(cand["baseline_questions"]) == 5
    assert cand["judge_plan_score"] == 9


async def test_filters_out_low_judge_score(client, operator_token, sqlite_db_session):
    await _seed_session(
        sqlite_db_session, category="Low Quality Topic", judge_score=4,
    )
    r = await client.get(
        URL,
        headers={"Authorization": f"Bearer {_TOKEN}"},
        params={"min_judge_score": 7},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


async def test_filters_out_negative_sentiment(client, operator_token, sqlite_db_session):
    await _seed_session(
        sqlite_db_session,
        category="Down Voted Topic",
        sentiment=UserSentimentEnum.NEGATIVE,
    )
    r = await client.get(URL, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


async def test_filters_out_missing_baseline_when_required(
    client, operator_token, sqlite_db_session,
):
    await _seed_session(
        sqlite_db_session, category="No Questions Topic", add_baseline=False,
    )
    r = await client.get(
        URL,
        headers={"Authorization": f"Bearer {_TOKEN}"},
        params={"require_baseline_questions": "true"},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


async def test_filters_out_old_sessions(client, operator_token, sqlite_db_session):
    await _seed_session(
        sqlite_db_session,
        category="Stale Old Topic",
        completed_offset_h=48,
    )
    r = await client.get(
        URL,
        headers={"Authorization": f"Bearer {_TOKEN}"},
        params={"since_hours": 24},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


async def test_filters_out_already_packed_slugs(
    client, operator_token, sqlite_db_session,
):
    """A session whose slug already corresponds to a topic with
    current_pack_id MUST not be returned (would be wasted work)."""
    from app.models.db import (
        BaselineQuestionSet,
        CharacterSet,
        Synopsis,
    )

    # Pre-seed a topic with a published pack and current_pack_id set.
    topic = Topic(
        slug="already-packed-topic", display_name="Already Packed Topic"
    )
    sqlite_db_session.add(topic)
    await sqlite_db_session.flush()
    syn = Synopsis(topic_id=topic.id, content_hash="h-syn", body={"x": 1})
    cs = CharacterSet(composition_hash="h-cs", composition={"members": []})
    bqs = BaselineQuestionSet(
        composition_hash="h-bqs", composition={"questions": []}
    )
    sqlite_db_session.add_all([syn, cs, bqs])
    await sqlite_db_session.flush()
    pack = TopicPack(
        topic_id=topic.id, version=1, status="published",
        synopsis_id=syn.id, character_set_id=cs.id, baseline_question_set_id=bqs.id,
        model_provenance={}, built_in_env="test",
    )
    sqlite_db_session.add(pack)
    await sqlite_db_session.flush()
    topic.current_pack_id = pack.id
    await sqlite_db_session.commit()

    await _seed_session(sqlite_db_session, category="Already Packed Topic")
    r = await client.get(URL, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


async def test_requires_operator_bearer(client, operator_token):
    r = await client.get(URL)
    assert r.status_code == 401


async def test_filters_out_incomplete_sessions(
    client, operator_token, sqlite_db_session,
):
    await _seed_session(
        sqlite_db_session, category="Half Done Topic", is_completed=False,
    )
    r = await client.get(URL, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200
    assert r.json()["total"] == 0
