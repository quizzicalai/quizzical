# backend/tests/reliability/test_config_concurrency_caps.py
"""
Reliability guard rails for LLM concurrency configuration.

The defaults baked into ``appconfig.local.yaml`` are part of the contract —
overshooting them empirically triggers Gemini ``503 ServiceUnavailable``
cascades on quizzes with ≥ 13 archetypes (~40 % of profiles return empty
after retry exhaustion). See AC-PERF-CHAR-2.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def _load_local_config() -> dict:
    here = Path(__file__).resolve().parents[2]  # backend/
    cfg_path = here / "appconfig.local.yaml"
    assert cfg_path.exists(), f"missing {cfg_path}"
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_local_character_concurrency_is_capped_per_ac_perf_char_2():
    """AC-PERF-CHAR-2: the local default ``character_concurrency`` MUST be
    ≤ 8. Higher values overwhelm the per-minute Gemini quota for big quizzes
    and silently drop ~40 % of character profiles."""
    cfg = _load_local_config()
    quiz = (cfg.get("quizzical") or {}).get("quiz") or {}
    cc = quiz.get("character_concurrency")
    assert cc is not None, "character_concurrency must be set explicitly"
    assert isinstance(cc, int), f"character_concurrency must be int, got {type(cc)}"
    assert 1 <= cc <= 8, (
        f"character_concurrency={cc} violates AC-PERF-CHAR-2 (must be ≤ 8 to "
        "avoid Gemini 503 cascades on ≥ 13-archetype quizzes)"
    )
