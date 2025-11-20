# tests/fixtures/tool_fixtures.py
"""
Test fixtures for tool wiring.

Goals:
- Do NOT stub or override the actual tool implementations in
  planning_tools / content_creation_tools / data_tools.
  Those modules should be testable directly.
- Ensure that app.agent.graph always calls the *same tool objects*
  exported from those modules, so that:
    * Unit tests can patch at the module level (ctools/ptools/dtools).
    * Graph-level tests automatically see those patches via its
      tool_* bindings.
- Keep everything fast & deterministic by relying on llm_fixtures.py
  for fake LLM / RAG behavior, not by stubbing tools here.

This fixture is autouse so tests never have to remember to import
or call it; graph.py is always kept in sync with the current
tool modules.
"""

import pytest

# Graph (holds tool_* names bound at import time)
import app.agent.graph as graph_mod

# Tool modules (source of truth for tool implementations)
from app.agent.tools import (
    planning_tools as ptools,
    content_creation_tools as ctools,
    data_tools as dtools,
)


def _bind(monkeypatch, module, attr_name, value):
    """
    Helper: bind `module.attr_name` to `value`, creating it if missing.

    We use raising=False so this works whether or not the attribute
    already exists on graph_mod.
    """
    monkeypatch.setattr(module, attr_name, value, raising=False)


@pytest.fixture(autouse=True)
def align_graph_tool_bindings(monkeypatch):
    """
    Autouse fixture that keeps app.agent.graph's tool bindings aligned
    with the actual tool modules.

    Pattern:
    - The real implementations live in planning_tools / content_creation_tools /
      data_tools (and are decorated with @tool).
    - graph.py typically imports those once at module import time and stores
      them as names like tool_plan_quiz, tool_generate_baseline_questions, etc.
    - In tests, we want to be able to patch at the *module* level (e.g.,
      monkeypatch.setattr(ctools, "generate_baseline_questions", fake_tool)),
      and have graph automatically see that patch.

    So on every test:
    - We re-bind graph_mod.tool_* to whatever objects are currently exported
      from the tool modules.
    - We do NOT change behavior here; behavior is controlled by:
        * the real implementations, plus
        * any monkeypatches you do in individual tests, plus
        * the fake LLM / RAG plumbing in llm_fixtures.py.
    """

    # ------------------------------------------------------------------
    # Planning tools
    # ------------------------------------------------------------------
    _bind(monkeypatch, graph_mod, "tool_plan_quiz", ptools.plan_quiz)
    _bind(monkeypatch, graph_mod, "tool_generate_character_list", ptools.generate_character_list)
    _bind(monkeypatch, graph_mod, "tool_select_characters_for_reuse", ptools.select_characters_for_reuse)

    # ------------------------------------------------------------------
    # Content creation tools
    # ------------------------------------------------------------------
    _bind(monkeypatch, graph_mod, "tool_draft_character_profile", ctools.draft_character_profile)
    _bind(monkeypatch, graph_mod, "tool_draft_character_profiles", ctools.draft_character_profiles)
    _bind(monkeypatch, graph_mod, "tool_generate_baseline_questions", ctools.generate_baseline_questions)
    _bind(monkeypatch, graph_mod, "tool_generate_next_question", ctools.generate_next_question)
    _bind(monkeypatch, graph_mod, "tool_decide_next_step", ctools.decide_next_step)
    _bind(monkeypatch, graph_mod, "tool_write_final_user_profile", ctools.write_final_user_profile)

    # ------------------------------------------------------------------
    # Data / RAG tools (only if graph exposes them as tool_* aliases)
    # ------------------------------------------------------------------
    # These may or may not exist on graph_mod, so we always bind safely.
    _bind(monkeypatch, graph_mod, "tool_wikipedia_search", dtools.wikipedia_search)
    _bind(monkeypatch, graph_mod, "tool_web_search", dtools.web_search)
