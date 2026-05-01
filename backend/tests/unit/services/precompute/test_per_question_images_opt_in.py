"""§21 Phase 7 — per-question images opt-in (`AC-PRECOMP-COST-7`).

Default: feature OFF. The builder MUST NOT request per-question images
unless `settings.precompute.per_question_images` is True.
"""

from __future__ import annotations

from app.core.config import PrecomputeConfig


def test_default_off_no_image_calls_made():
    cfg = PrecomputeConfig()
    assert cfg.per_question_images is False, (
        "per-question images default must be OFF — see AC-PRECOMP-COST-7"
    )


def test_opt_in_via_config_true_propagates():
    cfg = PrecomputeConfig(per_question_images=True)
    assert cfg.per_question_images is True
