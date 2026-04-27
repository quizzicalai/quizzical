"""AC-AGENT-TIER-4..7 — adaptive model tiering covers all quality-critical tools.

Phase 7 (performance): well-known topics use the cheaper/faster default model;
fringe topics auto-upgrade to ``model_unknown`` for any tool that materially
shapes user-visible content. See specifications/backend-design.MD §7.7.3 and
§16 (Performance Tier ACs).
"""
from __future__ import annotations

import pytest

from app.agent import llm_helpers
from app.agent.llm_helpers import ADAPTIVE_TIER_TOOLS, resolve_model_for_tool


# Tools that must participate in adaptive tiering. These materially shape
# user-visible content (synopsis, archetypes, characters, questions, results).
QUALITY_CRITICAL_TOOLS: frozenset[str] = frozenset({
    "initial_planner",
    "character_list_generator",
    "synopsis_generator",
    "profile_writer",
    "profile_batch_writer",
    "profile_improver",
    "question_generator",
    "next_question_generator",
    "final_profile_writer",
})


@pytest.mark.parametrize("tool_name", sorted(QUALITY_CRITICAL_TOOLS))
def test_adaptive_tier_membership(tool_name: str) -> None:
    """AC-AGENT-TIER-4: every quality-critical tool participates in adaptive tiering."""
    assert tool_name in ADAPTIVE_TIER_TOOLS, (
        f"{tool_name!r} must be in ADAPTIVE_TIER_TOOLS so fringe topics can "
        "upgrade to model_unknown for higher fidelity."
    )


@pytest.mark.parametrize("tool_name", sorted(QUALITY_CRITICAL_TOOLS))
def test_resolve_model_uses_unknown_tier_for_fringe_topics(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """AC-AGENT-TIER-5: fringe topic + model_unknown set → upgraded model."""
    monkeypatch.setattr(
        llm_helpers,
        "_get_tool_cfg",
        lambda name: {"model": "fast/flash", "model_unknown": "deep/pro"} if name == tool_name else None,
    )
    assert resolve_model_for_tool(tool_name, is_well_known=False) == "deep/pro"
    assert resolve_model_for_tool(tool_name, is_well_known=True) == "fast/flash"


def test_resolve_model_falls_back_when_model_unknown_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-AGENT-TIER-6: missing model_unknown falls back to base model (no error)."""
    monkeypatch.setattr(
        llm_helpers, "_get_tool_cfg", lambda _name: {"model": "only/model"}
    )
    assert resolve_model_for_tool("question_generator", is_well_known=False) == "only/model"
    assert resolve_model_for_tool("question_generator", is_well_known=True) == "only/model"


def test_resolve_model_returns_none_for_unconfigured_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-AGENT-TIER-7: unconfigured tools return None (caller may use service default)."""
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda _name: None)
    assert resolve_model_for_tool("nonexistent_tool", is_well_known=True) is None
    assert resolve_model_for_tool("nonexistent_tool", is_well_known=False) is None


def test_non_adaptive_tool_ignores_model_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-AGENT-TIER-8: tools NOT in ADAPTIVE_TIER_TOOLS always use base model."""
    monkeypatch.setattr(
        llm_helpers,
        "_get_tool_cfg",
        lambda _name: {"model": "fast/flash", "model_unknown": "deep/pro"},
    )
    # safety_checker is NOT a quality-critical content tool
    assert "safety_checker" not in ADAPTIVE_TIER_TOOLS
    assert resolve_model_for_tool("safety_checker", is_well_known=False) == "fast/flash"
    assert resolve_model_for_tool("safety_checker", is_well_known=True) == "fast/flash"
