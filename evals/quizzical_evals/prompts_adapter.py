"""Bridge eval variants to the agent's real prompts.

The agent's prompt source of truth is ``backend/app/agent/prompts.py``
(``DEFAULT_PROMPTS``) plus the per-strategy transforms in
``backend/Analysis/prompts_variants.py`` ("baseline" | "cot" | "fewshot").
Rather than copy prompt text into the eval package (which would silently drift),
we import them at runtime. If the backend isn't importable (e.g. running evals
in isolation without the app's deps), we degrade to a minimal built-in fallback
and log it, so the harness still runs in ``--dry-run``.

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


def get_prompt_pair(function: str, strategy: str = "baseline") -> tuple[str, str]:
    """Return (system, user_template) for a function + prompt strategy."""
    if _ensure_backend_on_path():
        try:
            # Prefer the experiment's strategy transforms (baseline/cot/fewshot)
            # so eval prompt variants match the existing study exactly.
            from Analysis.prompts_variants import get_prompt_pair as _exp_pair  # type: ignore

            return _exp_pair(function, strategy)  # type: ignore[arg-type]
        except Exception:
            pass
        try:
            from app.agent.prompts import DEFAULT_PROMPTS  # type: ignore

            if function in DEFAULT_PROMPTS:
                return DEFAULT_PROMPTS[function]
        except Exception as exc:  # pragma: no cover
            logger.warning("prompts_adapter.default_prompts_fail err=%s", exc)
    if function in _FALLBACK:
        return _FALLBACK[function]
    raise KeyError(f"No prompt available for function '{function}'")
