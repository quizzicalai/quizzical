"""§21 Phase 3 — `/quiz/start` short-circuits on a precompute HIT with a
fully-baked pack (synopsis + characters), without invoking the LangGraph
agent.
"""

from __future__ import annotations

import uuid

import pytest
import structlog
from sqlalchemy import select

from app.core.config import settings
from app.main import API_PREFIX
from app.models.db import (
    BaselineQuestionSet,
    Character,
    CharacterSet,
    SessionHistory,
    Synopsis,
    Topic,
    TopicPack,
)
from app.services.precompute import cache as pack_cache
from app.services.precompute.cache import ResolvedPack
from tests.helpers.sample_payloads import start_quiz_payload

API = API_PREFIX.rstrip("/")


class _StubResolution:
    def __init__(self, topic_id: str, pack_id: str) -> None:
        self.topic_id = topic_id
        self.pack_id = pack_id
        self.via = "alias"
        self.similarity = 1.0


async def _seed_full_pack(db) -> tuple[Topic, TopicPack, list[Character]]:
    topic = Topic(
        id=uuid.uuid4(), slug=f"sc-{uuid.uuid4().hex[:8]}", display_name="Short-circuit"
    )
    db.add(topic)
    await db.flush()

    syn = Synopsis(
        id=uuid.uuid4(),
        topic_id=topic.id,
        content_hash=f"syn-{uuid.uuid4().hex}",
        body={"title": "SC Title", "summary": "SC summary text."},
    )
    db.add(syn)

    chars: list[Character] = []
    for n in ("Sigma", "Tau", "Upsilon", "Phi"):
        ch = Character(
            id=uuid.uuid4(),
            name=f"{n}-{uuid.uuid4().hex[:6]}",
            short_description=f"{n} short desc",
            profile_text=f"{n} long profile body, multi sentence.",
            canonical_key=f"{n.lower()}-{uuid.uuid4().hex[:6]}",
        )
        db.add(ch)
        await db.flush()
        chars.append(ch)

    cs = CharacterSet(
        id=uuid.uuid4(),
        composition_hash=f"cs-{uuid.uuid4().hex}",
        composition={"character_ids": [str(c.id) for c in chars]},
    )
    bqs = BaselineQuestionSet(
        id=uuid.uuid4(),
        composition_hash=f"bqs-{uuid.uuid4().hex}",
        composition={"question_ids": []},
    )
    db.add_all([cs, bqs])
    await db.flush()

    pack = TopicPack(
        id=uuid.uuid4(),
        topic_id=topic.id,
        version=1,
        status="published",
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=bqs.id,
        model_provenance={"source": "test"},
        built_in_env="test",
    )
    db.add(pack)
    topic.current_pack_id = pack.id
    await db.flush()
    return topic, pack, chars


def _force_resolver_hit(monkeypatch, topic_id: str, pack_id: str) -> None:
    from app.services.precompute import lookup as lookup_mod

    async def _fake_resolve(self, _category):  # noqa: ANN001
        return _StubResolution(topic_id, pack_id)

    monkeypatch.setattr(lookup_mod.PrecomputeLookup, "resolve_topic", _fake_resolve)

    async def _fake_get_or_fill(_redis, _topic_id, _fill_fn, **_kw):
        return ResolvedPack(
            topic_id=topic_id,
            pack_id=pack_id,
            version=1,
            synopsis_id=str(uuid.uuid4()),
            character_set_id=str(uuid.uuid4()),
            baseline_question_set_id=str(uuid.uuid4()),
            storage_uris=(),
        )

    monkeypatch.setattr(pack_cache, "get_or_fill", _fake_get_or_fill)


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_short_circuits_with_pack_content(
    client, sqlite_db_session, monkeypatch
):
    """AC-PRECOMP-HIT-1 — pack with synopsis + characters serves a complete
    /quiz/start response straight from the DB. The structlog event
    ``precompute.start.short_circuit`` fires; the agent is not invoked
    (no `quiz.start.agent_invoked` event from the fake graph)."""

    monkeypatch.setattr(settings.precompute, "enabled", True)
    topic, pack, chars = await _seed_full_pack(sqlite_db_session)

    _force_resolver_hit(monkeypatch, str(topic.id), str(pack.id))

    payload = start_quiz_payload(topic="Whatever — resolver is stubbed")
    with structlog.testing.capture_logs() as captured:
        resp = await client.post(f"{API}/quiz/start?_a=test&_k=test", json=payload)

    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Pre-baked synopsis was served verbatim.
    assert body["initialPayload"]["type"] == "synopsis"
    assert body["initialPayload"]["data"]["title"] == "SC Title"
    assert body["initialPayload"]["data"]["summary"] == "SC summary text."

    # All four pre-baked characters are present in the response.
    assert body.get("charactersPayload") is not None
    returned_names = {c["name"] for c in body["charactersPayload"]["data"]}
    assert returned_names == {c.name for c in chars}

    # Short-circuit telemetry event must fire (and "Agent initial step
    # completed" — emitted right after agent_graph.ainvoke — must NOT,
    # proving we skipped LangGraph entirely).
    events = [e.get("event") for e in captured]
    assert "precompute.start.short_circuit" in events, captured
    assert "Agent initial step completed" not in events, captured

    # Session row was persisted.
    quiz_id = uuid.UUID(body["quizId"])
    row = (
        await sqlite_db_session.execute(
            select(SessionHistory).where(SessionHistory.session_id == quiz_id)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert isinstance(row.character_set, list)
    assert len(row.character_set) == 4


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_quiz_start_falls_through_when_pack_has_no_characters(
    client, sqlite_db_session, monkeypatch
):
    """AC-PRECOMP-HIT-2 — a HIT against a legacy synopsis-only pack falls
    through to the live agent path (existing v1 behaviour)."""

    monkeypatch.setattr(settings.precompute, "enabled", True)

    # Seed a synopsis-only pack (empty character set).
    topic = Topic(id=uuid.uuid4(), slug="legacy-only", display_name="Legacy Only")
    sqlite_db_session.add(topic)
    await sqlite_db_session.flush()
    syn = Synopsis(
        id=uuid.uuid4(),
        topic_id=topic.id,
        content_hash=f"syn-{uuid.uuid4().hex}",
        body={"title": "Legacy", "summary": "legacy"},
    )
    cs = CharacterSet(
        id=uuid.uuid4(),
        composition_hash=f"cs-{uuid.uuid4().hex}",
        composition={"character_ids": []},
    )
    bqs = BaselineQuestionSet(
        id=uuid.uuid4(),
        composition_hash=f"bqs-{uuid.uuid4().hex}",
        composition={"question_ids": []},
    )
    sqlite_db_session.add_all([syn, cs, bqs])
    await sqlite_db_session.flush()
    pack = TopicPack(
        id=uuid.uuid4(),
        topic_id=topic.id,
        version=1,
        status="published",
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=bqs.id,
        model_provenance={"source": "test"},
        built_in_env="test",
    )
    sqlite_db_session.add(pack)
    topic.current_pack_id = pack.id
    await sqlite_db_session.flush()

    _force_resolver_hit(monkeypatch, str(topic.id), str(pack.id))

    payload = start_quiz_payload(topic="Anything")
    with structlog.testing.capture_logs() as captured:
        resp = await client.post(f"{API}/quiz/start?_a=test&_k=test", json=payload)

    assert resp.status_code == 201, resp.text
    events = [e.get("event") for e in captured]
    assert "precompute.start.short_circuit" not in events
    # Skip-no-content trace fires, then we fall through to the agent.
    assert "precompute.start.short_circuit.skip_no_content" in events
