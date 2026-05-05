"""AC-PROD-R6-CHAR-DESC-1 — CharacterProfile.short_description length cap.

Updated by AC-PROD-R12-CHAR-DESC-1: the cap is now SOFT (truncating
field validator) instead of HARD (ValidationError). Rejecting the whole
profile because the LLM overshot by a few chars caused user-visible
agent retry storms. We preserve the FE 240-char layout contract by
silently right-stripping at the boundary.
"""

from __future__ import annotations

from app.agent.schemas import CharacterProfile


def test_short_description_within_cap_is_accepted() -> None:
    cp = CharacterProfile(
        name="Alpha",
        short_description="A bold, calculating strategist who reads the room before speaking.",
        profile_text="...",
    )
    assert cp.short_description.startswith("A bold")


def test_short_description_at_cap_is_accepted() -> None:
    desc = "x" * 240
    cp = CharacterProfile(name="Alpha", short_description=desc, profile_text="...")
    assert len(cp.short_description) == 240


def test_short_description_exceeding_cap_is_truncated() -> None:
    """AC-PROD-R12-CHAR-DESC-1 — overshoot is silently truncated, not rejected."""
    desc = "x" * 320
    cp = CharacterProfile(name="Alpha", short_description=desc, profile_text="...")
    assert len(cp.short_description) == 240
    assert cp.short_description == "x" * 240


def test_short_description_truncation_rstrips_trailing_whitespace() -> None:
    # 238 'x' + 2 spaces + tail; truncation point lands at 240, then rstrip.
    desc = ("x" * 238) + "  trailing"
    cp = CharacterProfile(name="Alpha", short_description=desc, profile_text="...")
    assert len(cp.short_description) <= 240
    assert not cp.short_description.endswith(" ")


def test_empty_short_description_still_allowed() -> None:
    cp = CharacterProfile(name="Alpha", short_description="", profile_text="...")
    assert cp.short_description == ""

