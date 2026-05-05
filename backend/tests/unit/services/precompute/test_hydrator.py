"""§21 Phase 3 — `app/services/precompute/hydrator.py` unit tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.db import (
    BaselineQuestionSet,
    Character,
    CharacterSet,
    Question,
    Synopsis,
    Topic,
    TopicPack,
)
from app.services.precompute.hydrator import HydratedPack, hydrate_pack


async def _seed_pack(
    db,
    *,
    with_characters: bool = True,
    pack_status: str = "published",
    synopsis_body: dict | None = None,
) -> TopicPack:
    """Build a minimal published pack and return it (uncommitted)."""
    topic = Topic(id=uuid.uuid4(), slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
    db.add(topic)
    await db.flush()

    syn = Synopsis(
        id=uuid.uuid4(),
        topic_id=topic.id,
        content_hash=f"syn-{uuid.uuid4().hex}",
        body=synopsis_body if synopsis_body is not None else {"title": "T", "summary": "S"},
    )
    db.add(syn)

    char_ids: list[uuid.UUID] = []
    if with_characters:
        for n in ("Alpha", "Beta"):
            ch = Character(
                id=uuid.uuid4(),
                name=f"{n}-{uuid.uuid4().hex[:6]}",
                short_description=f"{n} desc",
                profile_text=f"{n} long profile text.",
                canonical_key=f"{n.lower()}-{uuid.uuid4().hex[:6]}",
            )
            db.add(ch)
            await db.flush()
            char_ids.append(ch.id)

    cs = CharacterSet(
        id=uuid.uuid4(),
        composition_hash=f"cs-{uuid.uuid4().hex}",
        composition={"character_ids": [str(c) for c in char_ids]},
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
        status=pack_status,
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=bqs.id,
        model_provenance={"source": "test"},
        built_in_env="test",
    )
    db.add(pack)
    await db.flush()
    return pack


@pytest.mark.anyio
async def test_hydrate_pack_returns_synopsis_and_characters(sqlite_db_session):
    pack = await _seed_pack(sqlite_db_session)
    out = await hydrate_pack(sqlite_db_session, pack_id=pack.id)
    assert isinstance(out, HydratedPack)
    assert out.synopsis == {"title": "T", "summary": "S"}
    assert len(out.characters) == 2
    assert all(c["profile_text"] for c in out.characters)
    assert all("image_url" in c for c in out.characters)


@pytest.mark.anyio
async def test_hydrate_pack_strips_extra_synopsis_fields(sqlite_db_session):
    """Persisted ``synopses.body`` may carry author-only metadata (tone,
    themes). The hydrator must project to ``{title, summary}`` only so the
    in-memory ``Synopsis`` StrictBase model accepts it and Redis save
    succeeds. Regression for the silent ``redis.save_state.fail`` observed
    on prod after the §21 Phase 3 deploy.
    """
    pack = await _seed_pack(
        sqlite_db_session,
        synopsis_body={
            "title": "Fancy Title",
            "summary": "Fancy summary.",
            "tone": "magical, contemplative",
            "themes": ["courage", "loyalty"],
        },
    )
    out = await hydrate_pack(sqlite_db_session, pack_id=pack.id)
    assert isinstance(out, HydratedPack)
    assert out.synopsis == {"title": "Fancy Title", "summary": "Fancy summary."}
    assert "tone" not in out.synopsis
    assert "themes" not in out.synopsis


@pytest.mark.anyio
async def test_hydrate_pack_missing_pack_returns_none(sqlite_db_session):
    assert await hydrate_pack(sqlite_db_session, pack_id=uuid.uuid4()) is None


@pytest.mark.anyio
async def test_hydrate_pack_unpublished_returns_none(sqlite_db_session):
    pack = await _seed_pack(sqlite_db_session, pack_status="draft")
    assert await hydrate_pack(sqlite_db_session, pack_id=pack.id) is None


@pytest.mark.anyio
async def test_hydrate_pack_no_character_ids_returns_none(sqlite_db_session):
    pack = await _seed_pack(sqlite_db_session, with_characters=False)
    assert await hydrate_pack(sqlite_db_session, pack_id=pack.id) is None


@pytest.mark.anyio
async def test_hydrate_pack_dangling_character_ids_returns_none(sqlite_db_session):
    """Composition references character_ids that don't exist anymore."""
    pack = await _seed_pack(sqlite_db_session, with_characters=False)
    cs = (
        await sqlite_db_session.execute(
            select(CharacterSet).where(CharacterSet.id == pack.character_set_id)
        )
    ).scalar_one()
    cs.composition = {"character_ids": [str(uuid.uuid4()), str(uuid.uuid4())]}
    await sqlite_db_session.flush()
    assert await hydrate_pack(sqlite_db_session, pack_id=pack.id) is None


@pytest.mark.anyio
async def test_hydrate_pack_synopsis_body_not_dict_returns_none(sqlite_db_session):
    pack = await _seed_pack(sqlite_db_session, synopsis_body={"title": "X", "summary": "Y"})
    syn = (
        await sqlite_db_session.execute(
            select(Synopsis).where(Synopsis.id == pack.synopsis_id)
        )
    ).scalar_one()
    syn.body = "not a dict"  # type: ignore[assignment]
    await sqlite_db_session.flush()
    assert await hydrate_pack(sqlite_db_session, pack_id=pack.id) is None


# ---------------------------------------------------------------------------
# §21 Phase 4 — baseline-question hydration
# ---------------------------------------------------------------------------


async def _attach_baseline_questions(db, pack: TopicPack, n: int = 3) -> list[Question]:
    """Upsert N Question rows and rewrite the BQS composition to reference them."""
    qs: list[Question] = []
    q_ids: list[str] = []
    for i in range(n):
        q = Question(
            id=uuid.uuid4(),
            text_hash=f"q-{uuid.uuid4().hex}",
            text=f"Q{i + 1}?",
            options={"items": [{"text": f"Opt{i}-{j}"} for j in range(4)]},
            kind="baseline",
        )
        db.add(q)
        await db.flush()
        qs.append(q)
        q_ids.append(str(q.id))
    bqs = (
        await db.execute(
            select(BaselineQuestionSet).where(
                BaselineQuestionSet.id == pack.baseline_question_set_id
            )
        )
    ).scalar_one()
    bqs.composition = {"question_ids": q_ids}
    await db.flush()
    return qs


@pytest.mark.anyio
async def test_hydrate_pack_returns_baseline_questions(sqlite_db_session):
    pack = await _seed_pack(sqlite_db_session)
    await _attach_baseline_questions(sqlite_db_session, pack, n=3)
    out = await hydrate_pack(sqlite_db_session, pack_id=pack.id)
    assert out is not None
    assert len(out.baseline_questions) == 3
    first = out.baseline_questions[0]
    assert first["question_text"] == "Q1?"
    assert isinstance(first["options"], list) and len(first["options"]) == 4
    assert first["options"][0]["text"].startswith("Opt0-")


@pytest.mark.anyio
async def test_hydrate_pack_baseline_questions_include_progress_phrase(
    sqlite_db_session,
):
    """AC-PROD-R6-PRECOMP-PHRASE-1 — precomputed baselines carry the same
    deterministic progress_phrase the live agent attaches."""
    from app.agent.progress_phrases import baseline_phrase_for_index

    pack = await _seed_pack(sqlite_db_session)
    await _attach_baseline_questions(sqlite_db_session, pack, n=4)
    out = await hydrate_pack(sqlite_db_session, pack_id=pack.id)
    assert out is not None
    assert len(out.baseline_questions) == 4
    for idx, q in enumerate(out.baseline_questions):
        assert q.get("progress_phrase") == baseline_phrase_for_index(idx)


@pytest.mark.anyio
async def test_hydrate_pack_no_baseline_questions_yields_empty_tuple(sqlite_db_session):
    """Legacy v2 packs (no question_ids) hydrate fine but with empty questions tuple."""
    pack = await _seed_pack(sqlite_db_session)
    out = await hydrate_pack(sqlite_db_session, pack_id=pack.id)
    assert out is not None
    assert out.baseline_questions == ()


@pytest.mark.anyio
async def test_hydrate_pack_dangling_question_ids_yields_empty_tuple(sqlite_db_session):
    """Composition references question rows that no longer exist."""
    pack = await _seed_pack(sqlite_db_session)
    bqs = (
        await sqlite_db_session.execute(
            select(BaselineQuestionSet).where(
                BaselineQuestionSet.id == pack.baseline_question_set_id
            )
        )
    ).scalar_one()
    bqs.composition = {"question_ids": [str(uuid.uuid4()), str(uuid.uuid4())]}
    await sqlite_db_session.flush()
    out = await hydrate_pack(sqlite_db_session, pack_id=pack.id)
    assert out is not None
    assert out.baseline_questions == ()
