"""Hook flag-gate tests — the #1 requirement: flag-OFF is a strict no-op.

Covers:
  - Flag OFF: ``maybe_bind_icons`` returns the artefact UNCHANGED (same object),
    binds nothing, and NEVER imports the embedder / binder / fastembed.
  - Flag ON: the binder resolves icon ids from the seeded ``icon_assets`` table
    and attaches them ADDITIVELY to the artefact's Q&A; below-tau strings get no
    icon; existing fields are untouched.

The flag-ON path injects a deterministic fake embedder (monkeypatched onto the
embedder module) so the test is fast and does not load a model.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import IconAsset
from app.services.icons.hook import maybe_bind_icons
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


class _Images:
    def __init__(self, enabled, tau=0.5, query_prefix=""):
        self.qa_icons_enabled = enabled
        self.tau = tau
        self.query_prefix = query_prefix


class _Settings:
    def __init__(self, images):
        self.images = images


def _artefact():
    return {
        "questions": [
            {
                "question_text": "Which classic starter are you?",
                "options": [
                    {"text": "a fire type with a burning tail"},
                    {"text": "a water type that defends with its shell"},
                ],
            }
        ]
    }


async def _seed_two_icons(session: AsyncSession):
    # Two orthogonal 384-dim icons so argmax is deterministic with the fake embed.
    fire = [0.0] * 384
    fire[0] = 1.0
    water = [0.0] * 384
    water[1] = 1.0
    session.add(IconAsset(id="fire", lucide_name="flame", concept="fire",
                          caption="c", palette_variant="amber", embedding=fire))
    session.add(IconAsset(id="water", lucide_name="droplet", concept="water",
                          caption="c", palette_variant="sea", embedding=water))
    await session.flush()


# ---------------------------------------------------------------------------
# FLAG OFF — strict no-op (the #1 requirement)
# ---------------------------------------------------------------------------

async def test_flag_off_is_strict_noop(sqlite_db_session: AsyncSession):
    artefact = _artefact()
    out, n = await maybe_bind_icons(
        sqlite_db_session, artefact, settings_obj=_Settings(_Images(enabled=False))
    )

    assert n == 0
    assert out is artefact  # same object, untouched
    assert "icon_id" not in out["questions"][0]
    assert "icon_id" not in out["questions"][0]["options"][0]


def test_flag_off_does_not_import_embedder_or_fastembed():
    """The strict-no-op contract, asserted in a CLEAN interpreter so it is not
    polluted by other tests that legitimately import the embedder.

    A flag-off ``maybe_bind_icons`` call must NOT import the embedder, the
    binder, the index loader, or fastembed.
    """
    script = textwrap.dedent(
        """
        import sys, asyncio
        from app.services.icons.hook import maybe_bind_icons

        class _Imgs:
            qa_icons_enabled = False
            tau = 0.5
            query_prefix = ""
        class _S:
            images = _Imgs()

        art = {"questions": [{"question_text": "x", "options": [{"text": "y"}]}]}
        out, n = asyncio.run(maybe_bind_icons(db=None, artefact=art, settings_obj=_S()))
        assert n == 0 and out is art, (n, out)
        for mod in ("fastembed",
                    "app.services.icons.embedder",
                    "app.services.icons.binder",
                    "app.services.icons.index"):
            assert mod not in sys.modules, f"LEAKED: {mod}"
        print("STRICT_NOOP_OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr[-2000:]!r}"
    assert "STRICT_NOOP_OK" in proc.stdout


async def test_flag_off_defaults_from_real_settings(sqlite_db_session: AsyncSession):
    """With the real (default) settings — flag is off in appconfig.local.yaml —
    the hook is a no-op."""
    artefact = _artefact()
    out, n = await maybe_bind_icons(sqlite_db_session, artefact)  # settings_obj=None -> real settings
    assert n == 0
    assert out is artefact


# ---------------------------------------------------------------------------
# FLAG ON — additive icon ids
# ---------------------------------------------------------------------------

async def test_flag_on_attaches_icon_ids(sqlite_db_session: AsyncSession, monkeypatch):
    await _seed_two_icons(sqlite_db_session)

    # Fake embedder: route "fire ..." -> fire axis, "water ..." -> water axis,
    # everything else (incl. the question stem) -> diagonal (below tau).
    async def _fake_raw_embed(text: str):
        t = text.lower()
        if "fire" in t:
            v = [0.0] * 384
            v[0] = 1.0
            return v
        if "water" in t:
            v = [0.0] * 384
            v[1] = 1.0
            return v
        # ambiguous -> 45° between the two seeded axes -> cosine ~0.707
        v = [0.0] * 384
        v[0] = 1.0
        v[1] = 1.0
        return v

    # Patch the embedder module's raw_embed (the hook imports it lazily).
    import app.services.icons.embedder as emb
    monkeypatch.setattr(emb, "raw_embed", _fake_raw_embed, raising=True)

    artefact = _artefact()
    out, n = await maybe_bind_icons(
        sqlite_db_session, artefact,
        settings_obj=_Settings(_Images(enabled=True, tau=0.9)),
    )

    q = out["questions"][0]
    opts = q["options"]
    # Both concrete options route above tau=0.9 (cosine 1.0) and get an icon.
    assert opts[0]["icon_id"] == "fire"
    assert opts[0]["icon_palette_variant"] == "amber"
    assert "icon_similarity" in opts[0]
    assert opts[1]["icon_id"] == "water"
    # The ambiguous question stem (cosine ~0.707 < 0.9) gets NO icon.
    assert "icon_id" not in q
    assert n == 2


async def test_flag_on_empty_index_is_safe(sqlite_db_session: AsyncSession, monkeypatch):
    # No icons seeded -> binder has nothing -> no-op, no error.
    import app.services.icons.embedder as emb

    async def _fake(text: str):
        return [1.0] + [0.0] * 383

    monkeypatch.setattr(emb, "raw_embed", _fake, raising=True)
    artefact = _artefact()
    out, n = await maybe_bind_icons(
        sqlite_db_session, artefact,
        settings_obj=_Settings(_Images(enabled=True, tau=0.1)),
    )
    assert n == 0
    assert "icon_id" not in out["questions"][0]["options"][0]


async def test_flag_on_does_not_overwrite_existing_icon(sqlite_db_session: AsyncSession, monkeypatch):
    await _seed_two_icons(sqlite_db_session)
    import app.services.icons.embedder as emb

    async def _fake(text: str):
        v = [0.0] * 384
        v[0] = 1.0
        return v  # always -> fire

    monkeypatch.setattr(emb, "raw_embed", _fake, raising=True)
    artefact = _artefact()
    artefact["questions"][0]["options"][0]["icon_id"] = "preexisting"
    out, _ = await maybe_bind_icons(
        sqlite_db_session, artefact,
        settings_obj=_Settings(_Images(enabled=True, tau=0.1)),
    )
    # Idempotent: a pre-set icon_id is not clobbered.
    assert out["questions"][0]["options"][0]["icon_id"] == "preexisting"
