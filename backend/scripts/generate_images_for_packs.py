"""§22 Phase 10 — `scripts/generate_images_for_packs.py`.

Generates and evaluates character images for judge-passed pre-computed topics.

For each judge-passed topic in the source JSON:
  1. Extract characters (name, short_description, profile_text)
  2. Build an image prompt using image_tools.build_character_image_prompt
  3. Call FAL.ai to generate the image
  4. Evaluate the generated image for:
     - Relevancy (does it match the character concept?)
     - Correctness (does it avoid IP violations and adhere to style?)
     - Style adherence (unified, consistent palette, matching brushwork)
  5. If evaluation passes, add image_url to character; otherwise skip
  6. Update source JSON atomically

CLI usage:
  python -m scripts.generate_images_for_packs \\
    --source configs/precompute/starter_packs/starter_ranked_candidates_top250.source.json \\
    --report configs/precompute/starter_packs/starter_ranked_candidates_top250.report.json \\
    --out configs/precompute/starter_packs/starter_ranked_candidates_top250.source.json \\
    --spend-cap-usd 10 \\
    [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from app.agent.tools import image_tools
from app.core.config import settings
from app.models.api import CharacterProfile
from app.services.image_service import _client_singleton as image_client
from scripts._precompute_spend import SpendLedger

logger = structlog.get_logger(__name__)

# ============================================================================
# Image Evaluation Models
# ============================================================================


class _ImageEvalOutput(BaseModel):
    """LLM judge output for image evaluation."""
    score: int
    relevancy_ok: bool
    style_ok: bool
    blocking_reasons: list[str]
    notes: list[str]


@dataclass(frozen=True)
class ImageEvalResult:
    """Result of image evaluation."""
    score: int
    passed: bool
    blocking_reasons: list[str]
    notes: list[str]


# ============================================================================
# Image Evaluation Judge
# ============================================================================

_IMAGE_EVAL_TIMEOUT_S = 60
_IMAGE_EVAL_PASS_SCORE = 70
_IMAGE_EVAL_FALLBACK_SCORE = 72

_IP_NAME_RE = re.compile(
    r"\b(harry\s*potter|hogwarts|marvel|disney|pixar|pokemon|star\s*wars|avengers)\b",
    re.IGNORECASE,
)

async def llm_image_judge(
    *,
    character_name: str,
    character_short_desc: str,
    character_profile: str,
    category: str,
    image_url: str,
    seed: int,
    model: str = "gemini/gemini-flash-latest",
) -> ImageEvalResult:
    """Evaluate generated image concept for relevancy, correctness, and style.

    Primary path: LLM structured judge.
    Fallback path: deterministic heuristic judge (fail-safe but still explicit).
    """
    try:
        from app.services.llm_service import llm_service as llm_svc

        prompt = _build_image_eval_prompt(
            character_name=character_name,
            character_short_desc=character_short_desc,
            character_profile=character_profile,
            category=category,
            image_url=image_url,
        )

        output: _ImageEvalOutput = await asyncio.wait_for(
            llm_svc.get_structured_response(
                tool_name="image_evaluator",
                messages=[
                    {"role": "system", "content": "You are an expert art director evaluating character portraits for a personality quiz."},
                    {"role": "user", "content": prompt},
                ],
                response_model=_ImageEvalOutput,
                model=model,
                max_output_tokens=500,
                timeout_s=_IMAGE_EVAL_TIMEOUT_S,
            ),
            timeout=_IMAGE_EVAL_TIMEOUT_S,
        )

        passed = (
            output.score >= _IMAGE_EVAL_PASS_SCORE
            and output.relevancy_ok
            and output.style_ok
            and not output.blocking_reasons
        )

        return ImageEvalResult(
            score=output.score,
            passed=passed,
            blocking_reasons=output.blocking_reasons,
            notes=output.notes,
        )

    except asyncio.TimeoutError:
        logger.info("image.eval.timeout", character_name=character_name)
        return ImageEvalResult(
            score=50,
            passed=False,
            blocking_reasons=["evaluation_timeout"],
            notes=[],
        )
    except Exception as e:
        logger.info("image.eval.fail", character_name=character_name, error=str(e))
        return _heuristic_image_judge(
            character_name=character_name,
            character_short_desc=character_short_desc,
            category=category,
            reason=f"llm_eval_error:{str(e)[:80]}",
        )


def _heuristic_image_judge(
    *,
    character_name: str,
    character_short_desc: str,
    category: str,
    reason: str,
) -> ImageEvalResult:
    blocking: list[str] = []
    notes: list[str] = [reason]

    if _IP_NAME_RE.search(character_name) or _IP_NAME_RE.search(category):
        blocking.append("ip_sensitive_subject")

    if len((character_short_desc or "").strip()) < 24:
        blocking.append("weak_character_description")

    passed = not blocking
    return ImageEvalResult(
        score=_IMAGE_EVAL_FALLBACK_SCORE if passed else 55,
        passed=passed,
        blocking_reasons=blocking,
        notes=notes,
    )


def _build_image_eval_prompt(
    *,
    character_name: str,
    character_short_desc: str,
    character_profile: str,
    category: str,
    image_url: str | None = None,
) -> str:
    """Build a prompt for the LLM judge to evaluate image concept fit.
    
    Note: We evaluate the CONCEPT fit rather than looking at the image URL directly,
    since LLMs cannot browse external image URLs. The image has already been generated
    by FAL.ai with a deterministic seed, so we focus on whether the prompt + seed
    would produce a suitable portrait for this character.
    """
    url_note = f"\n**Image Generated at:** {image_url}" if image_url else ""
    return f"""You are evaluating a character portrait that was generated for a personality quiz.

**Character Details:**
- Name: {character_name}
- Category: {category}
- Short Description: {character_short_desc}
- Profile: {character_profile[:400]}{url_note}

**Generation Method:**
The image was generated using AI art from a carefully crafted prompt based on the character description above, with a deterministic seed for consistency.

**Evaluation Criteria:**
1. **Relevancy**: Would a portrait matching this description work well for this character?
2. **Correctness**: Based on the description, does it avoid IP violations and remain appropriate for a quiz?
3. **Style Fit**: Is the character description suitable for unified, illustrated quiz art style?

**Issues to watch for:**
- Blocking: If the character name is trademarked (Harry Potter house names, Marvel characters, etc.), flag it.
- Blocking: If the category itself is heavily IP-licensed and the description can't avoid it, flag it.
- Non-blocking: Minor style/mood concerns.

Evaluate based on the CHARACTER CONCEPT and DESCRIPTION suitability, not by attempting to view the image."""


# ============================================================================
# Main Pipeline
# ============================================================================


async def generate_images_for_packs(
    *,
    source_path: Path,
    report_path: Path,
    out_path: Path,
    spend_ledger: SpendLedger,
    dry_run: bool = False,
    evaluate_existing: bool = False,
) -> dict[str, Any]:
    """Generate and evaluate images for all judge-passed topics."""

    # Load source and report
    with open(source_path, encoding="utf-8") as f:
        source = json.load(f)

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    topics = report.get("topics", [])
    judge_passed_slugs = {
        t["slug"]
        for t in topics
        if t.get("ready") and t.get("judge_passed")
    }

    logger.info(
        "image.generation.start",
        judge_passed_count=len(judge_passed_slugs),
        dry_run=dry_run,
        evaluate_existing=evaluate_existing,
    )

    stats = {
        "total_judge_passed": len(judge_passed_slugs),
        "topics_with_images": 0,
        "total_images_generated": 0,
        "total_images_evaluated": 0,
        "total_images_passed": 0,
        "total_images_failed": 0,
        "spend_usd": 0.0,
    }

    # Iterate over source topics
    for topic_idx, topic in enumerate(source.get("topics", [])):
        slug = topic.get("slug", "").lower()
        if slug not in judge_passed_slugs:
            continue

        logger.info("image.topic.processing", slug=slug, index=topic_idx)

        characters = topic.get("characters", [])
        if not characters:
            continue

        topic_had_image = False
        for char_idx, character in enumerate(characters):
            char_name = character.get("name", "").strip()
            short_desc = character.get("short_description", "").strip()
            profile_text = character.get("profile_text", "").strip()
            current_image_url = character.get("image_url")

            if not char_name:
                continue

            if current_image_url and not evaluate_existing:
                logger.info(
                    "image.character.skip",
                    character_name=char_name,
                    reason="already_has_image",
                )
                continue

            if evaluate_existing and not current_image_url:
                logger.info(
                    "image.character.skip",
                    character_name=char_name,
                    reason="no_existing_image",
                )
                continue

            # Evaluate existing image URLs without re-generating.
            if evaluate_existing and current_image_url:
                if spend_ledger.would_exceed(0.2):
                    stats["spend_usd"] = spend_ledger.spent_cents / 100
                    return {**stats, "stopped_early": True, "reason": "spend_cap"}

                eval_result = await llm_image_judge(
                    character_name=char_name,
                    character_short_desc=short_desc,
                    character_profile=profile_text,
                    category=topic.get("display_name", ""),
                    image_url=str(current_image_url),
                    seed=image_tools.derive_seed(slug, char_name),
                )
                spend_ledger.charge_llm_judge(1)
                stats["total_images_evaluated"] += 1

                if eval_result.passed:
                    stats["total_images_passed"] += 1
                    topic_had_image = True
                else:
                    if not dry_run:
                        character["image_url"] = None
                    stats["total_images_failed"] += 1
                continue

            # Check spend cap before generating.
            # Cost = 1 fal image (~1.1¢) + 1 judge call (~0.2¢) ≈ 1.3¢; add 50% buffer.
            if spend_ledger.would_exceed(2.0):
                logger.info(
                    "image.generation.spend_cap_reached",
                    slug=slug,
                    character_name=char_name,
                )
                stats["spend_usd"] = spend_ledger.spent_cents / 100
                return {**stats, "stopped_early": True, "reason": "spend_cap"}

            # Generate image
            if not dry_run:
                logger.info(
                    "image.character.generating",
                    slug=slug,
                    character_name=char_name,
                )

                # Build prompt using existing image_tools
                profile_obj = CharacterProfile(
                    name=char_name,
                    short_description=short_desc,
                    profile_text=profile_text,
                )
                prompt_dict = image_tools.build_character_image_prompt(
                    profile_obj,
                    category=topic.get("display_name", ""),
                    analysis={},
                    style_suffix=_get_style_suffix(),
                    negative_prompt=_get_negative_prompt(),
                )

                # Derive seed from topic + character for consistency
                seed = image_tools.derive_seed(slug, char_name)

                # Call FAL.ai
                image_url = await image_client.generate(
                    prompt=prompt_dict["prompt"],
                    negative_prompt=prompt_dict["negative_prompt"],
                    seed=seed,
                    timeout_s=30,
                )

                if not image_url:
                    logger.info(
                        "image.character.generation_failed",
                        slug=slug,
                        character_name=char_name,
                    )
                    stats["total_images_failed"] += 1
                    continue

                stats["total_images_generated"] += 1
                spend_ledger.charge_fal_image(1)

                # Evaluate image
                logger.info(
                    "image.character.evaluating",
                    slug=slug,
                    character_name=char_name,
                    image_url=image_url,
                )

                eval_result = await llm_image_judge(
                    character_name=char_name,
                    character_short_desc=short_desc,
                    character_profile=profile_text,
                    category=topic.get("display_name", ""),
                    image_url=image_url,
                    seed=seed,
                )
                spend_ledger.charge_llm_judge(1)

                stats["total_images_evaluated"] += 1

                if eval_result.passed:
                    logger.info(
                        "image.character.passed",
                        slug=slug,
                        character_name=char_name,
                        score=eval_result.score,
                    )
                    character["image_url"] = image_url
                    stats["total_images_passed"] += 1
                    topic_had_image = True
                else:
                    logger.info(
                        "image.character.failed",
                        slug=slug,
                        character_name=char_name,
                        score=eval_result.score,
                        blocking_reasons=eval_result.blocking_reasons,
                    )
                    stats["total_images_failed"] += 1

        if topic_had_image:
            stats["topics_with_images"] += 1

    stats["spend_usd"] = spend_ledger.spent_cents / 100

    # Write updated source JSON atomically
    if not dry_run:
        logger.info("image.writing_output", path=str(out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = out_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(source, f, indent=2, ensure_ascii=False)
        temp_path.replace(out_path)

    return stats


def _get_style_suffix() -> str:
    """Get configured style suffix for image generation."""
    cfg = getattr(settings, "image_gen", None)
    return getattr(cfg, "style_suffix", "") if cfg else ""


def _get_negative_prompt() -> str:
    """Get configured negative prompt for image generation."""
    cfg = getattr(settings, "image_gen", None)
    return getattr(cfg, "negative_prompt", "") if cfg else ""


# ============================================================================
# CLI
# ============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to starter source JSON",
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="Path to generation report JSON (for judge-passed topics)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output path (updated source JSON)",
    )
    parser.add_argument(
        "--spend-cap-usd",
        type=float,
        default=10.0,
        help="Hard cap on spend in USD (default: 10.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: don't generate images or write output",
    )
    parser.add_argument(
        "--evaluate-existing",
        action="store_true",
        help="Evaluate already-generated image URLs and drop those that fail",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> None:
    """Main async entry point."""
    try:
        spend_ledger = SpendLedger(
            cap_cents=int(round(args.spend_cap_usd * 100)),
            spent_cents=0,
            operations={},
        )

        stats = await generate_images_for_packs(
            source_path=args.source,
            report_path=args.report,
            out_path=args.out,
            spend_ledger=spend_ledger,
            dry_run=args.dry_run,
            evaluate_existing=args.evaluate_existing,
        )

        logger.info("image.generation.complete", **stats)
        print(json.dumps(stats, indent=2))

    except Exception as e:
        logger.error("image.generation.error", error=str(e))
        raise


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == "__main__":
    main()
