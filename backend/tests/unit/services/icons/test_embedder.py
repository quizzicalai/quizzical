"""Embedder tests — the local 384-dim bge-small embedder.

These require the OPTIONAL ``fastembed`` extra (``pip install -e '.[qa-icons]'``)
and download the model on first run, so they are SKIPPED when fastembed is not
installed — keeping the core suite green without the optional dependency. The
fast, deterministic selection logic is covered without fastembed in
``test_binder.py`` / ``test_hook_flag_gate.py``.
"""

from __future__ import annotations

import importlib.util
import math

import pytest

pytestmark = pytest.mark.anyio

_HAS_FASTEMBED = importlib.util.find_spec("fastembed") is not None
requires_fastembed = pytest.mark.skipif(
    not _HAS_FASTEMBED, reason="fastembed not installed (optional [qa-icons] extra)"
)


@requires_fastembed
async def test_raw_embed_is_384_dim_unit_norm():
    from app.services.icons.embedder import DIM, raw_embed

    v = await raw_embed("a rocket launches into orbit")
    assert v is not None
    assert len(v) == DIM == 384
    norm = math.sqrt(sum(x * x for x in v))
    assert norm == pytest.approx(1.0, abs=1e-3)


@requires_fastembed
async def test_raw_embed_empty_text_returns_none():
    from app.services.icons.embedder import raw_embed

    assert await raw_embed("") is None
    assert await raw_embed("   ") is None


@requires_fastembed
async def test_raw_embed_is_deterministic():
    from app.services.icons.embedder import raw_embed

    a = await raw_embed("a fresh crisp apple")
    b = await raw_embed("a fresh crisp apple")
    assert a == b


@requires_fastembed
async def test_semantically_distinct_strings_are_far_apart():
    from app.services.icons.embedder import raw_embed
    from app.services.precompute.lookup import _default_cosine

    rocket = await raw_embed("a rocket launches into orbit")
    apple = await raw_embed("a fresh crisp apple")
    assert rocket is not None and apple is not None
    # Unrelated concepts should be well below the validated tau (~0.64).
    assert _default_cosine(rocket, apple) < 0.64


@requires_fastembed
async def test_embed_many_sync_matches_embed_one_sync():
    from app.services.icons.embedder import embed_many_sync, embed_one_sync

    texts = ["rocket spaceship launch", "wine drink grape glass"]
    batched = embed_many_sync(texts)
    assert len(batched) == 2
    assert all(len(v) == 384 for v in batched)
    one = embed_one_sync(texts[0])
    # Batched vs single path are numerically equivalent.
    assert one == pytest.approx(batched[0], abs=1e-5)
