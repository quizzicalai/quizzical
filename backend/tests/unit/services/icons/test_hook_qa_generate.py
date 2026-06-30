"""Hook integration tests for same-universe generation (PRIORITY 2).

Covers:
  - The generation sub-flag (``qa_generated_images_enabled``) is STRICTLY
    downstream of ``qa_icons_enabled``: generation runs only when BOTH are on.
  - When generation is off (but icons on) the build behaves exactly as before
    (no FAL client constructed, no spend) — only the $0 icon binder runs.
  - When generation is on, images bind additively and the generic-icon binder
    still fills the rest (generated image is preferred; icon is fallback).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import FalSpendLedger
from app.services.icons.hook import maybe_bind_icons
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


class _Budget:
    cap_usd = 150.0
    cost_per_image_usd = 0.011
    enforce = True

    @property
    def cap_cents(self):
        return 15000

    @property
    def cost_per_image_cents(self):
        return 1.1


class _ImageGenCfg:
    provider = "fal"
    model = "fal-ai/flux/schnell"
    style_suffix = "flat illustrated, no text"
    negative_prompt = "text, watermark"


class _Gate:
    def __init__(self, enabled=False, margin=0.04, concrete_floor=0.20):
        self.enabled = enabled
        self.margin = margin
        self.concrete_floor = concrete_floor


class _Images:
    def __init__(self, icons=True, generate=False, tau=0.9, query_prefix="", gate=None):
        self.qa_icons_enabled = icons
        self.qa_generated_images_enabled = generate
        self.tau = tau
        self.query_prefix = query_prefix
        self.fal_budget = _Budget()
        # Default: gate DISABLED so the existing tests keep attempting every
        # string (the gate's routing quality is validated by the offline eval +
        # the dedicated gate/pipeline unit tests).
        self.relevance_gate = gate if gate is not None else _Gate(enabled=False)


class _Settings:
    def __init__(self, images):
        self.images = images
        self.image_gen = _ImageGenCfg()


def _artefact():
    return {
        "topic": {"display_name": "Harry Potter", "slug": "harry-potter"},
        "questions": [
            {
                "text": "Which trait fits you?",
                "options": [
                    {"text": "Brave"},
                    {"text": "Cunning"},
                ],
            }
        ],
    }


async def test_generation_off_runs_no_fal(sqlite_db_session: AsyncSession, monkeypatch):
    """Icons on, generation off => no FAL spend, no image_url bound by generation."""
    import app.services.icons.embedder as emb

    async def _fake(text):  # everything below tau -> no icon, keeps test focused
        v = [0.0] * 384
        v[0] = 1.0
        v[1] = 1.0
        return v

    monkeypatch.setattr(emb, "raw_embed", _fake, raising=True)

    art = _artefact()
    out, _ = await maybe_bind_icons(
        sqlite_db_session, art, settings_obj=_Settings(_Images(icons=True, generate=False))
    )
    # No FAL ledger rows were ever written.
    rows = (await sqlite_db_session.execute(
        __import__("sqlalchemy").select(FalSpendLedger)
    )).scalars().all()
    assert rows == []
    assert "image_url" not in out["questions"][0]


async def test_generation_on_binds_images(sqlite_db_session: AsyncSession, monkeypatch):
    """Both flags on => same-universe images bind via a faked FAL client."""
    import app.services.icons.embedder as emb
    import app.services.image_service as image_service

    async def _fake_embed(text):
        return [1.0] + [0.0] * 383

    class _FakeClient:
        async def generate(self, *, prompt, negative_prompt=None, seed=None):
            return "https://fal.media/x.png"

    monkeypatch.setattr(emb, "raw_embed", _fake_embed, raising=True)
    monkeypatch.setattr(image_service, "_client_singleton", _FakeClient(), raising=True)

    art = _artefact()
    out, _ = await maybe_bind_icons(
        sqlite_db_session, art, settings_obj=_Settings(_Images(icons=True, generate=True, tau=0.99)),
    )

    q = out["questions"][0]
    assert q["image_url"] == "https://fal.media/x.png"
    assert q["options"][0]["image_url"] == "https://fal.media/x.png"
    # Ledger recorded the charged generations.
    rows = (await sqlite_db_session.execute(
        __import__("sqlalchemy").select(FalSpendLedger)
    )).scalars().all()
    assert len([r for r in rows if r.status == "charged"]) == 3


async def test_relevance_gate_routes_abstract_away_via_hook(
    sqlite_db_session: AsyncSession, monkeypatch
):
    """With the gate ENABLED, the hook must keep FAL spend off abstract strings.

    A keyword-aware fake embedder: 'brave' leans concrete, everything else
    (incl. the anchors and the abstract stem/option) leans abstract — so only
    the concrete option generates; the rest fall back ($0)."""
    import app.services.icons.embedder as emb
    import app.services.icons.relevance_gate as rg
    import app.services.image_service as image_service

    # Reset the process-wide anchor cache so our fake embedder's anchors are used.
    rg._ANCHORS._concrete = None
    rg._ANCHORS._abstract = None
    rg._ANCHORS._key = None

    CONCRETE = [1.0] + [0.0] * 383
    ABSTRACT = [0.0, 1.0] + [0.0] * 382

    async def _fake_embed(text):
        if text in rg.CONCRETE_ANCHORS:
            return list(CONCRETE)
        if text in rg.ABSTRACT_ANCHORS:
            return list(ABSTRACT)
        return list(CONCRETE) if "dragon" in (text or "").lower() else list(ABSTRACT)

    class _FakeClient:
        def __init__(self):
            self.n = 0

        async def generate(self, *, prompt, negative_prompt=None, seed=None):
            self.n += 1
            return f"https://fal.media/{self.n}.png"

    fake_client = _FakeClient()
    monkeypatch.setattr(emb, "raw_embed", _fake_embed, raising=True)
    monkeypatch.setattr(image_service, "_client_singleton", fake_client, raising=True)

    art = {
        "topic": {"display_name": "Mythical Creature", "slug": "mythical-creature"},
        "questions": [
            {
                "text": "Which trait fits you best?",  # abstract stem
                "options": [
                    {"text": "A fierce dragon over a mountain"},  # concrete
                    {"text": "Quietly confident and reserved"},  # abstract
                ],
            }
        ],
    }
    out, _ = await maybe_bind_icons(
        sqlite_db_session,
        art,
        settings_obj=_Settings(
            _Images(icons=True, generate=True, tau=0.99, gate=_Gate(enabled=True))
        ),
    )

    q = out["questions"][0]
    assert "image_url" not in q  # abstract stem gated out
    assert q["options"][0]["image_url"].startswith("https://fal.media/")  # "Brave"
    assert "image_url" not in q["options"][1]  # "Cunning" gated out
    assert fake_client.n == 1  # FAL called exactly once
    rows = (await sqlite_db_session.execute(
        __import__("sqlalchemy").select(FalSpendLedger)
    )).scalars().all()
    assert len([r for r in rows if r.status == "charged"]) == 1


async def test_generation_failure_is_fail_open(sqlite_db_session: AsyncSession, monkeypatch):
    """If the generation pipeline raises, the build is unharmed and icons still run."""
    import app.services.icons.embedder as emb
    import app.services.icons.qa_pipeline as qa_pipeline

    async def _fake_embed(text):
        v = [0.0] * 384
        v[0] = 1.0
        v[1] = 1.0
        return v

    monkeypatch.setattr(emb, "raw_embed", _fake_embed, raising=True)

    class _BoomGen:
        def __init__(self, *a, **k):
            pass

        async def enrich(self, artefact):
            raise RuntimeError("boom")

    monkeypatch.setattr(qa_pipeline, "QaImageGenerator", _BoomGen, raising=True)

    art = _artefact()
    out, n = await maybe_bind_icons(
        sqlite_db_session, art, settings_obj=_Settings(_Images(icons=True, generate=True, tau=0.99)),
    )
    # No crash; artefact returned; no image bound by the failed generator.
    assert "image_url" not in out["questions"][0]
    assert n == 0
