"""Unit tests for ``app.agent.prompts.PromptManager`` and ``DEFAULT_PROMPTS``.

The PromptManager exposes a tiny but critical contract:

* When ``settings.llm_prompts[name]`` provides a complete override
  (``system_prompt`` and ``user_prompt_template``), use it.
* Otherwise, fall back to the entry in ``DEFAULT_PROMPTS``.
* Raise ``ValueError`` when the name is unknown to both sources.

The default registry is also validated structurally so accidental missing
``{placeholders}`` or stray ``{`` / ``}`` won't slip into a release.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.prompts import ChatPromptTemplate

from app.agent import prompts as prompts_module
from app.agent.prompts import DEFAULT_PROMPTS, PromptManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_settings(
    monkeypatch: pytest.MonkeyPatch, llm_prompts: dict[str, Any]
) -> None:
    """Swap ``prompts.settings`` for a stand-in with the given ``llm_prompts``."""
    monkeypatch.setattr(prompts_module, "settings", SimpleNamespace(llm_prompts=llm_prompts))


# ---------------------------------------------------------------------------
# Default-registry contract
# ---------------------------------------------------------------------------


def test_default_prompts_registry_is_complete() -> None:
    expected = {
        "topic_normalizer",
        "initial_planner",
        "character_list_generator",
        "character_selector",
        "synopsis_generator",
        "profile_writer",
        "profile_batch_writer",
        "question_generator",
        "next_question_generator",
        "decision_maker",
        "final_profile_writer",
        "image_prompt_enhancer",
        "safety_checker",
        "error_analyzer",
        "failure_explainer",
    }
    assert expected.issubset(set(DEFAULT_PROMPTS.keys()))


@pytest.mark.parametrize("name", sorted(DEFAULT_PROMPTS.keys()))
def test_default_prompt_pair_is_well_formed(name: str) -> None:
    sys, user = DEFAULT_PROMPTS[name]
    assert isinstance(sys, str) and sys.strip(), f"system prompt empty for {name}"
    assert isinstance(user, str) and user.strip(), f"user prompt empty for {name}"


# ---------------------------------------------------------------------------
# Manager: returns ChatPromptTemplate
# ---------------------------------------------------------------------------


def test_get_prompt_returns_chat_prompt_template_from_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, llm_prompts={})

    tmpl = PromptManager().get_prompt("safety_checker")
    assert isinstance(tmpl, ChatPromptTemplate)
    # Two messages: system + human.
    assert len(tmpl.messages) == 2


def test_get_prompt_unknown_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, llm_prompts={})
    with pytest.raises(ValueError, match="not found"):
        PromptManager().get_prompt("does_not_exist")


# ---------------------------------------------------------------------------
# Override / fallback semantics
# ---------------------------------------------------------------------------


def test_dynamic_override_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    override = SimpleNamespace(
        system_prompt="OVERRIDE_SYS",
        user_prompt_template="OVERRIDE_USER {category}",
    )
    _patch_settings(monkeypatch, llm_prompts={"safety_checker": override})

    tmpl = PromptManager().get_prompt("safety_checker")
    formatted = tmpl.format_messages(category="Cats", synopsis="x")
    # Two messages emitted: system + human.
    assert formatted[0].content == "OVERRIDE_SYS"
    assert formatted[1].content == "OVERRIDE_USER Cats"


def test_partial_override_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Override missing user_prompt_template => default registry wins."""
    incomplete = SimpleNamespace(system_prompt="ONLY_SYS", user_prompt_template="")
    _patch_settings(monkeypatch, llm_prompts={"failure_explainer": incomplete})

    tmpl = PromptManager().get_prompt("failure_explainer")
    msgs = tmpl.format_messages(error_summary="boom")
    default_sys, _ = DEFAULT_PROMPTS["failure_explainer"]
    assert msgs[0].content == default_sys
    assert "ONLY_SYS" not in msgs[0].content


def test_override_without_system_prompt_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete = SimpleNamespace(system_prompt="", user_prompt_template="USER {x}")
    _patch_settings(monkeypatch, llm_prompts={"safety_checker": incomplete})

    tmpl = PromptManager().get_prompt("safety_checker")
    default_sys, _ = DEFAULT_PROMPTS["safety_checker"]
    msgs = tmpl.format_messages(category="x", synopsis="y")
    assert msgs[0].content == default_sys


# ---------------------------------------------------------------------------
# Required placeholders for the most-called prompts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,required",
    [
        ("topic_normalizer", {"category", "search_context"}),
        ("initial_planner", {"category", "outcome_kind", "creativity_mode", "intent", "canonical_names"}),
        ("character_list_generator", {"category", "synopsis", "creativity_mode", "search_context", "canonical_names", "intent"}),
        ("synopsis_generator", {"category", "outcome_kind", "creativity_mode"}),
        ("profile_writer", {"category", "creativity_mode", "intent", "character_name", "outcome_kind", "character_context"}),
        ("profile_batch_writer", {"category", "outcome_kind", "creativity_mode", "intent", "character_contexts", "character_names", "count"}),
        ("question_generator", {"count", "category", "creativity_mode", "outcome_kind", "intent", "synopsis", "character_profiles", "max_options"}),
        ("next_question_generator", {"category", "creativity_mode", "outcome_kind", "intent", "synopsis", "character_profiles", "quiz_history", "max_options"}),
        ("decision_maker", {"category", "creativity_mode", "outcome_kind", "character_profiles", "quiz_history", "max_total_questions", "min_questions_before_finish", "confidence_threshold"}),
        ("final_profile_writer", {"winning_character_name", "category", "creativity_mode", "outcome_kind", "quiz_history"}),
        ("image_prompt_enhancer", {"style", "concept"}),
        ("safety_checker", {"category", "synopsis"}),
        ("error_analyzer", {"error_message", "state"}),
        ("failure_explainer", {"error_summary"}),
    ],
)
def test_default_prompt_declares_required_placeholders(
    name: str, required: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_settings(monkeypatch, llm_prompts={})
    tmpl = PromptManager().get_prompt(name)
    declared = set(tmpl.input_variables)
    missing = required - declared
    assert not missing, f"{name} is missing placeholders {missing}; declared={declared}"


# ---------------------------------------------------------------------------
# Templates do not leak literal JSON braces as variables.
# ---------------------------------------------------------------------------


def test_default_prompts_do_not_expose_literal_json_keys_as_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If '{{' / '}}' escaping was forgotten, langchain would expose JSON keys
    such as 'question_text' or 'archetypes' as required input variables.
    """
    _patch_settings(monkeypatch, llm_prompts={})
    suspicious = {
        "questionText",
        '"question_text"',
        '"archetypes"',
        '"options"',
        '"action"',
    }
    for name in DEFAULT_PROMPTS:
        tmpl = PromptManager().get_prompt(name)
        bad = suspicious & set(tmpl.input_variables)
        assert not bad, f"{name} accidentally exposes JSON keys as variables: {bad}"


# ---------------------------------------------------------------------------
# Format with the smallest required kwargs and check rendering.
# ---------------------------------------------------------------------------


def test_safety_checker_renders_with_minimal_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, llm_prompts={})
    tmpl = PromptManager().get_prompt("safety_checker")
    msgs = tmpl.format_messages(category="Cats", synopsis="meow")
    assert "Cats" in msgs[1].content
    assert "meow" in msgs[1].content


def test_question_generator_keeps_literal_json_braces(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, llm_prompts={})
    tmpl = PromptManager().get_prompt("question_generator")
    msgs = tmpl.format_messages(
        count=5,
        category="Cats",
        creativity_mode="whimsical",
        outcome_kind="characters",
        intent="identify",
        synopsis="A quiz about cats.",
        character_profiles="N/A",
        max_options=4,
    )
    rendered = msgs[1].content
    # Literal JSON braces survive (proves '{{' was used to escape).
    assert '"question_text"' in rendered
    assert '"options"' in rendered
    # Format substitutions happened.
    assert re.search(r"EXACTLY\s+5\s+diverse", rendered)
    assert "Cats" in rendered


def test_module_exposes_singleton_prompt_manager() -> None:
    assert isinstance(prompts_module.prompt_manager, PromptManager)
