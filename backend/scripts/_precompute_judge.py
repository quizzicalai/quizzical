"""LLM-as-judge for precomputed quiz topics.

Implements a `judge_fn` compatible with
`app.services.precompute.evaluator.evaluate_single`, so the operator
draft pipeline can score generated topics with two-judge consensus
(`AC-PRECOMP-QUAL-2`).

The judge prompts gemini-flash with the topic synopsis, characters and
baseline questions and asks it to return:
- `score` 0-100 (overall artefact quality)
- `blocking_reasons` list (each is a short tag)
- `non_blocking_notes` list (qualitative feedback)

Two judges run with different seeds (passed via the `seed` kwarg by
`evaluate_single`); the consensus rule is implemented inside the
evaluator module — this file only owns the prompt + parsing.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, Field

from app.services.precompute.evaluator import EvaluatorResult, JudgeTier

logger = structlog.get_logger(__name__)

JUDGE_DEFAULT_MODEL = "gemini/gemini-flash-latest"
JUDGE_MAX_TOKENS = 1500
JUDGE_TIMEOUT_S = 45


class _JudgeOutput(BaseModel):
    score: int = Field(..., ge=0, le=100)
    blocking_reasons: list[str] = Field(default_factory=list, max_length=8)
    non_blocking_notes: list[str] = Field(default_factory=list, max_length=8)


_JUDGE_SYSTEM_PROMPT = (
    "You are a strict editor reviewing a 'Which X are you?' personality quiz "
    "before publication. You will be shown the topic's synopsis, character "
    "outcomes, and baseline questions. Score the package 0-100 on overall "
    "quality, and list any blocking issues. A blocking issue is something "
    "that would embarrass the platform if shipped: factually wrong character "
    "claims, malformed/duplicate questions, characters that are not actually "
    "from the stated topic, options that don't map clearly to a single "
    "outcome, or quiz questions that have an obvious 'correct' answer "
    "instead of being preference-based. Non-blocking notes are stylistic "
    "improvements. Return STRICT JSON matching the schema."
)


def _format_artefact(artefact: Any) -> str:
    """Convert a topic dict into the compact prompt text the judge sees."""
    if not isinstance(artefact, dict):
        artefact = dict(getattr(artefact, "__dict__", {}) or {})
    title = artefact.get("display_name") or artefact.get("slug") or "<unnamed>"
    syn = artefact.get("synopsis") or {}
    syn_title = syn.get("title") or ""
    syn_summary = syn.get("summary") or ""
    chars = artefact.get("characters") or []
    questions = artefact.get("baseline_questions") or []

    char_lines: list[str] = []
    for c in chars:
        nm = c.get("name", "")
        sd = c.get("short_description", "")
        char_lines.append(f"- {nm}: {sd}")

    q_lines: list[str] = []
    for idx, q in enumerate(questions, start=1):
        q_text = q.get("question_text") or q.get("text") or ""
        q_lines.append(f"Q{idx}: {q_text}")
        for opt in q.get("options", []):
            q_lines.append(f"  - {opt.get('text', '')}")

    parts = [
        f"TOPIC: {title}",
        f"SYNOPSIS TITLE: {syn_title}",
        f"SYNOPSIS SUMMARY: {syn_summary}",
        "CHARACTER OUTCOMES:",
        *char_lines,
        "BASELINE QUESTIONS:",
        *q_lines,
    ]
    return "\n".join(parts)


async def llm_judge(
    *,
    artefact: Any,
    tier: JudgeTier = "cheap",
    seed: int = 1,
    model: str | None = None,
) -> EvaluatorResult:
    """`JudgeFn` implementation backed by gemini-flash structured output."""
    from app.services import llm_service

    body = _format_artefact(artefact)
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Judge seed: {seed}. Be deterministic for this seed.\n\n{body}",
        },
    ]
    try:
        result = await llm_service.llm_service.get_structured_response(
            tool_name="topic_judge",
            messages=messages,
            response_model=_JudgeOutput,
            model=model or JUDGE_DEFAULT_MODEL,
            max_output_tokens=JUDGE_MAX_TOKENS,
            timeout_s=JUDGE_TIMEOUT_S,
            text_params={"temperature": 0.0 + 0.05 * (seed % 4)},
            trace_id=f"topic-judge-seed-{seed}",
        )
    except Exception as exc:
        logger.warning("llm_judge.failed", seed=seed, error=str(exc))
        # Fail safe: treat judge failures as non-blocking so structural
        # eval remains the gate. The caller can still decide to retry.
        return EvaluatorResult(
            score=50,
            blocking_reasons=(),
            non_blocking_notes=(f"judge_unavailable:{type(exc).__name__}",),
            tier=tier,
        )

    return EvaluatorResult(
        score=int(result.score),
        blocking_reasons=tuple(result.blocking_reasons),
        non_blocking_notes=tuple(result.non_blocking_notes),
        tier=tier,
    )
