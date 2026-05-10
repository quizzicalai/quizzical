"""Live LLM integration gate for final profile quality.

This test is intentionally opt-in because it performs a real model call.
Enable with:

    RUN_LIVE_LLM_TESTS=1 pytest -q backend/tests/integration/test_live_final_profile_quality.py
"""

from __future__ import annotations

import os

import pytest

from app.agent.tools import content_creation_tools as ctools

pytestmark = [pytest.mark.integration]


def _live_tests_enabled() -> bool:
    return os.getenv("RUN_LIVE_LLM_TESTS", "0") == "1"


@pytest.mark.skipif(not _live_tests_enabled(), reason="Set RUN_LIVE_LLM_TESTS=1 to run live LLM quality gate")
@pytest.mark.asyncio
async def test_live_final_profile_meets_quality_floor() -> None:
    """AC-QUALITY-FINALPROFILE-1/-2 live check: output must be substantive."""
    out = await ctools.write_final_user_profile.ainvoke(
        {
            "winning_character": {
                "name": "The Architect",
                "category": "Ancient Rome",
                "intent": "identify",
                "profile_text": (
                    "You build systems that last. You optimize for durability and coherent structure.\n\n"
                    "In groups, you naturally become the planner who aligns moving parts and timelines."
                ),
            },
            "quiz_history": [
                {
                    "question": "Which achievement is most impressive?",
                    "answer": "Aqueducts",
                },
                {
                    "question": "What role would you choose in a city project?",
                    "answer": "Designing the long-term plan",
                },
                {
                    "question": "How do you handle conflict?",
                    "answer": "Create a process everyone can follow",
                },
            ],
            "category": "Ancient Rome",
            "outcome_kind": "characters",
            "creativity_mode": "balanced",
        }
    )

    assert ctools._count_paragraphs(out.description) >= ctools.MIN_FINAL_PARAGRAPHS
    assert len((out.description or "").strip()) >= ctools.MIN_FINAL_DESCRIPTION_CHARS
