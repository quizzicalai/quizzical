"""§21 Phase 3 — dedup helper tests (`AC-PRECOMP-DEDUP-1..3`)."""

from __future__ import annotations

import pytest

from app.models.db import Character, MediaAsset
from app.services.precompute.dedup import (
    content_hash,
    coerce_uuid,
    find_character_by_canonical_key,
    find_media_asset_by_prompt_hash,
    prompt_hash,
)
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


def test_content_hash_is_stable_across_dict_order() -> None:
    a = content_hash({"x": 1, "y": [1, 2], "z": {"k": "v"}})
    b = content_hash({"z": {"k": "v"}, "y": [1, 2], "x": 1})
    assert a == b
    # And differs when the payload changes.
    assert a != content_hash({"x": 2, "y": [1, 2], "z": {"k": "v"}})


def test_prompt_hash_includes_provider_and_model() -> None:
    base = prompt_hash("a kitten in a teacup", provider="fal", model="m1")
    assert base != prompt_hash("a kitten in a teacup", provider="fal", model="m2")
    assert base != prompt_hash("a kitten in a teacup", provider="other", model="m1")
    # Same triple → same hash.
    assert base == prompt_hash("a kitten in a teacup", provider="fal", model="m1")


def test_coerce_uuid_accepts_str_and_uuid_and_rejects_garbage() -> None:
    from uuid import UUID, uuid4

    u = uuid4()
    assert coerce_uuid(u) == u
    assert coerce_uuid(str(u)) == u
    assert coerce_uuid("not-a-uuid") is None
    assert coerce_uuid(None) is None


async def test_find_character_by_canonical_key_returns_existing(sqlite_db_session) -> None:
    # Seed a character with canonical_key = "darth vader".
    ch = Character(
        name="Darth Vader",
        short_description="lord of the sith",
        profile_text="a profile body",
        canonical_key="darth vader",
    )
    sqlite_db_session.add(ch)
    await sqlite_db_session.commit()

    hit = await find_character_by_canonical_key(sqlite_db_session, "Darth  Vader")
    assert hit is not None
    assert hit.id == ch.id

    miss = await find_character_by_canonical_key(sqlite_db_session, "Luke Skywalker")
    assert miss is None


async def test_find_media_asset_by_prompt_hash_filters_by_score(sqlite_db_session) -> None:
    h = prompt_hash("a wizard", provider="fal", model="m1")
    asset = MediaAsset(
        content_hash="chash-1",
        prompt_hash=h,
        storage_uri="https://cdn/x",
        prompt_payload={"prompt": "a wizard"},
        evaluator_score=9,
    )
    sqlite_db_session.add(asset)
    await sqlite_db_session.commit()

    hit = await find_media_asset_by_prompt_hash(
        sqlite_db_session, prompt="a wizard", provider="fal", model="m1",
        min_evaluator_score=7,
    )
    assert hit is not None and hit.id == asset.id

    # Below threshold → treated as a miss.
    miss = await find_media_asset_by_prompt_hash(
        sqlite_db_session, prompt="a wizard", provider="fal", model="m1",
        min_evaluator_score=10,
    )
    assert miss is None
