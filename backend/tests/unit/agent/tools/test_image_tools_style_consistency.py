"""AC-IMG-STYLE-1..4 — image-prompt style consistency across builders.

Phase 7 (performance): user requirement is "low fidelity is fine SO LONG AS
the style is consistent and matches intended output". We enforce this by:
  1. A shared, immutable ``STYLE_ANCHOR`` constant present in every builder's
     output (synopsis / character / result), in addition to the configurable
     ``style_suffix``.
  2. A deterministic ``derive_seed`` helper used by the pipeline to pin FAL's
     RNG so identical (session, subject) pairs reproduce the same image.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


@pytest.fixture
def it():
    from app.agent.tools import image_tools as _it
    return _it


@pytest.fixture
def sample_profile():
    from app.models.api import CharacterProfile
    return CharacterProfile(
        name="The Sage",
        short_description="A wise mentor with kind eyes and a patient demeanor",
        profile_text="Speaks softly and carries deep knowledge of ancient lore.",
    )


@pytest.fixture
def sample_synopsis():
    from app.models.api import Synopsis
    return Synopsis(title="Which Sage Are You?", summary="A reflective journey.")


@pytest.fixture
def sample_result():
    from app.models.api import FinalResult
    return FinalResult(
        title="You are The Sage",
        description="Wise and patient.",
        image_url=None,
    )


STYLE_SUFFIX = "flat illustrated portrait, soft lighting, no text"
NEG = "text, watermark, blurry"


# AC-IMG-STYLE-1: STYLE_ANCHOR exists and is non-empty
def test_style_anchor_constant_exists(it):
    assert hasattr(it, "STYLE_ANCHOR"), (
        "image_tools.STYLE_ANCHOR must exist as the immutable cross-builder style hint."
    )
    assert isinstance(it.STYLE_ANCHOR, str)
    assert len(it.STYLE_ANCHOR.strip()) >= 8


# AC-IMG-STYLE-2: every builder includes the STYLE_ANCHOR verbatim
def test_character_prompt_includes_style_anchor(it, sample_profile):
    out = it.build_character_image_prompt(
        sample_profile, category="Wisdom", analysis={"is_media": False},
        style_suffix=STYLE_SUFFIX, negative_prompt=NEG,
    )
    assert it.STYLE_ANCHOR in out["prompt"]


def test_synopsis_prompt_includes_style_anchor(it, sample_synopsis):
    out = it.build_synopsis_image_prompt(
        sample_synopsis, category="Wisdom", analysis={"is_media": False},
        style_suffix=STYLE_SUFFIX, negative_prompt=NEG,
    )
    assert it.STYLE_ANCHOR in out["prompt"]


def test_result_prompt_includes_style_anchor(it, sample_result):
    out = it.build_result_image_prompt(
        sample_result, category="Wisdom", character_set=[],
        style_suffix=STYLE_SUFFIX, negative_prompt=NEG, analysis={"is_media": False},
    )
    assert it.STYLE_ANCHOR in out["prompt"]


# AC-IMG-STYLE-3: anchor is consistent (identical token sequence) across builders
def test_anchor_position_is_consistent_across_builders(it, sample_profile, sample_synopsis, sample_result):
    char_p = it.build_character_image_prompt(
        sample_profile, category="Wisdom", analysis={"is_media": False},
        style_suffix=STYLE_SUFFIX, negative_prompt=NEG,
    )["prompt"]
    syn_p = it.build_synopsis_image_prompt(
        sample_synopsis, category="Wisdom", analysis={"is_media": False},
        style_suffix=STYLE_SUFFIX, negative_prompt=NEG,
    )["prompt"]
    res_p = it.build_result_image_prompt(
        sample_result, category="Wisdom", character_set=[],
        style_suffix=STYLE_SUFFIX, negative_prompt=NEG, analysis={"is_media": False},
    )["prompt"]
    # All three must contain the anchor and the configurable style_suffix.
    for p in (char_p, syn_p, res_p):
        assert it.STYLE_ANCHOR in p
        assert STYLE_SUFFIX in p


# AC-IMG-STYLE-4: derive_seed is deterministic and stable
def test_derive_seed_is_deterministic(it):
    assert hasattr(it, "derive_seed"), (
        "image_tools.derive_seed(session_id, subject) must exist to pin FAL RNG "
        "for cross-image style consistency."
    )
    s1 = it.derive_seed("session-abc", "The Sage")
    s2 = it.derive_seed("session-abc", "The Sage")
    s3 = it.derive_seed("session-abc", "The Trickster")
    s4 = it.derive_seed("session-xyz", "The Sage")
    # Same inputs → same seed
    assert s1 == s2
    # Different subject within same session → different seed
    assert s1 != s3
    # Different session, same subject → different seed
    assert s1 != s4
    # Seeds fit in a uint32 (FAL accepts up to 2**32 - 1)
    for s in (s1, s2, s3, s4):
        assert isinstance(s, int)
        assert 0 <= s < 2**32
