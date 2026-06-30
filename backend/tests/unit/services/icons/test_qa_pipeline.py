"""Same-universe Q&A image pipeline tests (PRIORITY 2).

Covers the prompt builder (universe-anchored, FAL-shape) + the build-time
generator (additive enrich, dedup reuse, cap-aware fallback) with a FAKE FAL
client so no real spend occurs.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.image_tools import build_qa_image_prompt, qa_image_alt
from app.models.db import FalSpendLedger, MediaAsset
from app.services.icons.fal_ledger import FalLedger
from app.services.icons.qa_pipeline import QaImageGenerator
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Prompt builder — same-universe shape
# ---------------------------------------------------------------------------

def test_qa_prompt_anchors_on_universe():
    built = build_qa_image_prompt(
        topic="Harry Potter",
        text="Dumbledore looking into a pensieve",
        kind="answer",
        style_suffix="flat illustrated, no text",
        negative_prompt="text, watermark",
    )
    p = built["prompt"]
    assert "Harry Potter" in p
    assert "Dumbledore looking into a pensieve" in p
    # The universe is named first (grounding token at the head).
    assert p.index("Harry Potter") < p.index("Dumbledore")
    assert built["negative_prompt"] == "text, watermark"


def test_qa_prompt_question_kind_is_establishing():
    built = build_qa_image_prompt(
        topic="Disney Princess",
        text="Where are you on a Saturday?",
        kind="question",
        style_suffix="s",
        negative_prompt="n",
    )
    assert "Disney Princess" in built["prompt"]
    assert "establishing scene" in built["prompt"]


def test_qa_alt_is_concise():
    assert qa_image_alt(topic="Harry Potter", text="A wand") == "A wand — Harry Potter"


# ---------------------------------------------------------------------------
# Generator — additive enrich
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self):
        self.calls = []

    async def generate(self, *, prompt, negative_prompt=None, seed=None):
        self.calls.append(prompt)
        return f"https://fal.media/{len(self.calls)}.png"


class _ImageGenCfg:
    provider = "fal"
    model = "fal-ai/flux/schnell"
    style_suffix = "flat illustrated, no text"
    negative_prompt = "text, watermark"


class _Budget:
    def __init__(self, cap_usd=150.0, cost_per_image_usd=0.011, enforce=True):
        self.cap_usd = cap_usd
        self.cost_per_image_usd = cost_per_image_usd
        self.enforce = enforce

    @property
    def cap_cents(self):
        return int(round(self.cap_usd * 100))

    @property
    def cost_per_image_cents(self):
        return self.cost_per_image_usd * 100.0


def _artefact():
    return {
        "topic": {"display_name": "Harry Potter", "slug": "harry-potter"},
        "questions": [
            {
                "text": "Which house suits you?",
                "options": [
                    {"text": "Brave and daring like Gryffindor"},
                    {"text": "Cunning and ambitious like Slytherin"},
                ],
            }
        ],
    }


async def test_generator_enriches_additively(sqlite_db_session: AsyncSession):
    client = _FakeClient()
    ledger = FalLedger(sqlite_db_session, config=_Budget())
    gen = QaImageGenerator(
        session=sqlite_db_session, ledger=ledger, client=client, image_gen_cfg=_ImageGenCfg()
    )
    art = _artefact()
    stats = await gen.enrich(art)

    q = art["questions"][0]
    # 1 question stem + 2 options = 3 generated images.
    assert stats.generated == 3
    assert q.get("image_url", "").startswith("https://fal.media/")
    assert q.get("image_alt")
    for opt in q["options"]:
        assert opt["image_url"].startswith("https://fal.media/")
        assert "Harry Potter" in opt["image_alt"]
    # The text fields are untouched (additive only).
    assert q["text"] == "Which house suits you?"


async def test_generator_skips_when_no_topic(sqlite_db_session: AsyncSession):
    client = _FakeClient()
    gen = QaImageGenerator(
        session=sqlite_db_session,
        ledger=FalLedger(sqlite_db_session, config=_Budget()),
        client=client,
        image_gen_cfg=_ImageGenCfg(),
    )
    art = {"questions": [{"text": "x", "options": [{"text": "y"}]}]}  # no topic
    stats = await gen.enrich(art)
    assert stats.generated == 0
    assert client.calls == []
    assert "image_url" not in art["questions"][0]


async def test_generator_reuses_dedup_asset(sqlite_db_session: AsyncSession):
    """A pre-existing media_assets row for the same prompt is reused — $0, no FAL."""
    from app.services.precompute.dedup import prompt_hash

    client = _FakeClient()
    cfg = _ImageGenCfg()

    # Pre-seed the media asset that the FIRST option's prompt would hash to.
    first_prompt = build_qa_image_prompt(
        topic="Harry Potter",
        text="Brave and daring like Gryffindor",
        kind="answer",
        style_suffix=cfg.style_suffix,
        negative_prompt=cfg.negative_prompt,
    )["prompt"]
    h = prompt_hash(first_prompt, provider=cfg.provider, model=cfg.model)
    sqlite_db_session.add(
        MediaAsset(
            content_hash="seed-ch-1",
            prompt_hash=h,
            storage_provider="fal",
            storage_uri="https://fal.media/cached.png",
            prompt_payload={"prompt": first_prompt},
        )
    )
    await sqlite_db_session.flush()

    gen = QaImageGenerator(
        session=sqlite_db_session,
        ledger=FalLedger(sqlite_db_session, config=_Budget()),
        client=client,
        image_gen_cfg=cfg,
    )
    art = _artefact()
    stats = await gen.enrich(art)

    opt0 = art["questions"][0]["options"][0]
    assert opt0["image_url"] == "https://fal.media/cached.png"  # reused, not generated
    assert stats.reused == 1
    # FAL was NOT called for the reused string.
    assert first_prompt not in client.calls
    # A 'reused' audit row (0 cost) exists.
    rows = (await sqlite_db_session.execute(
        __import__("sqlalchemy").select(FalSpendLedger)
    )).scalars().all()
    assert any(r.status == "reused" and r.cost_cents == 0 for r in rows)


class _FakeGate:
    """Routes a string to generation iff its text contains a flagged keyword."""

    def __init__(self, concrete_words):
        self.concrete_words = set(concrete_words)
        self.seen = []

    async def score(self, text):
        from app.services.icons.relevance_gate import GateDecision

        self.seen.append(text)
        low = (text or "").lower()
        hit = any(w in low for w in self.concrete_words)
        return GateDecision(
            generate=hit,
            reason="concrete" if hit else "abstract",
            concrete_sim=0.6 if hit else 0.3,
            abstract_sim=0.3 if hit else 0.5,
        )


async def test_gate_routes_abstract_strings_away(sqlite_db_session: AsyncSession):
    """The relevance gate must keep FAL spend OFF abstract strings: only the
    keyword-bearing (concrete) options generate; the rest fall back ($0)."""
    client = _FakeClient()
    gate = _FakeGate(concrete_words={"gryffindor", "slytherin"})
    gen = QaImageGenerator(
        session=sqlite_db_session,
        ledger=FalLedger(sqlite_db_session, config=_Budget()),
        client=client,
        image_gen_cfg=_ImageGenCfg(),
        gate=gate,
    )
    art = {
        "topic": {"display_name": "Harry Potter", "slug": "harry-potter"},
        "questions": [
            {
                "text": "Which house suits you?",  # abstract stem -> gated out
                "options": [
                    {"text": "Brave and daring like Gryffindor"},  # concrete -> gen
                    {"text": "Cunning and ambitious like Slytherin"},  # concrete -> gen
                    {"text": "I just go with my gut"},  # abstract -> gated out
                ],
            }
        ],
    }
    stats = await gen.enrich(art)

    assert stats.generated == 2  # only the two concrete options
    assert stats.gated_out == 2  # the stem + the abstract option
    assert len(client.calls) == 2  # FAL called only twice
    q = art["questions"][0]
    assert "image_url" not in q  # abstract stem got no image
    assert q["options"][0].get("image_url", "").startswith("https://fal.media/")
    assert q["options"][1].get("image_url", "").startswith("https://fal.media/")
    assert "image_url" not in q["options"][2]  # abstract option fell back


async def test_qa_style_suffix_override_used_in_prompt(sqlite_db_session: AsyncSession):
    """The Q&A scene style_suffix override (not the character 'portrait' one)
    must appear in the generated prompt when provided."""
    client = _FakeClient()
    gen = QaImageGenerator(
        session=sqlite_db_session,
        ledger=FalLedger(sqlite_db_session, config=_Budget()),
        client=client,
        image_gen_cfg=_ImageGenCfg(),
        style_suffix="flat illustrated scene, simple background, no text",
    )
    await gen.enrich(_artefact())
    assert client.calls, "expected at least one FAL call"
    assert all(
        "flat illustrated scene, simple background, no text" in p
        for p in client.calls
    )
    # The character-path 'portrait' suffix from _ImageGenCfg is NOT used.
    assert all("flat illustrated, no text" not in p for p in client.calls)


async def test_gate_none_attempts_every_string(sqlite_db_session: AsyncSession):
    """gate=None preserves legacy behaviour (attempt every string)."""
    client = _FakeClient()
    gen = QaImageGenerator(
        session=sqlite_db_session,
        ledger=FalLedger(sqlite_db_session, config=_Budget()),
        client=client,
        image_gen_cfg=_ImageGenCfg(),
        gate=None,
    )
    stats = await gen.enrich(_artefact())
    assert stats.generated == 3
    assert stats.gated_out == 0


async def test_generator_persists_media_asset_for_cross_build_dedup(
    sqlite_db_session: AsyncSession,
):
    """A generated image writes a media_assets row so a SECOND build reuses it
    ($0, no FAL) — closing the cross-build dedup loop."""
    from sqlalchemy import func, select

    cfg = _ImageGenCfg()
    # First build: generate (writes media_assets rows).
    client1 = _FakeClient()
    gen1 = QaImageGenerator(
        session=sqlite_db_session,
        ledger=FalLedger(sqlite_db_session, config=_Budget()),
        client=client1,
        image_gen_cfg=cfg,
    )
    art1 = _artefact()
    stats1 = await gen1.enrich(art1)
    assert stats1.generated == 3
    n_assets = (
        await sqlite_db_session.execute(select(func.count()).select_from(MediaAsset))
    ).scalar_one()
    assert n_assets == 3  # one row per generated string

    # Second build: SAME topic+strings => every string dedups, $0, no FAL.
    client2 = _FakeClient()
    gen2 = QaImageGenerator(
        session=sqlite_db_session,
        ledger=FalLedger(sqlite_db_session, config=_Budget()),
        client=client2,
        image_gen_cfg=cfg,
    )
    art2 = _artefact()
    stats2 = await gen2.enrich(art2)
    assert stats2.reused == 3
    assert stats2.generated == 0
    assert client2.calls == []  # FAL never called on the second build


async def test_generator_falls_back_when_cap_blocks(sqlite_db_session: AsyncSession):
    """When the cap is exhausted mid-build, remaining strings get NO image (so the
    generic-icon fallback can still cover them) — generation never overruns."""
    client = _FakeClient()
    # Cap fits exactly one image.
    cfg = _Budget(cap_usd=0.011, cost_per_image_usd=0.011, enforce=True)
    gen = QaImageGenerator(
        session=sqlite_db_session,
        ledger=FalLedger(sqlite_db_session, config=cfg),
        client=client,
        image_gen_cfg=_ImageGenCfg(),
    )
    art = _artefact()
    stats = await gen.enrich(art)

    assert stats.generated == 1  # only the first string fit the cap
    assert stats.blocked >= 1
    assert len(client.calls) == 1  # FAL called exactly once
    # The first target got an image; later ones did not (free for icon fallback).
    q = art["questions"][0]
    bound = [t for t in (q, *q["options"]) if t.get("image_url")]
    assert len(bound) == 1
