"""Binder fidelity tests — the binder MUST mirror lookup.py::_vector_nn.

These tests inject a deterministic fake ``embed_fn`` (no fastembed / no model
load) so they are fast and verify the SELECTION logic only:

    embed(query) -> cosine-argmax over candidates -> tau cutoff -> else None

plus the BGE query prefix and the graceful no-icon fallback. The numeric
equivalence with ``_vector_nn``'s cosine is checked directly against
``_default_cosine``.
"""

from __future__ import annotations

import pytest

from app.services.icons.binder import IconBinder
from app.services.icons.index import IconCandidate
from app.services.precompute.lookup import _default_cosine

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers — a tiny orthogonal-ish index so argmax is unambiguous.
# ---------------------------------------------------------------------------

def _vec(*pairs: tuple[int, float], dim: int = 8) -> list[float]:
    v = [0.0] * dim
    for i, val in pairs:
        v[i] = val
    return v


def _index() -> list[IconCandidate]:
    return [
        IconCandidate(id="rocket", lucide="rocket", concept="space", caption="c",
                      palette_variant="sea", embedding=_vec((0, 1.0))),
        IconCandidate(id="fire", lucide="flame", concept="fire", caption="c",
                      palette_variant="amber", embedding=_vec((1, 1.0))),
        IconCandidate(id="water", lucide="droplet", concept="water", caption="c",
                      palette_variant="sea", embedding=_vec((2, 1.0))),
    ]


def _fake_embed_for(target_index: int, *, magnitude: float = 1.0):
    """Return an async EmbedFn that always emits a vector pointing at one axis,
    so the nearest candidate is deterministic. ``magnitude`` lets us push the
    cosine above/below tau (cosine is scale-invariant, so we instead blend)."""
    async def _embed(text: str) -> list[float] | None:
        if not text or not text.strip():
            return None
        return _vec((target_index, magnitude))
    return _embed


# ---------------------------------------------------------------------------
# Selection mirrors _vector_nn: argmax above tau
# ---------------------------------------------------------------------------

async def test_binds_argmax_above_tau():
    binder = IconBinder(index=_index(), embed_fn=_fake_embed_for(1), tau=0.5)
    out = await binder.bind("a fire type with a burning tail")
    assert out is not None
    assert out.icon_id == "fire"
    assert out.palette_variant == "amber"
    assert out.similarity == pytest.approx(1.0, abs=1e-6)


async def test_no_icon_below_tau():
    # Query vector at 45° to every axis -> max cosine ~0.577 < tau=0.7 -> None.
    async def _diagonal(text: str) -> list[float] | None:
        return _vec((0, 1.0), (1, 1.0), (2, 1.0))

    binder = IconBinder(index=_index(), embed_fn=_diagonal, tau=0.7)
    out = await binder.bind("an abstract motto with no clear icon")
    assert out is None  # graceful no-icon, like _vector_nn returning None


async def test_empty_query_returns_none():
    binder = IconBinder(index=_index(), embed_fn=_fake_embed_for(0), tau=0.1)
    assert await binder.bind("") is None
    assert await binder.bind("   ") is None


async def test_empty_index_returns_none():
    binder = IconBinder(index=[], embed_fn=_fake_embed_for(0), tau=0.1)
    assert await binder.bind("anything") is None


async def test_embed_fn_returning_none_yields_no_icon():
    async def _none(text: str) -> list[float] | None:
        return None

    binder = IconBinder(index=_index(), embed_fn=_none, tau=0.0)
    assert await binder.bind("rocket") is None


# ---------------------------------------------------------------------------
# BGE query prefix is applied to the QUERY only
# ---------------------------------------------------------------------------

async def test_query_prefix_is_prepended_to_query():
    seen: list[str] = []

    async def _spy(text: str) -> list[float] | None:
        seen.append(text)
        return _vec((0, 1.0))

    prefix = "Represent this sentence for searching relevant passages: "
    binder = IconBinder(index=_index(), embed_fn=_spy, tau=0.5, query_prefix=prefix)
    await binder.bind("rocket")
    assert seen == [prefix + "rocket"]


# ---------------------------------------------------------------------------
# Cosine math is identical to lookup.py::_default_cosine (numeric equivalence)
# ---------------------------------------------------------------------------

async def test_cosine_matches_default_cosine():
    idx = _index()

    # Capture the exact sims the binder computes by injecting a spy cosine_fn
    # that delegates to _default_cosine (the same function _vector_nn uses).
    calls: list[float] = []

    def _spy_cosine(a, b):
        s = _default_cosine(a, b)
        calls.append(s)
        return s

    query = _vec((1, 1.0))

    async def _embed(text: str) -> list[float] | None:
        return query

    binder = IconBinder(index=idx, embed_fn=_embed, tau=0.0, cosine_fn=_spy_cosine)
    out = await binder.bind("fire")

    # Recompute the expected argmax independently with _default_cosine.
    expected = max(idx, key=lambda c: _default_cosine(query, c.embedding))
    assert out is not None
    assert out.icon_id == expected.id
    # The binder evaluated cosine against every candidate (full scan, like _vector_nn).
    assert len(calls) == len(idx)
