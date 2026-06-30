# tests/unit/agent/test_blended_pilot_gate.py

"""
Unit tests for the blended-outcome PILOT allowlist gate.

`is_blended_pilot_topic` decides whether a topic gets a true blended-profile
result. The contract that protects every existing quiz:
  - DISC (and its aliases / phrasings) -> True with the default allowlist;
  - Big Five is canonically "blended" too but is NOT in the default pilot ->
    False (stays single-character until the owner adds it);
  - single-mode and non-canonical topics -> always False;
  - an empty/None allowlist disables the pilot entirely;
  - the allowlist is honoured (adding "big five" flips Big Five to True).
"""

import pytest

from app.agent.canonical_sets import is_blended_pilot_topic
from app.core.config import settings

DEFAULT_ALLOWLIST = ["disc"]


@pytest.mark.parametrize(
    "topic",
    ["disc", "DISC", "DISC Styles", "What is my DISC type", "disc profiles"],
)
def test_disc_is_pilot_with_default_allowlist(topic):
    assert is_blended_pilot_topic(topic, DEFAULT_ALLOWLIST) is True


@pytest.mark.parametrize(
    "topic",
    ["Big Five", "big five", "ocean", "ffm", "Big Five Personality Traits"],
)
def test_big_five_is_not_pilot_by_default(topic):
    # Big Five is outcome_mode="blended" but excluded from the default pilot;
    # it must keep the single-character path.
    assert is_blended_pilot_topic(topic, DEFAULT_ALLOWLIST) is False


@pytest.mark.parametrize(
    "topic",
    ["Harry Potter", "Hogwarts Houses", "Type of Dog", "Gilmore Girls"],
)
def test_single_and_noncanonical_topics_never_pilot(topic):
    assert is_blended_pilot_topic(topic, DEFAULT_ALLOWLIST) is False


@pytest.mark.parametrize("allowlist", [[], None])
def test_empty_allowlist_disables_pilot(allowlist):
    assert is_blended_pilot_topic("disc", allowlist) is False


def test_owner_can_widen_allowlist_to_big_five():
    widened = ["disc", "big five"]
    assert is_blended_pilot_topic("Big Five", widened) is True
    # DISC still works alongside it.
    assert is_blended_pilot_topic("disc", widened) is True


def test_default_settings_allowlist_is_disc_only():
    # The shipped default must be DISC-only so the live feature is gated tight.
    assert settings.quiz.blended_outcome_pilot == ["disc"]
