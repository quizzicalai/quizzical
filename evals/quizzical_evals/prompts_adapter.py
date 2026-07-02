"""Bridge eval variants to the agent's real prompts.

WHAT PRODUCTION ACTUALLY SHIPS (and therefore what "baseline" means here):
``PromptManager.get_prompt`` prefers the App-Config override in
``settings.llm_prompts`` (populated from ``backend/appconfig.local.yaml``
``llm.prompts``) and only falls back to the hardcoded
``app.agent.prompts.DEFAULT_PROMPTS``. Before 2026-07-02 this adapter read
ONLY ``DEFAULT_PROMPTS``, so evals scored prompt text that differed from what
prod sends for any overridden function (initial_planner, question_generator,
next_question_generator). ``baseline`` now mirrors the production resolution
order exactly; the extra ``default`` strategy exposes the bare code default so
an override can be A/B-tested against it.

Strategies:
  * ``baseline`` -- the production-effective prompt (App-Config override when
    present, else the code default). This is what prod ships.
  * ``default``  -- the code default (``DEFAULT_PROMPTS``) verbatim, ignoring
    any App-Config override. Use to measure whether an override earns its keep.
  * ``cot``      -- code default + explicit private-reasoning preface (kept
    byte-identical to the retired ``Analysis/prompts_variants.py`` transform so
    scores stay comparable to the 108-run study).
  * ``fewshot``  -- code default + one worked example (same provenance).

If the backend isn't importable (e.g. running evals in isolation without the
app's deps), we degrade to a minimal built-in fallback and log it, so the
harness still runs in ``--dry-run``.

``get_prompt_pair(function, strategy)`` returns ``(system, user_template)`` where
``user_template`` is a ``str.format``-style template expecting the same keys the
production tool passes (see ``content_creation_tools.py`` / ``planning_tools.py``).
The dataset fixtures provide those keys.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Functions this adapter knows how to render. Aligns with FunctionEvalSpec.function.
SUPPORTED = (
    "initial_planner",
    "profile_batch_writer",
    "profile_writer",
    "question_generator",
    "next_question_generator",
    "decision_maker",
    "final_profile_writer",
)


_BACKEND_AVAILABLE: bool | None = None  # cache so we warn at most once


def _ensure_backend_on_path() -> bool:
    """Add ``backend/`` to sys.path so ``import app...`` works from evals/.

    Result is cached: the import is attempted once per process and the warning
    (if any) fires once, not per cell. When the backend import fails (e.g. a
    broken transitive dep in the active interpreter), we fall back to the
    built-in prompt stubs below so the harness still runs offline.
    """
    global _BACKEND_AVAILABLE
    if _BACKEND_AVAILABLE is not None:
        return _BACKEND_AVAILABLE
    here = Path(__file__).resolve()
    # evals/quizzical_evals/prompts_adapter.py -> repo root is parents[2]
    repo_root = here.parents[2]
    backend = repo_root / "backend"
    if backend.is_dir() and str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        import app.agent.prompts  # noqa: F401

        _BACKEND_AVAILABLE = True
    except Exception as exc:  # pragma: no cover - depends on env
        logger.warning(
            "prompts_adapter.backend_unavailable err=%s -- using built-in "
            "fallback prompts (fine for --dry-run; for --live, run under the "
            "backend venv so production prompt text is used).",
            exc,
        )
        _BACKEND_AVAILABLE = False
    return _BACKEND_AVAILABLE


_FALLBACK: dict[str, tuple[str, str]] = {
    # Minimal, schema-faithful stand-ins; ONLY used if the backend import fails
    # (e.g. running evals under an interpreter without the app's deps). The
    # authoritative text lives in backend/app/agent/prompts.py and is preferred
    # whenever importable. These mirror the production JSON contracts closely
    # enough that --dry-run exercises every function end-to-end.
    "initial_planner": (
        "You are a master planner for viral personality quizzes.",
        "Plan a quiz about '{category}'. Outcome kind: {outcome_kind}. "
        "Creativity: {creativity_mode}. Intent: {intent}. "
        "Canonical (optional): {canonical_names}. Return ONLY JSON "
        '{{"title": str, "synopsis": str, "ideal_archetypes": [str], "ideal_count_hint": int}}.',
    ),
    "profile_batch_writer": (
        "You craft vivid quiz outcome profiles in batch.",
        "Quiz: {category}. Creativity: {creativity_mode}. Write a profile for each "
        "of these names, in order: {character_names}. Return ONLY a JSON array of "
        '{{"name": str, "short_description": str, "profile_text": str, "image_url": null}}.',
    ),
    "profile_writer": (
        "You craft outcome profiles for personality quizzes.",
        "Write a profile for '{character_name}' in quiz '{category}'. Creativity: "
        '{creativity_mode}. Return ONLY JSON {{"name": str, "short_description": '
        'str, "profile_text": str, "image_url": null}}.',
    ),
    "question_generator": (
        "You are a researcher generating baseline personality-quiz questions.",
        "Create EXACTLY {count} diverse multiple-choice questions for '{category}'. "
        "2..{max_options} options each. Profiles: {character_profiles}. Synopsis: "
        '{synopsis}. Return ONLY JSON {{"questions": [{{"question_text": str, '
        '"options": [{{"text": str}}]}}]}}.',
    ),
    "next_question_generator": (
        "You are a researcher creating the most informative next quiz question.",
        "Generate ONE NOVEL multiple-choice question for '{category}' given history "
        "{quiz_history} and profiles {character_profiles}. 2..{max_options} options. "
        'Return ONLY JSON {{"question_text": str, "options": [{{"text": str}}], '
        '"progress_phrase": str}}.',
    ),
    "decision_maker": (
        "You analyze quiz answers and decide whether to ask one more or finish.",
        "Quiz '{category}'. Profiles: {character_profiles}. History: {quiz_history}. "
        "Finish early only if answers >= {min_questions_before_finish} and confidence "
        '>= {confidence_threshold}. Return ONLY JSON {{"action": '
        '"ASK_ONE_MORE_QUESTION"|"FINISH_NOW", "confidence": number, '
        '"winning_character_name": str}}.',
    ),
    "final_profile_writer": (
        "You write personalized, multi-paragraph personality readings (>=3 "
        "paragraphs, >=400 chars).",
        "User matched '{winning_character_name}' for quiz '{category}'. History: "
        "{quiz_history}. Write a deep second-person reading referencing their "
        'answers. Return ONLY JSON {{"title": str, "description": str, '
        '"image_url": null}}.',
    ),
}


# Strategy transforms, byte-identical to the retired Analysis/prompts_variants.py
# so cot/fewshot scores remain comparable to the prior 108-run study.
_COT_SYSTEM_SUFFIX = (
    "\n\nThink step-by-step privately about (a) what makes a great answer, "
    "(b) likely failure modes, and (c) the explicit JSON schema. Do NOT include "
    "your reasoning in the response. Return ONLY the required JSON object."
)

_COT_USER_PREFIX = (
    "Before composing the JSON, internally check: (1) does this match the "
    "schema exactly?  (2) is the tone consistent with the creativity_mode? "
    "(3) does each field actively earn its place?  Then output the JSON.\n\n"
)

_FEWSHOT_EXAMPLES: dict[str, str] = {
    "initial_planner": (
        "\n\n## Example (for format only — do not copy content)\n"
        "Topic: 'Type of Pasta Shape'\n"
        '{{"title":"What Pasta Shape Are You?",'
        '"synopsis":"Every pasta shape has a personality — clingy sauces, '
        "showy presentations, hearty bakes. This quiz reveals which one truly "
        'matches your vibe.",'
        '"ideal_archetypes":["Spaghetti","Penne","Farfalle","Fettuccine","Orecchiette","Rigatoni","Fusilli","Linguine"],'
        '"ideal_count_hint":8}}\n'
    ),
    "profile_batch_writer": (
        "\n\n## Example object (for format only)\n"
        '{{"name":"Spaghetti","short_description":"The reliable classic everyone loves.",'
        '"profile_text":"You are dependable and adaptable… (2-4 sentences).",'
        '"image_url":null}}\n'
    ),
    "question_generator": (
        "\n\n## Example question (for format/style only — do not reuse)\n"
        '{{"question_text":"Your ideal Sunday is…",'
        '"options":[{{"text":"Hosting a long lunch"}},{{"text":"Quietly reading"}},'
        '{{"text":"A spontaneous adventure"}},{{"text":"Catching up on chores"}}]}}\n'
    ),
    "final_profile_writer": (
        "\n\n## Example (for format/style only)\n"
        '{{"title":"You are Spaghetti!",'
        '"description":"You\'re the friend everyone returns to… (2-4 short paragraphs).",'
        '"image_url":null}}\n'
    ),
}


def _default_pair(function: str) -> tuple[str, str] | None:
    """The CODE default prompt (``DEFAULT_PROMPTS``), or None if unavailable."""
    try:
        from app.agent.prompts import DEFAULT_PROMPTS  # type: ignore

        if function in DEFAULT_PROMPTS:
            return DEFAULT_PROMPTS[function]
    except Exception as exc:  # pragma: no cover
        logger.warning("prompts_adapter.default_prompts_fail err=%s", exc)
    return None


def _production_pair(function: str) -> tuple[str, str] | None:
    """The prompt PRODUCTION ships: App-Config override first, then default.

    Mirrors ``app.agent.prompts.PromptManager.get_prompt`` exactly: the
    ``settings.llm_prompts`` entry (from ``appconfig.local.yaml`` ``llm.prompts``)
    wins when BOTH its fields are non-empty; otherwise the code default.
    """
    try:
        from app.core.config import settings  # type: ignore

        cfg = (getattr(settings, "llm_prompts", None) or {}).get(function)
        system = getattr(cfg, "system_prompt", None) if cfg else None
        user = getattr(cfg, "user_prompt_template", None) if cfg else None
        if system and user:
            logger.info("prompts_adapter.using_appconfig_override function=%s", function)
            return (system, user)
    except Exception as exc:  # pragma: no cover
        logger.warning("prompts_adapter.settings_prompts_fail err=%s", exc)
    return _default_pair(function)


def get_prompt_pair(function: str, strategy: str = "baseline") -> tuple[str, str]:
    """Return (system, user_template) for a function + prompt strategy."""
    if _ensure_backend_on_path():
        if strategy == "baseline":
            pair = _production_pair(function)
            if pair is not None:
                return pair
        elif strategy in ("default", "cot", "fewshot"):
            pair = _default_pair(function)
            if pair is not None:
                system, user = pair
                if strategy == "default":
                    return system, user
                if strategy == "cot":
                    return system + _COT_SYSTEM_SUFFIX, _COT_USER_PREFIX + user
                return system, user + _FEWSHOT_EXAMPLES.get(function, "")
        else:
            raise KeyError(f"Unknown prompt strategy '{strategy}'")
    if function in _FALLBACK:
        return _FALLBACK[function]
    raise KeyError(f"No prompt available for function '{function}'")
