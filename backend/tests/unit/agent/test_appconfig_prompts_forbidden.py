# backend/tests/unit/agent/test_appconfig_prompts_forbidden.py
"""AC-EVAL-2026-07-02 (punchlist P6) — the PRODUCTION-resolved QG/NQG prompt
must keep the anti-self-referential "ABSOLUTELY FORBIDDEN" block.

Background: the ``llm.prompts`` overrides in ``appconfig.local.yaml`` for
``question_generator`` and ``next_question_generator`` originally DROPPED the
"ABSOLUTELY FORBIDDEN" block present in ``DEFAULT_PROMPTS`` — the guard that
stops the model from asking the user which outcome they think they are
(self-match), naming candidate outcomes, or asking meta questions. The runtime
guard (``is_self_referential_question``) still caught offenders, but every catch
costs a full extra LLM call.

The 2026-07-02 live eval then showed the CoT-styled overrides were a NET
NEGATIVE for gpt-4o-mini on both functions (QG: 3.41 override vs 3.66 code
default; NQG: 4.36 vs 4.45 and 6.6s vs 2.4s p95), so the overrides were REMOVED
and both functions now resolve to the code default. Either way the block must be
present in whatever prompt production actually sends. These tests assert on the
resolved prompt (``PromptManager.get_prompt``), so they hold whether the block
lives in a future override or in the code default.
"""
from __future__ import annotations

import pytest

from app.agent.prompts import DEFAULT_PROMPTS, prompt_manager


def _forbidden_block(default_user_template: str) -> str:
    start = default_user_template.index("ABSOLUTELY FORBIDDEN")
    end = default_user_template.index("\n\n", start)
    return default_user_template[start:end]


def _resolved_user_template(prompt_name: str) -> str:
    """The human/user template production actually sends for ``prompt_name``."""
    tmpl = prompt_manager.get_prompt(prompt_name)
    # The human message is the last in the ChatPromptTemplate.
    human = tmpl.messages[-1]
    return human.prompt.template


@pytest.mark.parametrize(
    "prompt_name", ["question_generator", "next_question_generator"]
)
def test_resolved_prompt_keeps_forbidden_block_verbatim(prompt_name: str) -> None:
    default_user = DEFAULT_PROMPTS[prompt_name][1]
    block = _forbidden_block(default_user)
    assert block.startswith("ABSOLUTELY FORBIDDEN")
    assert "self-identify" in block

    resolved = _resolved_user_template(prompt_name)
    assert block in resolved, (
        f"The production-resolved prompt for {prompt_name!r} must contain the "
        "'ABSOLUTELY FORBIDDEN' block verbatim (AC-EVAL-2026-07-02 / punchlist "
        "P6). Without it the model regresses into self-match questions that the "
        "runtime guard then catches at the cost of an extra LLM call per offence."
    )


def test_qg_nqg_have_no_appconfig_prompt_override() -> None:
    """AC-EVAL-2026-07-02: the CoT-styled QG/NQG overrides were removed (they
    scored worse than the code default for gpt-4o-mini), so both functions must
    resolve to DEFAULT_PROMPTS. If someone re-adds an override, the verbatim
    test above still guards the FORBIDDEN block."""
    from pathlib import Path

    import yaml  # type: ignore[import-untyped]

    cfg_path = Path(__file__).resolve().parents[3] / "appconfig.local.yaml"
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    prompts = data["quizzical"]["llm"].get("prompts", {}) or {}
    assert "question_generator" not in prompts, (
        "question_generator llm.prompts override should be removed per the "
        "2026-07-02 eval (code default beats it 3.66 vs 3.41)."
    )
    assert "next_question_generator" not in prompts, (
        "next_question_generator llm.prompts override should be removed per the "
        "2026-07-02 eval (code default beats it 4.45 vs 4.36 at 2.4s vs 6.6s p95)."
    )
