# tests/unit/agent/tools/test_blended_profile_tool.py

"""
Unit tests for app.agent.tools.content_creation_tools.write_blended_profile

The blended-profile writer is the DISC pilot's final-result generator. These
tests exercise the real tool against a faked LLM (no network) and assert:
  - the happy path produces a result_kind="blended_profile" with a populated
    profile (palette-ordered dimensions + primary/secondary + narrative);
  - the canonical palette is authoritative (bogus/missing members repaired,
    emphasis clamped, invalid primary ignored);
  - a thin narrative is replaced by a composed substantive one;
  - an LLM error still yields a valid blended profile;
  - an empty palette degrades to the single-character writer.
"""

from types import SimpleNamespace

import pytest

from app.agent.tools import content_creation_tools as ctools
from app.models.api import FinalResult

# Hit the real tool implementations, not the global stubs.
pytestmark = pytest.mark.no_tool_stubs


_DISC_PALETTE = ["Dominance", "Influence", "Steadiness", "Conscientiousness"]


class _DummyPrompt:
    def invoke(self, payload):
        return SimpleNamespace(messages=["dummy"])


def _long_narrative() -> str:
    """A narrative that clears both the paragraph and character floors."""
    para = (
        "You lead with drive and back it with rigour, a combination that shows up "
        "in the answers you gave throughout this quiz and the choices you kept making. "
    )
    return (para + "\n\n") * 3 + ("x" * 120)


def _patch_prompt_and_schema(monkeypatch):
    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: _DummyPrompt(), raising=True
    )
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)


@pytest.mark.asyncio
async def test_write_blended_profile_happy_path(monkeypatch):
    _patch_prompt_and_schema(monkeypatch)

    async def fake_invoke_structured(**_):
        return {
            "title": "You're a D/C blend",
            "dimensions": [
                {"name": "Dominance", "emphasis": 82, "blurb": "You push for results."},
                {"name": "Conscientiousness", "emphasis": 61, "blurb": "You value accuracy."},
                {"name": "Influence", "emphasis": 30, "blurb": "You can rally people."},
                {"name": "Steadiness", "emphasis": 18, "blurb": "You prefer steady change."},
            ],
            "primary": "Dominance",
            "secondary": "Conscientiousness",
            "narrative": _long_narrative(),
        }

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.write_blended_profile.ainvoke(
        {
            "winning_character": {"name": "X", "image_url": "http://i"},
            "quiz_history": [],
            "dimensions": _DISC_PALETTE,
            "category": "DISC",
            "creativity_mode": "balanced",
        }
    )

    assert isinstance(out, FinalResult)
    assert out.result_kind == "blended_profile"
    assert out.profile is not None
    # All canonical dimensions present, in palette order, names intact.
    assert [d.name for d in out.profile.dimensions] == _DISC_PALETTE
    assert out.profile.primary == "Dominance"
    assert out.profile.secondary == "Conscientiousness"
    # Narrative clears the floor and is mirrored into description.
    assert len(out.profile.narrative) >= ctools.MIN_BLEND_NARRATIVE_CHARS
    assert out.description == out.profile.narrative
    assert out.image_url == "http://i"


@pytest.mark.asyncio
async def test_write_blended_profile_aligns_to_palette_and_clamps(monkeypatch):
    """The model can't invent/drop members or emit out-of-range emphasis."""
    _patch_prompt_and_schema(monkeypatch)

    async def fake_invoke_structured(**_):
        return {
            "title": "Blend",
            "dimensions": [
                {"name": "Dominance", "emphasis": 999, "blurb": "high"},  # clamp -> 100
                {"name": "NotARealStyle", "emphasis": 50, "blurb": "ignored"},  # bogus
                # Steadiness/Influence/Conscientiousness omitted -> neutral default
            ],
            "primary": "NotARealStyle",  # invalid -> falls back to emphasis order
            "secondary": None,
            "narrative": _long_narrative(),
        }

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.write_blended_profile.ainvoke(
        {
            "winning_character": {"name": "X"},
            "quiz_history": [],
            "dimensions": _DISC_PALETTE,
            "category": "DISC",
        }
    )

    names = [d.name for d in out.profile.dimensions]
    assert names == _DISC_PALETTE  # exactly the palette; no bogus member leaked in
    dom = next(d for d in out.profile.dimensions if d.name == "Dominance")
    assert dom.emphasis == 100  # clamped from 999
    # Invalid LLM primary ignored -> highest emphasis (Dominance) wins.
    assert out.profile.primary == "Dominance"
    # Every emphasis is within range.
    assert all(0 <= d.emphasis <= 100 for d in out.profile.dimensions)


@pytest.mark.asyncio
async def test_write_blended_profile_thin_narrative_gets_fallback(monkeypatch):
    """A too-short narrative is replaced by a composed, substantive one."""
    _patch_prompt_and_schema(monkeypatch)

    async def fake_invoke_structured(**_):
        return {
            "title": "Blend",
            "dimensions": [{"name": "Dominance", "emphasis": 90, "blurb": "b"}],
            "primary": "Dominance",
            "secondary": None,
            "narrative": "too short",
        }

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.write_blended_profile.ainvoke(
        {
            "winning_character": {"name": "X"},
            "quiz_history": [],
            "dimensions": _DISC_PALETTE,
            "category": "DISC",
        }
    )

    assert out.result_kind == "blended_profile"
    assert len(out.profile.narrative) >= ctools.MIN_BLEND_NARRATIVE_CHARS
    assert (
        ctools._count_paragraphs(out.profile.narrative)
        >= ctools.MIN_BLEND_NARRATIVE_PARAGRAPHS
    )


@pytest.mark.asyncio
async def test_write_blended_profile_exception_fallback(monkeypatch):
    """LLM error still yields a valid, palette-consistent blended profile."""
    _patch_prompt_and_schema(monkeypatch)

    async def boom(**_):
        raise RuntimeError("llm down")

    monkeypatch.setattr(ctools, "invoke_structured", boom, raising=True)

    out = await ctools.write_blended_profile.ainvoke(
        {
            "winning_character": {"name": "X", "image_url": "http://i"},
            "quiz_history": [],
            "dimensions": _DISC_PALETTE,
            "category": "DISC",
        }
    )

    assert out.result_kind == "blended_profile"
    assert [d.name for d in out.profile.dimensions] == _DISC_PALETTE
    assert out.profile.primary == "Dominance"
    assert len(out.profile.narrative) >= ctools.MIN_BLEND_NARRATIVE_CHARS
    assert out.image_url == "http://i"


@pytest.mark.asyncio
async def test_write_blended_profile_empty_palette_falls_back_to_single(monkeypatch):
    """No canonical palette -> we degrade to the single-character writer."""
    _patch_prompt_and_schema(monkeypatch)

    async def fake_invoke_structured(**_):
        return FinalResult(title="Custom", description="x" * 420, image_url=None)

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.write_blended_profile.ainvoke(
        {
            "winning_character": {"name": "Hero", "image_url": "http://h"},
            "quiz_history": [],
            "dimensions": [],
            "category": "DISC",
        }
    )

    # Single-character shape (no blended profile) when there's nothing to blend.
    # result_kind is left unset (None) so the wire payload stays byte-identical.
    assert out.result_kind is None
    assert out.profile is None
