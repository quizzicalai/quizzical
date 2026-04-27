# tests/unit/agent/test_model_tier_selection.py
"""
Tests for tiered model selection (§7.7.3).

Acceptance criteria covered:
- AC-AGENT-TIER-1: per-tool model strings come from settings, not Python literals
- AC-AGENT-TIER-2: profile_batch_writer / question_generator use `model_unknown` (Pro/3.x)
  when topic is fringe, and `model` (Flash) when well-known
- AC-AGENT-TIER-3: missing `model_unknown` falls back to `model` without raising
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


@pytest.fixture
def resolve():
    from app.agent.llm_helpers import resolve_model_for_tool
    return resolve_model_for_tool


# ---------------------------------------------------------------------------
# AC-AGENT-TIER-1: model comes from config
# ---------------------------------------------------------------------------

def test_model_resolved_from_settings(resolve, monkeypatch):
    from app.agent import llm_helpers

    monkeypatch.setattr(llm_helpers, "_get_tool_cfg",
                        lambda name: {"model": "gemini/gemini-2.5-flash"})

    assert resolve("any_tool", is_well_known=True) == "gemini/gemini-2.5-flash"


def test_model_resolution_returns_none_when_no_config(resolve, monkeypatch):
    from app.agent import llm_helpers
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: None)
    assert resolve("missing_tool", is_well_known=True) is None


# ---------------------------------------------------------------------------
# AC-AGENT-TIER-2: adaptive tier for batch writer / question generator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name", ["profile_batch_writer", "question_generator"])
def test_adaptive_tools_use_pro_when_fringe(resolve, monkeypatch, tool_name):
    from app.agent import llm_helpers
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: {
        "model": "gemini/gemini-2.5-flash",
        "model_unknown": "gemini/gemini-2.5-pro",
    })
    assert resolve(tool_name, is_well_known=False) == "gemini/gemini-2.5-pro"


@pytest.mark.parametrize("tool_name", ["profile_batch_writer", "question_generator"])
def test_adaptive_tools_use_flash_when_well_known(resolve, monkeypatch, tool_name):
    from app.agent import llm_helpers
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: {
        "model": "gemini/gemini-2.5-flash",
        "model_unknown": "gemini/gemini-2.5-pro",
    })
    assert resolve(tool_name, is_well_known=True) == "gemini/gemini-2.5-flash"


def test_non_adaptive_tool_ignores_is_well_known(resolve, monkeypatch):
    """Tools not in the adaptive set always use 'model' regardless of is_well_known."""
    from app.agent import llm_helpers
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: {
        "model": "gemini/gemini-2.5-flash-lite",
        "model_unknown": "gemini/gemini-2.5-pro",
    })
    assert resolve("safety_checker", is_well_known=False) == "gemini/gemini-2.5-flash-lite"
    assert resolve("safety_checker", is_well_known=True) == "gemini/gemini-2.5-flash-lite"


# ---------------------------------------------------------------------------
# AC-AGENT-TIER-3: missing model_unknown falls back to model
# ---------------------------------------------------------------------------

def test_missing_model_unknown_falls_back_to_model(resolve, monkeypatch):
    from app.agent import llm_helpers
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: {
        "model": "gemini/gemini-2.5-flash",
    })
    # Even on fringe, fall back to flash without raising.
    assert resolve("profile_batch_writer", is_well_known=False) == "gemini/gemini-2.5-flash"
