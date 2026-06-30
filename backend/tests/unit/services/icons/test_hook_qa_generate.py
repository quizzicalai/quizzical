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


class _Images:
    def __init__(self, icons=True, generate=False, tau=0.9, query_prefix=""):
        self.qa_icons_enabled = icons
        self.qa_generated_images_enabled = generate
        self.tau = tau
        self.query_prefix = query_prefix
        self.fal_budget = _Budget()


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
