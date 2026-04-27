"""AC-IMG-PERF-1..3 — image generation performance defaults.

Phase 7: image gen must be as fast as possible. Defaults align FAL semaphore
with the character-generation concurrency cap, and tighten the per-call timeout
so a stuck FAL call cannot drag a quiz beyond a single character's budget.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


def test_image_gen_concurrency_default_matches_character_concurrency():
    """AC-IMG-PERF-1 — semaphore wide enough to fan out one image per character."""
    from app.core.config import ImageGenSettings, QuizConfig
    img = ImageGenSettings()
    quiz = QuizConfig()
    # default character_concurrency may be None (auto), so use 6 as the soft target.
    target = quiz.character_concurrency if quiz.character_concurrency else 6
    assert img.concurrency >= target, (
        f"image_gen.concurrency ({img.concurrency}) must be >= character_concurrency "
        f"({target}) so character-image fan-out is never the bottleneck."
    )


def test_image_gen_timeout_default_is_tight():
    """AC-IMG-PERF-2 — per-call timeout ≤ 12s so a stuck FAL call cannot
    extend total quiz latency by more than one character's worth."""
    from app.core.config import ImageGenSettings
    img = ImageGenSettings()
    assert 1.0 < img.timeout_s <= 12.0, (
        f"image_gen.timeout_s ({img.timeout_s}) must be ≤ 12.0s for fast-fail."
    )


def test_image_gen_steps_remain_low_fidelity():
    """AC-IMG-PERF-3 — fidelity stays low; quality comes from style consistency,
    not from inference steps. Steps must remain ≤ 3 (Schnell sweet spot)."""
    from app.core.config import ImageGenSettings
    img = ImageGenSettings()
    assert img.num_inference_steps <= 3, (
        f"num_inference_steps ({img.num_inference_steps}) must stay low for speed; "
        "style consistency is enforced via STYLE_ANCHOR + deterministic seed."
    )
