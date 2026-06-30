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


# Brand names ARE now passed verbatim — FAL handles licensing on its side.
# (Was: "omits_verbatim_category_when_media".)
def test_character_prompt_passes_brand_through(builders, sample_profile):
    out = builders.build_character_image_prompt(
        sample_profile,
        category="Harry Potter",
        analysis={"is_media": True},
        style_suffix=STYLE,
        negative_prompt=NEG,
    )
    assert "Harry Potter" in out["prompt"]
    assert "Hermione Granger" in out["prompt"]


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


# Synopsis builder now always echoes the topic verbatim — FAL handles
# licensing on its side. (Was: "is_abstract_for_media".)
def test_synopsis_prompt_includes_topic_for_media(builders):
    from app.models.api import Synopsis
    s = Synopsis(title="Which Hogwarts House?", summary="A magical sorting quiz.")
    out = builders.build_synopsis_image_prompt(
        s, category="Harry Potter",
        analysis={"is_media": True},
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "Harry Potter" in out["prompt"]
    # Blackbox #2 — the wide synopsis hero is reframed as a universe-first
    # establishing scene and uses the SCENE-framed style suffix, NOT the
    # character "portrait" suffix the caller passes.
    assert "In the world of Harry Potter" in out["prompt"]
    assert builders.SCENE_STYLE_SUFFIX in out["prompt"]
    assert STYLE not in out["prompt"]  # the portrait suffix must NOT leak in


def test_synopsis_prompt_includes_summary_for_non_media(builders):
    from app.models.api import Synopsis
    s = Synopsis(title="Which Mountain Are You?", summary="Find your inner peak.")
    out = builders.build_synopsis_image_prompt(
        s, category="Mountains", analysis={"is_media": False},
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "Mountains" in out["prompt"] or "mountain" in out["prompt"].lower()


# AC-IMG-5 / AC-UX-2026-05-01 — result builder anchors on the matched
# character's NAME + CATEGORY only. Verbose `short_description` /
# `profile_text` snippets were intentionally dropped from the prompt
# body because they caused the image model to drift away from the
# canonical character likeness (UX feedback: generated portraits often
# didn't resemble the actual character).
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
    # Prompt anchors on the matched character name + category.
    assert "The Architect" in out["prompt"]
    assert "MBTI Types" in out["prompt"]
    # Verbose descriptors must NOT leak through — they bias the model
    # toward generic illustration and away from the named character.
    assert "Methodical strategic thinker" not in out["prompt"]
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


# AC-UX-2026-05-01 — every result prompt must carry BOTH the style
# anchor (so generations share a visual language across the series)
# AND the negative prompt (so we keep `text, watermark, logo, blurry`
# out of the character portrait). Tightening item 1 stripped verbose
# descriptors; this test pins that the anchor + negative prompt did
# NOT also get accidentally dropped.
def test_result_prompt_always_includes_style_anchor_and_negative(builders):
    from app.models.api import FinalResult
    r = FinalResult(title="You are The Architect", description="A planner at heart.")
    chars = [{"name": "The Architect", "short_description": "x", "profile_text": "y"}]

    matched = builders.build_result_image_prompt(
        r, category="MBTI", character_set=chars,
        style_suffix=STYLE, negative_prompt=NEG,
    )
    fallback = builders.build_result_image_prompt(
        r, category="MBTI", character_set=[],
        style_suffix=STYLE, negative_prompt=NEG,
    )

    for out in (matched, fallback):
        assert STYLE in out["prompt"], out["prompt"]
        assert builders.STYLE_ANCHOR in out["prompt"], out["prompt"]
        # Blackbox #2 — the result hero now appends face-specific negatives onto
        # the caller's negative prompt (it renders a face at 1024px via FLUX dev).
        assert out["negative_prompt"].startswith(NEG)
        assert "deformed face" in out["negative_prompt"]
        assert "asymmetric eyes" in out["negative_prompt"]


# AC-UX-2026-05-01 — FAL handles long prompts but our 600-char budget
# is what keeps generations fast + on-brand. Even worst-case inputs
# (long character name + long category) must respect the cap and must
# preserve the STYLE_ANCHOR (the anchor is appended last and must
# survive truncation of the head).
def test_result_prompt_respects_600_char_budget_under_worst_case(builders):
    from app.models.api import FinalResult
    long_name = "Sir Reginald Archibald Pemberton-Smythe " * 20  # ~800 chars
    long_cat = "Late-Victorian Steam-Era Country-House Detective Novels"
    r = FinalResult(title=f"You are {long_name}", description="x")
    out = builders.build_result_image_prompt(
        r,
        category=long_cat,
        character_set=[{
            "name": long_name,
            "short_description": "x",
            "profile_text": "y",
        }],
        style_suffix=STYLE,
        negative_prompt=NEG,
    )
    # Budget honored.
    assert len(out["prompt"]) <= builders._MAX_PROMPT_CHARS, (
        f"Prompt was {len(out['prompt'])} chars (cap is "
        f"{builders._MAX_PROMPT_CHARS})"
    )
    # Anchor preserved (appended last; never truncated).
    assert builders.STYLE_ANCHOR in out["prompt"]


# Regression guard for image fidelity. The previous prompt body
# crammed `short_description` and `profile_text` into the FAL request,
# which made generations drift from the canonical character. The fix
# anchors the body on NAME + CATEGORY only. Pinning this both ways:
# verbose strings must NOT appear, and the canonical anchor MUST.
def test_result_prompt_body_uses_name_and_category_only(builders):
    from app.models.api import FinalResult
    r = FinalResult(title="You are Dumbledore", description="Wise and kind.")
    chars = [{
        "name": "Dumbledore",
        "short_description": "Tall wizard with a silver beard and half-moon glasses",
        "profile_text": "Headmaster of Hogwarts; favours lemon drops; cryptic mentor.",
    }]
    out = builders.build_result_image_prompt(
        r, category="Harry Potter", character_set=chars,
        style_suffix=STYLE, negative_prompt=NEG,
    )

    assert "Dumbledore" in out["prompt"]
    assert "Harry Potter" in out["prompt"]
    # Verbose descriptors must NOT leak through (they bias the image
    # model away from the actual named character).
    assert "silver beard" not in out["prompt"]
    assert "half-moon glasses" not in out["prompt"]
    assert "lemon drops" not in out["prompt"]
    assert "Headmaster" not in out["prompt"]
