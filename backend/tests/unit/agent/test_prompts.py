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

    tmpl = PromptManager().get_prompt("error_analyzer")
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
        user_prompt_template="OVERRIDE_USER {error_summary}",
    )
    _patch_settings(monkeypatch, llm_prompts={"failure_explainer": override})

    tmpl = PromptManager().get_prompt("failure_explainer")
    formatted = tmpl.format_messages(error_summary="Cats")
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
    _patch_settings(monkeypatch, llm_prompts={"error_analyzer": incomplete})

    tmpl = PromptManager().get_prompt("error_analyzer")
    default_sys, _ = DEFAULT_PROMPTS["error_analyzer"]
    msgs = tmpl.format_messages(error_message="x", state="y")
    assert msgs[0].content == default_sys


# ---------------------------------------------------------------------------
# Required placeholders for the most-called prompts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,required",
    [
        ("topic_normalizer", {"category", "search_context"}),
        # instrument_rigor (2026-07-02, owner blackbox #5): conditional
        # INSTRUMENT RIGOR block for validated instruments — filled with "" for
        # every non-instrument topic (see app.agent.instrument_rigor).
        ("initial_planner", {"category", "outcome_kind", "creativity_mode", "intent", "canonical_names", "instrument_rigor"}),
        ("character_list_generator", {"category", "synopsis", "creativity_mode", "search_context", "canonical_names", "intent"}),
        ("synopsis_generator", {"category", "outcome_kind", "creativity_mode"}),
        ("profile_writer", {"category", "creativity_mode", "intent", "character_name", "outcome_kind", "character_context"}),
        ("profile_batch_writer", {"category", "outcome_kind", "creativity_mode", "intent", "character_contexts", "character_names", "count"}),
        ("question_generator", {"count", "category", "creativity_mode", "outcome_kind", "intent", "synopsis", "character_profiles", "max_options", "instrument_rigor"}),
        ("next_question_generator", {"category", "creativity_mode", "outcome_kind", "intent", "synopsis", "character_profiles", "quiz_history", "max_options", "instrument_rigor"}),
        ("decision_maker", {"category", "creativity_mode", "outcome_kind", "character_profiles", "quiz_history", "max_total_questions", "min_questions_before_finish", "confidence_threshold"}),
        ("final_profile_writer", {"winning_character_name", "category", "creativity_mode", "outcome_kind", "quiz_history"}),
        ("image_prompt_enhancer", {"style", "concept"}),
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


def test_error_analyzer_renders_with_minimal_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, llm_prompts={})
    tmpl = PromptManager().get_prompt("error_analyzer")
    msgs = tmpl.format_messages(error_message="Cats", state="meow")
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
        # Non-instrument topic: the INSTRUMENT RIGOR variable renders empty.
        instrument_rigor="",
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


def test_final_profile_writer_prompt_enforces_depth_and_multi_answer_grounding() -> None:
    """AC-QUALITY-FINALPROFILE-3: prompt must demand 3+ paragraphs + 2+ answer references."""
    _sys, user = DEFAULT_PROMPTS["final_profile_writer"]
    assert "At least 3 paragraphs" in user
    assert "At least 400 characters" in user
    assert "at least one concrete reference to an answer" in user
    assert "additional concrete answer reference" in user


def test_profile_batch_writer_prompt_enforces_per_name_completeness() -> None:
    """AC-PROD-R13-PERF-1: the batch prompt must demand a profile for EVERY name.

    The eval flagged the cheaper batch model dropping names (coverage failure).
    The prompt's first line of defence is to (a) state the required count and
    (b) demand exactly one profile per name verbatim.
    """
    sys, user = DEFAULT_PROMPTS["profile_batch_writer"]
    # System prompt frames completeness as the top priority.
    assert "COMPLETENESS" in sys
    # Body states the required count up front and in the contract.
    assert "EXACTLY {count}" in user
    assert "exactly {count}" in user
    # Per-name verbatim requirement + the explicit count-and-confirm step.
    assert "VERBATIM" in user
    assert "appears exactly once" in user
    # The roster placeholder must survive so callers can enumerate the names.
    assert "{character_names}" in user


def test_profile_batch_writer_built_prompt_lists_every_name_and_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rendered prompt must contain every requested name and the count.

    Mirrors how ``draft_character_profiles`` renders the roster as a numbered
    enumeration before sending it to the model.
    """
    _patch_settings(monkeypatch, llm_prompts={})
    names = ["Gryffindor", "Hufflepuff", "Ravenclaw", "Slytherin"]
    enumerated = "\n".join(f"{i}. {n}" for i, n in enumerate(names, start=1))
    tmpl = PromptManager().get_prompt("profile_batch_writer")
    msgs = tmpl.format_messages(
        category="Hogwarts House",
        outcome_kind="types",
        creativity_mode="balanced",
        intent="identify",
        character_contexts={},
        character_names=enumerated,
        count=len(names),
    )
    body = msgs[1].content
    # Every requested name is present, verbatim and enumerated.
    for i, name in enumerate(names, start=1):
        assert name in body
        assert f"{i}. {name}" in body
    # The required count appears (substituted from {count}).
    assert "EXACTLY 4 profiles" in body
    assert "exactly 4 objects" in body
