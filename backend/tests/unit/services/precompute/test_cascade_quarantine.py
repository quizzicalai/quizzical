"""§21 Phase 6 — cascade quarantine (`AC-PRECOMP-SEC-6`)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.db import (
    BaselineQuestionSet,
    Character,
    CharacterSet,
    Synopsis,
    Topic,
    TopicPack,
)
from app.services.precompute.quarantine import cascade_quarantine_for_character


async def _seed_pack_with_character(session, *, character_id: uuid.UUID) -> TopicPack:
    topic = Topic(id=uuid.uuid4(), slug=f"t-{uuid.uuid4().hex[:6]}", display_name="T")
    session.add(topic)
    await session.flush()
    syn = Synopsis(
        id=uuid.uuid4(), topic_id=topic.id,
        content_hash="h" + uuid.uuid4().hex, body={"text": "y"},
    )
    cs = CharacterSet(
        id=uuid.uuid4(),
        composition_hash="c" + uuid.uuid4().hex,
        composition={"character_ids": [str(character_id)]},
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
async def test_lowering_character_score_quarantines_referencing_packs(
    sqlite_db_session,
):
    char = Character(
        id=uuid.uuid4(),
        name=f"Char-{uuid.uuid4().hex[:6]}",
        short_description="x",
        profile_text="y",
        canonical_key="char",
    )
    sqlite_db_session.add(char)
    await sqlite_db_session.flush()
    pack = await _seed_pack_with_character(sqlite_db_session, character_id=char.id)

    mutated = await cascade_quarantine_for_character(sqlite_db_session, char.id)
    await sqlite_db_session.commit()

    assert pack.id in mutated
    refreshed = (
        await sqlite_db_session.execute(select(TopicPack).where(TopicPack.id == pack.id))
    ).scalar_one()
    assert refreshed.status == "quarantined"


@pytest.mark.anyio
async def test_cascade_no_op_when_no_packs_reference_character(sqlite_db_session):
    char = Character(
        id=uuid.uuid4(),
        name=f"Char-{uuid.uuid4().hex[:6]}",
        short_description="x",
        profile_text="y",
        canonical_key="char-other",
    )
    sqlite_db_session.add(char)
    await sqlite_db_session.commit()
    mutated = await cascade_quarantine_for_character(sqlite_db_session, char.id)
    assert mutated == []


@pytest.mark.anyio
async def test_cascade_skips_already_quarantined_packs(sqlite_db_session):
    char = Character(
        id=uuid.uuid4(),
        name=f"Char-{uuid.uuid4().hex[:6]}",
        short_description="x",
        profile_text="y",
        canonical_key="char-q",
    )
    sqlite_db_session.add(char)
    await sqlite_db_session.flush()
    pack = await _seed_pack_with_character(sqlite_db_session, character_id=char.id)
    pack.status = "quarantined"
    sqlite_db_session.add(pack)
    await sqlite_db_session.commit()
    mutated = await cascade_quarantine_for_character(sqlite_db_session, char.id)
    # Already quarantined → not in the mutated list (filter on status='published').
    assert mutated == []
