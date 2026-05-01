"""§21 Phase 6 — distinct-IP threshold quarantines a topic pack
(`AC-PRECOMP-FLAG-4`) and the cache is invalidated so subsequent
`/quiz/start` falls through to the live agent (`AC-PRECOMP-FLAG-5`)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.main import API_PREFIX
from app.models.db import (
    BaselineQuestionSet,
    CharacterSet,
    Synopsis,
    Topic,
    TopicPack,
)

API = API_PREFIX.rstrip("/")
URL = f"{API}/content/flag"


async def _seed_published_pack(session) -> TopicPack:
    topic = Topic(
        id=uuid.uuid4(),
        slug=f"t-{uuid.uuid4().hex[:8]}",
        display_name="Test Topic",
    )
    session.add(topic)
    await session.flush()
    syn = Synopsis(
        id=uuid.uuid4(),
        topic_id=topic.id,
        content_hash="h" + uuid.uuid4().hex,
        body={"text": "x"},
    )
    cs = CharacterSet(
        id=uuid.uuid4(),
        composition_hash="c" + uuid.uuid4().hex,
        composition={"character_ids": []},
    )
    bqs = BaselineQuestionSet(
        id=uuid.uuid4(),
        composition_hash="b" + uuid.uuid4().hex,
        composition={"question_ids": []},
    )
    session.add_all([syn, cs, bqs])
    await session.flush()
    pack = TopicPack(
        id=uuid.uuid4(),
        topic_id=topic.id,
        version=1,
        status="published",
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=bqs.id,
        model_provenance={},
        built_in_env="test",
    )
    session.add(pack)
    await session.commit()
    await session.refresh(pack)
    return pack


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_distinct_ips_above_threshold_quarantines_pack(
    async_client, sqlite_db_session
):
    pack = await _seed_published_pack(sqlite_db_session)
    payload_base = {
        "target_kind": "topic_pack",
        "target_id": str(pack.id),
        "reason_code": "inappropriate",
        "reason_text": "bad",
    }
    # Default flag_quarantine_count is 5.
    for i in range(5):
        ip = f"198.51.100.{i + 1}"
        resp = await async_client.post(
            URL, json=payload_base, headers={"X-Forwarded-For": ip}
        )
        assert resp.status_code == 202

    refreshed = (
        await sqlite_db_session.execute(select(TopicPack).where(TopicPack.id == pack.id))
    ).scalar_one()
    assert refreshed.status == "quarantined"


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_below_threshold_does_not_quarantine(async_client, sqlite_db_session):
    pack = await _seed_published_pack(sqlite_db_session)
    payload = {
        "target_kind": "topic_pack",
        "target_id": str(pack.id),
        "reason_code": "inappropriate",
        "reason_text": "bad",
    }
    for i in range(4):  # one below threshold
        await async_client.post(
            URL, json=payload, headers={"X-Forwarded-For": f"198.51.100.{i + 1}"}
        )
    refreshed = (
        await sqlite_db_session.execute(select(TopicPack).where(TopicPack.id == pack.id))
    ).scalar_one()
    assert refreshed.status == "published"


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_same_ip_does_not_double_count(async_client, sqlite_db_session):
    pack = await _seed_published_pack(sqlite_db_session)
    payload = {
        "target_kind": "topic_pack",
        "target_id": str(pack.id),
        "reason_code": "inappropriate",
        "reason_text": "bad",
    }
    # Same IP 10 times → 1 distinct ip_hash → no quarantine.
    for _ in range(10):
        await async_client.post(
            URL, json=payload, headers={"X-Forwarded-For": "198.51.100.42"}
        )
    refreshed = (
        await sqlite_db_session.execute(select(TopicPack).where(TopicPack.id == pack.id))
    ).scalar_one()
    assert refreshed.status == "published"
