"""AC-PROD-R6-CHAR-DESC-1 — CharacterProfile.short_description length cap."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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


def test_short_description_exceeding_cap_is_rejected() -> None:
    desc = "x" * 241
    with pytest.raises(ValidationError):
        CharacterProfile(name="Alpha", short_description=desc, profile_text="...")


def test_empty_short_description_still_allowed() -> None:
    # Default-empty contract preserved (existing call sites pass "" until LLM
    # back-fills). Only the upper bound is new in Round 6.
    cp = CharacterProfile(name="Alpha", short_description="", profile_text="...")
    assert cp.short_description == ""
