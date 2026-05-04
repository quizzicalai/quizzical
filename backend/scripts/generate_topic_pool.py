"""Generate a ranked LLM-curated pool of quiz topic candidates.

Operator-side helper for §21 Phase 3 starter-pack draft flow. Asks an LLM
to brainstorm a large pool of quiz-friendly topics ranked by audience
appeal, then writes them to JSON for downstream consumption by
``scripts/generate_ranked_pack_candidates.py --topic-pool <file>``.

`AC-PRECOMP-DRAFT-6` (proposed):
- The pool generator produces N >= 50 candidate topics in a single LLM
  call (multi-shot if N > 80) with deterministic seed for reproducibility.
- Each candidate carries ``{slug, display_name, expected_outcome_count,
  rationale, category}`` and slugs are kebab-case ASCII unique.
- Output JSON is a list ordered best-first by appeal/quizability, ready
  to feed the ranked candidate generator.

Run::

    python -m scripts.generate_topic_pool --target 200 \
        --out configs/precompute/starter_packs/llm_topic_pool.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, field_validator

logger = structlog.get_logger(__name__)

POOL_BATCH_SIZE = 60  # gemini-flash structured output handles ~60 reliably
TOPIC_POOL_DEFAULT_MODEL = "gemini/gemini-flash-latest"
TOPIC_POOL_MAX_TOKENS = 10000
TOPIC_POOL_TIMEOUT_S = 120


class _PoolTopic(BaseModel):
    # Avoid min_length/max_length constraints on non-string fields —
    # Gemini's structured-output schema renderer rejects them.
    slug: str
    display_name: str
    expected_outcome_count: int
    category: str
    rationale: str

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        v = v.strip().lower()
        # Normalise anything the LLM might return
        v = re.sub(r"[^a-z0-9]+", "-", v).strip("-")
        return v or "unknown"


class _PoolResponse(BaseModel):
    topics: list[_PoolTopic]


_POOL_SYSTEM_PROMPT = (
    "You are a casting director for a viral 'Which X are you?' quiz platform. "
    "Your job: brainstorm topics with the highest probability of being chosen "
    "by a real user as their quiz subject. Each topic must have a small, "
    "exhaustive set of well-known archetypes/characters/types (3-12 outcomes) "
    "that the user could be matched to. Topics must be globally recognisable "
    "(or at least widely-known within an English-speaking audience), evergreen, "
    "and not require obscure trivia. Avoid: anything political, NSFW, "
    "religious doctrine, or culturally exclusionary. Bias toward iconic "
    "fictional universes, pop-culture taxonomies, classic personality "
    "frameworks, established sports/music canons, and lifestyle categories."
)


_SEED_CATEGORY_HINTS: dict[int, str] = {
    1: "broad mix: tv, film, gaming, music, personality, lifestyle, sports, mythology, literature",
    2: "lean heavily on tv shows, animated series, anime, and web series",
    3: "lean heavily on film franchises, book series, comics, and mythology",
    4: "lean heavily on music acts, sports teams/athletes, and lifestyle/wellness",
    5: "lean heavily on gaming franchises, personality frameworks, and internet culture",
}


def _user_prompt(target: int, seed: int) -> str:
    category_hint = _SEED_CATEGORY_HINTS.get(seed % len(_SEED_CATEGORY_HINTS) or len(_SEED_CATEGORY_HINTS), _SEED_CATEGORY_HINTS[1])
    return (
        f"Brainstorm exactly {target} DIFFERENT quiz topics ranked best-first by their "
        "expected user appeal. Use the structured output schema. For each topic: "
        "give a kebab-case slug, a human display_name, the expected number of "
        "well-known outcomes (3-12), a short category label "
        "(e.g. 'tv', 'film', 'literature', 'gaming', 'personality', 'music', "
        "'sports', 'lifestyle', 'mythology'), and a one-sentence rationale "
        "explaining why this topic will perform well as a personality quiz. "
        f"Category focus for this batch: {category_hint}. "
        "Coverage requirement: every topic must have a UNIQUE slug. "
        "Aim for maximum variety — do not repeat similar topics."
    )


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


async def _call_llm(target: int, seed: int) -> _PoolResponse:
    from app.services import llm_service

    messages = [
        {"role": "system", "content": _POOL_SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(target, seed)},
    ]
    return await llm_service.llm_service.get_structured_response(
        tool_name="topic_pool_generator",
        messages=messages,
        response_model=_PoolResponse,
        model=TOPIC_POOL_DEFAULT_MODEL,
        max_output_tokens=TOPIC_POOL_MAX_TOKENS,
        timeout_s=TOPIC_POOL_TIMEOUT_S,
        text_params={"temperature": 0.55 + 0.1 * (seed % 5)},
        trace_id=f"topic-pool-seed-{seed}",
    )


def _dedupe(topics: list[_PoolTopic]) -> list[_PoolTopic]:
    seen: set[str] = set()
    out: list[_PoolTopic] = []
    for t in topics:
        if t.slug in seen:
            continue
        seen.add(t.slug)
        out.append(t)
    return out


async def generate_pool(
    target: int,
    *,
    seed_start: int = 1,
    exclude_slugs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return a deduplicated, best-first list of topic candidates.

    Calls the LLM in batches of ``POOL_BATCH_SIZE``. Client-side dedup
    removes duplicates across batches. Stops as soon as ``target`` unique
    topics are collected or several consecutive batches return zero new topics.
    """
    collected: list[_PoolTopic] = []
    # Pre-exclude slugs the caller already has (e.g. existing packs).
    hard_excludes: set[str] = set(exclude_slugs or [])
    barren_streak = 0
    seed = seed_start
    # 8 attempts: tolerates ~5 transient Gemini Responses-API parse failures
    # while still bounding runtime when the topic space is genuinely exhausted.
    while len(collected) < target and barren_streak < 8:
        ask = min(POOL_BATCH_SIZE, target - len(collected) + 5)
        try:
            resp = await _call_llm(ask, seed)
        except Exception as exc:
            logger.warning("topic_pool.llm_call_failed", seed=seed, error=str(exc))
            barren_streak += 1
            seed += 1
            continue
        known_slugs = {t.slug for t in collected} | hard_excludes
        new_topics = [t for t in resp.topics if t.slug not in known_slugs]
        before = len(collected)
        collected = _dedupe(collected + new_topics)
        added = len(collected) - before
        logger.info(
            "topic_pool.batch", seed=seed, requested=ask, returned=len(resp.topics), added=added
        )
        if added == 0:
            barren_streak += 1
        else:
            barren_streak = 0
        seed += 1
    return [t.model_dump() for t in collected[:target]]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", type=int, default=200, help="number of topics to collect")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("configs/precompute/starter_packs/llm_topic_pool.json"),
    )
    p.add_argument("--seed", type=int, default=1)
    p.add_argument(
        "--exclude-slugs-file",
        type=Path,
        default=None,
        help="optional JSON list of slugs to exclude from suggestions",
    )
    return p.parse_args(argv)


async def _amain(argv: list[str] | None = None) -> int:
    _configure_stdio_utf8()
    args = _parse_args(argv)
    excludes: list[str] = []
    if args.exclude_slugs_file and args.exclude_slugs_file.exists():
        excludes = json.loads(args.exclude_slugs_file.read_text(encoding="utf-8"))
    topics = await generate_pool(args.target, seed_start=args.seed, exclude_slugs=excludes)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(topics, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("topic_pool.written", path=str(args.out), count=len(topics))
    print(f"wrote {len(topics)} topics to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
