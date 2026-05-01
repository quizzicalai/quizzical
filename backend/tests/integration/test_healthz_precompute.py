"""§21 Phase 10 — `/healthz/precompute` (`AC-PRECOMP-OBJ-3`)."""

from __future__ import annotations

import fakeredis.aioredis as fr
import pytest

from app.api.dependencies import get_redis_client
from app.core.config import settings
from app.main import app as fastapi_app
from app.models.db import (
    BaselineQuestionSet,
    CharacterSet,
    Synopsis,
    Topic,
    TopicPack,
)
from app.services.precompute import telemetry

pytestmark = pytest.mark.anyio

_TOKEN = "z" * 64
URL = f"{settings.project.api_prefix}/healthz/precompute"


@pytest.fixture
def operator_token(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", _TOKEN)
    yield _TOKEN
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)


@pytest.fixture
def real_fakeredis():
    inst = fr.FakeRedis(decode_responses=False)

    async def _dep():
        return inst

    fastapi_app.dependency_overrides[get_redis_client] = _dep
    try:
        yield inst
    finally:
        fastapi_app.dependency_overrides.pop(get_redis_client, None)


async def _seed_one_published(session) -> None:
    topic = Topic(slug="hz-t", display_name="HZ T", policy_status="allowed")
    session.add(topic)
    await session.flush()
    syn = Synopsis(topic_id=topic.id, content_hash="hz-syn", body={})
    cs = CharacterSet(composition_hash="hz-cs", composition={})
    bqs = BaselineQuestionSet(composition_hash="hz-bqs", composition={})
    session.add_all([syn, cs, bqs])
    await session.flush()
    session.add(TopicPack(
        topic_id=topic.id, version=1, status="published",
        synopsis_id=syn.id, character_set_id=cs.id, baseline_question_set_id=bqs.id,
        model_provenance={}, built_in_env="test",
    ))
    await session.commit()


async def test_healthz_requires_operator_bearer(client, operator_token, real_fakeredis):
    r = await client.get(URL)
    assert r.status_code == 401


async def test_healthz_returns_pack_count_and_zero_telemetry(
    client, operator_token, sqlite_db_session, real_fakeredis,
):
    await _seed_one_published(sqlite_db_session)
    r = await client.get(URL, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["packs_published"] == 1
    assert body["hits_24h"] == 0 and body["misses_24h"] == 0
    assert body["hit_rate_24h"] == 0.0
    assert body["top_misses_24h"] == []


async def test_healthz_reflects_recorded_hits_and_top_misses(
    client, operator_token, sqlite_db_session, real_fakeredis,
):
    await _seed_one_published(sqlite_db_session)
    for _ in range(3):
        await telemetry.record_hit(real_fakeredis)
    for _ in range(4):
        await telemetry.record_miss(real_fakeredis, topic_slug="cats")
    for _ in range(3):
        await telemetry.record_miss(real_fakeredis, topic_slug="dogs")

    r = await client.get(URL, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hits_24h"] == 3 and body["misses_24h"] == 7
    assert body["hit_rate_24h"] == pytest.approx(0.3, abs=1e-4)
    slugs_in_order = [m["slug"] for m in body["top_misses_24h"][:2]]
    assert slugs_in_order[0] == "cats"
    assert "dogs" in slugs_in_order
