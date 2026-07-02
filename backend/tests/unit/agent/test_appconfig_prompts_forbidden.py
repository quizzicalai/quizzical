# backend/tests/unit/agent/test_appconfig_prompts_forbidden.py
"""AC-EVAL-2026-07-02 (punchlist P6) — App-Config prompt overrides must keep
the anti-self-referential "ABSOLUTELY FORBIDDEN" block.

The ``llm.prompts`` overrides in ``appconfig.local.yaml`` for
``question_generator`` and ``next_question_generator`` shadow the code defaults
in ``DEFAULT_PROMPTS`` — and until 2026-07-02 they silently DROPPED the
"ABSOLUTELY FORBIDDEN" block that stops the model from asking the user which
outcome they think they are (the quiz-defeating self-match failure mode). The
runtime guard (``is_self_referential_question``) still caught offenders, but
every catch costs a full extra LLM call (drop + regenerate). These tests pin
the block VERBATIM in both overrides so a future prompt tweak cannot drop it
again without failing CI.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from app.agent.prompts import DEFAULT_PROMPTS


def _load_llm_prompts() -> dict:
    cfg_path = Path(__file__).resolve().parents[3] / "appconfig.local.yaml"
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["quizzical"]["llm"]["prompts"]


def _forbidden_block(default_user_template: str) -> str:
    """Extract the FORBIDDEN block from a default prompt (up to its blank-line
    terminator) so the override comparison is verbatim, not approximate."""
    start = default_user_template.index("ABSOLUTELY FORBIDDEN")
    end = default_user_template.index("\n\n", start)
    return default_user_template[start:end]


@pytest.mark.parametrize(
    "prompt_name", ["question_generator", "next_question_generator"]
)
def test_appconfig_override_keeps_forbidden_block_verbatim(prompt_name: str) -> None:
    default_user = DEFAULT_PROMPTS[prompt_name][1]
    block = _forbidden_block(default_user)
    assert block.startswith("ABSOLUTELY FORBIDDEN")
    assert "self-identify" in block

    override = _load_llm_prompts()[prompt_name]["user_prompt_template"]
    assert block in override, (
        f"The llm.prompts override for {prompt_name!r} in appconfig.local.yaml "
        "must contain the DEFAULT_PROMPTS 'ABSOLUTELY FORBIDDEN' block "
        "verbatim (AC-EVAL-2026-07-02 / punchlist P6). Dropping it makes the "
        "model regress into self-match questions that the runtime guard then "
        "has to catch at the cost of an extra LLM call per offence."
    )


@pytest.mark.parametrize(
    "prompt_name", ["initial_planner", "question_generator", "next_question_generator"]
)
def test_appconfig_override_placeholders_are_a_subset_of_defaults(prompt_name: str) -> None:
    """Overrides must not introduce placeholders the tool never supplies —
    LangChain raises a KeyError at runtime for any unexpected variable."""
    import re

    def placeholders(template: str) -> set[str]:
        # single-brace placeholders only ({{...}} JSON braces are escaped)
        return set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", template))

    default_user = DEFAULT_PROMPTS[prompt_name][1]
    override = _load_llm_prompts()[prompt_name]["user_prompt_template"]
    extra = placeholders(override) - placeholders(default_user)
    assert not extra, (
        f"Override for {prompt_name!r} references placeholders the production "
        f"tool does not supply: {sorted(extra)}"
    )
