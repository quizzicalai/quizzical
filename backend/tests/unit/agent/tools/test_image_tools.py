# tests/unit/agent/tools/test_image_tools.py
"""Tests for FAL prompt builders (§7.8 / AC-IMG-3..5)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


STYLE = "flat illustrated portrait, soft lighting, muted palette, no text"
NEG = "text, watermark, logo, signature, blurry, deformed, low quality"


@pytest.fixture
def builders():
    from app.agent.tools import image_tools as it
    return it


@pytest.fixture
def sample_profile():
    from app.models.api import CharacterProfile
    return CharacterProfile(
        name="Hermione Granger",
        short_description="Brilliant young witch with bushy brown hair and a determined expression",
        profile_text="Studious and loyal, she carries an armful of books at all times.",
    )


# AC-IMG-3 — character builder includes short_description + style_suffix
def test_character_prompt_includes_description_and_style(builders, sample_profile):
    out = builders.build_character_image_prompt(
        sample_profile,
        category="Harry Potter",
        analysis={"is_media": True},
        style_suffix=STYLE,
        negative_prompt=NEG,
    )
    assert isinstance(out, dict)
    assert "bushy brown hair" in out["prompt"]
    assert STYLE in out["prompt"]
    assert out["negative_prompt"] == NEG


# AC-IMG-3 — IP names not echoed verbatim when is_media=True
def test_character_prompt_omits_verbatim_category_when_media(builders, sample_profile):
    out = builders.build_character_image_prompt(
        sample_profile,
        category="Harry Potter",
        analysis={"is_media": True},
        style_suffix=STYLE,
        negative_prompt=NEG,
    )
    assert "Harry Potter" not in out["prompt"]
    # Character name should also be replaced with a descriptive token.
    assert "Hermione Granger" not in out["prompt"]


# AC-IMG-3 — non-IP categories may include the category name
def test_character_prompt_allows_category_when_not_media(builders):
    from app.models.api import CharacterProfile
    p = CharacterProfile(
        name="The Architect",
        short_description="Methodical strategic thinker who plans every move",
        profile_text="...",
    )
    out = builders.build_character_image_prompt(
        p, category="MBTI Types",
        analysis={"is_media": False},
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "Methodical" in out["prompt"]


# AC-IMG-4 — synopsis builder is abstract for media
def test_synopsis_prompt_is_abstract_for_media(builders):
    from app.models.api import Synopsis
    s = Synopsis(title="Which Hogwarts House?", summary="A magical sorting quiz.")
    out = builders.build_synopsis_image_prompt(
        s, category="Harry Potter",
        analysis={"is_media": True},
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "Hogwarts" not in out["prompt"]
    assert "Harry Potter" not in out["prompt"]
    assert STYLE in out["prompt"]


def test_synopsis_prompt_includes_summary_for_non_media(builders):
    from app.models.api import Synopsis
    s = Synopsis(title="Which Mountain Are You?", summary="Find your inner peak.")
    out = builders.build_synopsis_image_prompt(
        s, category="Mountains", analysis={"is_media": False},
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "Mountains" in out["prompt"] or "mountain" in out["prompt"].lower()


# AC-IMG-5 — result builder prefers matched character description
def test_result_prompt_prefers_matched_character(builders):
    from app.models.api import FinalResult
    r = FinalResult(title="You are The Architect", description="A planner at heart.")
    chars = [
        {"name": "The Architect",
         "short_description": "Methodical strategic thinker with sharp eyes",
         "profile_text": "..."},
        {"name": "The Dreamer", "short_description": "Whimsical wanderer", "profile_text": "..."},
    ]
    out = builders.build_result_image_prompt(
        r, category="MBTI Types", character_set=chars,
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "Methodical strategic thinker" in out["prompt"]
    assert STYLE in out["prompt"]


def test_result_prompt_falls_back_to_description(builders):
    from app.models.api import FinalResult
    r = FinalResult(title="You are unique", description="A blend of curiosity and grit.")
    out = builders.build_result_image_prompt(
        r, category="Anything", character_set=[],
        style_suffix=STYLE, negative_prompt=NEG,
    )
    # No matched character, so should derive from description.
    assert "curiosity" in out["prompt"] or "grit" in out["prompt"]
