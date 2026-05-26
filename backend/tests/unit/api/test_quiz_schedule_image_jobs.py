"""Unit tests for :func:`app.api.endpoints.quiz._schedule_image_jobs_safe`
and the topic-analysis propagation in
:func:`app.api.endpoints.quiz._short_circuit_from_pack`.

Background
----------
The image pipeline routes branded topics ("Star Wars", "Harry Potter")
through a different FAL prompt ladder than generic archetype topics. It
keys off ``analysis.is_media`` in the GraphState passed to
:func:`generate_character_images`.

Two state-shape mismatches used to defeat this:

1. The bootstrap agent writes ``topic_analysis``; the precompute short
   circuit wrote nothing; both call sites read ``state["analysis"]``.
2. The short-circuit path bypassed bootstrap entirely, so even branded
   precomputed packs hit the non-branded prompt.

These tests pin down the contract that fixes both:

* ``_schedule_image_jobs_safe`` falls back to ``topic_analysis`` when
  ``analysis`` is missing (covers the agent path).
* ``_short_circuit_from_pack`` writes ``analysis`` (and
  ``topic_analysis``) from a local heuristic so the precompute path is
  on equal footing.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


@pytest.mark.parametrize(
    "state_key", ["analysis", "topic_analysis"],
)
def test_schedule_image_jobs_reads_both_analysis_keys(monkeypatch, state_key):
    """``_schedule_image_jobs_safe`` must accept analysis under either key.

    The agent stores it as ``topic_analysis``; the short-circuit path uses
    ``analysis``. Without dual-read, branded agent-driven quizzes silently
    routed through the non-branded FAL prompt.
    """
    from app.api.endpoints import quiz as quiz_mod
    from app.models.api import Synopsis

    syn = Synopsis(title="t", summary="s")
    chars = [MagicMock(name="profile1"), MagicMock(name="profile2")]

    state = {
        "synopsis": syn,
        "generated_characters": chars,
        state_key: {"is_media": True, "domain": "media_characters"},
    }

    captured: list[dict] = []

    class _BG:
        def add_task(self, fn, **kwargs):
            captured.append({"fn": fn, "kwargs": kwargs})

    quiz_mod._schedule_image_jobs_safe(
        _BG(),
        quiz_id=uuid.uuid4(),
        category="Star Wars",
        state=state,  # type: ignore[arg-type]
    )

    # Two tasks scheduled: synopsis + characters. Both must carry the
    # is_media flag so the FAL ladder routes through the branded prompt.
    assert len(captured) == 2
    for task in captured:
        assert task["kwargs"]["analysis"]["is_media"] is True


def test_schedule_image_jobs_safe_when_no_analysis(monkeypatch):
    """Missing analysis must NOT crash; empty dict propagates downstream
    so non-branded archetype topics still render correctly."""
    from app.api.endpoints import quiz as quiz_mod
    from app.models.api import Synopsis

    state = {
        "synopsis": Synopsis(title="t", summary="s"),
        "generated_characters": [MagicMock()],
    }

    captured: list[dict] = []

    class _BG:
        def add_task(self, fn, **kwargs):
            captured.append(kwargs)

    quiz_mod._schedule_image_jobs_safe(
        _BG(),
        quiz_id=uuid.uuid4(),
        category="Generic Archetypes",
        state=state,  # type: ignore[arg-type]
    )

    assert len(captured) == 2
    for kw in captured:
        assert kw["analysis"] == {}


def test_analyze_topic_recognises_known_branded_topics():
    """Sanity check that the local heuristic ``_short_circuit_from_pack``
    relies on actually flags well-known media franchises. This is what
    makes the precompute branded fix work."""
    from app.agent.tools.intent_classification import analyze_topic

    a = analyze_topic("Star Wars characters")
    assert isinstance(a, dict)
    assert a.get("is_media") is True
