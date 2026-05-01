"""§21 Phase 7 — embeddings cache (`AC-PRECOMP-COST-1`)."""

from __future__ import annotations

import pytest

from app.services.embeddings.cache import get_or_compute_embedding, text_hash


@pytest.mark.anyio
async def test_repeated_text_hits_cache(sqlite_db_session):
    calls: list[str] = []
    vec = [0.1] * 384

    async def _embed(t: str) -> list[float]:
        calls.append(t)
        return list(vec)

    v1 = await get_or_compute_embedding(
        sqlite_db_session, "hello", model="m1", dim=384, embed_fn=_embed,
    )
    v2 = await get_or_compute_embedding(
        sqlite_db_session, "hello", model="m1", dim=384, embed_fn=_embed,
    )
    assert v1 == v2 == vec
    assert calls == ["hello"], "embed_fn must run exactly once"


@pytest.mark.anyio
async def test_different_text_does_not_collide(sqlite_db_session):
    calls: list[str] = []

    async def _embed(t: str) -> list[float]:
        calls.append(t)
        return [float(len(t))] + [0.0] * 383

    await get_or_compute_embedding(
        sqlite_db_session, "abc", model="m1", dim=384, embed_fn=_embed,
    )
    await get_or_compute_embedding(
        sqlite_db_session, "abcd", model="m1", dim=384, embed_fn=_embed,
    )
    assert calls == ["abc", "abcd"]


def test_text_hash_is_deterministic_and_hex():
    h = text_hash("hello")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert h == text_hash("hello")
