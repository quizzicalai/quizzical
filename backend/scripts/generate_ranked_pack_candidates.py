from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

PACK_V3_BASELINE_QUESTION_COUNT = 5
TOPIC_BUILD_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class RankedTopicCandidate:
    slug: str
    display_name: str
    aliases: tuple[str, ...] = ()
    source: str = "fallback"
    source_rank: int | None = None
    selection_reason: str = ""


FALLBACK_RANKED_TOPICS: tuple[RankedTopicCandidate, ...] = (
    RankedTopicCandidate(
        slug="avatar-nations",
        display_name="Avatar Nations",
        aliases=("avatar nation", "four nations", "atla nations"),
        source_rank=1,
        selection_reason="Evergreen fandom quiz with a canonical 4-outcome set.",
    ),
    RankedTopicCandidate(
        slug="divergent-factions",
        display_name="Divergent Factions",
        aliases=("divergent faction",),
        source_rank=2,
        selection_reason="Strong archetypal 5-way sort with stable franchise vocabulary.",
    ),
    RankedTopicCandidate(
        slug="ilvermorny-houses",
        display_name="Ilvermorny Houses",
        aliases=("ilvermorny house", "ilvermorny"),
        source_rank=3,
        selection_reason="Harry Potter-adjacent sorter with a compact 4-house outcome set.",
    ),
    RankedTopicCandidate(
        slug="four-temperaments",
        display_name="Four Temperaments",
        aliases=("temperaments", "humoral temperaments"),
        source_rank=4,
        selection_reason="Evergreen non-IP personality quiz with clean 4-way taxonomy.",
    ),
    RankedTopicCandidate(
        slug="stress-responses",
        display_name="Stress Responses",
        aliases=("fight flight freeze fawn", "trauma responses"),
        source_rank=5,
        selection_reason="Highly legible modern self-discovery quiz frame with 4 stable outcomes.",
    ),
    RankedTopicCandidate(
        slug="mtg-colors",
        display_name="Magic The Gathering Colors",
        aliases=("mtg colors", "mana colors", "magic colors"),
        source_rank=6,
        selection_reason="Durable 5-color identity quiz for tabletop / gaming audiences.",
    ),
    RankedTopicCandidate(
        slug="leadership-compass-points",
        display_name="Leadership Compass Points",
        aliases=("leadership compass",),
        source_rank=7,
        selection_reason="Compact 4-way workplace / team quiz with evergreen utility.",
    ),
    RankedTopicCandidate(
        slug="pokemon-regions",
        display_name="Pokemon Regions",
        aliases=("pokemon region", "pokémon regions"),
        source_rank=8,
        selection_reason="Broad Pokemon audience, but 9 outcomes makes it a second-wave candidate.",
    ),
    RankedTopicCandidate(
        slug="great-houses-of-westeros",
        display_name="Great Houses of Westeros",
        aliases=("game of thrones houses", "westeros houses"),
        source_rank=9,
        selection_reason="Recognizable franchise with strong quiz fit, but 9 outcomes increases authoring cost.",
    ),
    RankedTopicCandidate(
        slug="hunger-games-districts",
        display_name="Hunger Games Districts",
        aliases=("hunger games district",),
        source_rank=10,
        selection_reason="Popular YA franchise, but 13 outcomes makes it best suited for a later batch.",
    ),
)


def select_generation_queue(
    *,
    prod_topics: Sequence[dict[str, Any]],
    fallback_topics: Sequence[RankedTopicCandidate] = FALLBACK_RANKED_TOPICS,
    limit: int,
) -> list[RankedTopicCandidate]:
    """Select the next generation queue.

    Ordering rule:
    1. Existing production topics with `has_pack=False`, ordered by ascending
       `popularity_rank`.
    2. Curated evergreen fallback topics, preserving checked-in order.
    """
    if limit <= 0:
        return []

    queue: list[RankedTopicCandidate] = []
    seen_slugs: set[str] = set()
    prod_slugs = {str(row.get("slug") or "").strip() for row in prod_topics if row.get("slug")}

    unpacked = [row for row in prod_topics if not bool(row.get("has_pack"))]
    unpacked.sort(key=lambda row: (_rank_key(row.get("popularity_rank")), str(row.get("display_name") or "")))

    for row in unpacked:
        slug = str(row.get("slug") or "").strip()
        if not slug or slug in seen_slugs:
            continue
        queue.append(
            RankedTopicCandidate(
                slug=slug,
                display_name=str(row.get("display_name") or slug).strip(),
                aliases=(),
                source="production-popularity",
                source_rank=_coerce_int(row.get("popularity_rank")),
                selection_reason="Unpacked production topic ordered by popularity_rank.",
            )
        )
        seen_slugs.add(slug)
        if len(queue) >= limit:
            return queue

    for idx, topic in enumerate(fallback_topics, start=1):
        if topic.slug in seen_slugs or topic.slug in prod_slugs:
            continue
        queue.append(
            replace(
                topic,
                source="fallback",
                source_rank=topic.source_rank or idx,
                selection_reason=topic.selection_reason or "Curated evergreen fallback topic.",
            )
        )
        seen_slugs.add(topic.slug)
        if len(queue) >= limit:
            break

    return queue


def evaluate_topic_entry(
    topic: dict[str, Any],
    *,
    expected_question_count: int = PACK_V3_BASELINE_QUESTION_COUNT,
    min_characters: int = 4,
    max_characters: int = 6,
    expected_option_count: int | None = None,
) -> dict[str, Any]:
    """Apply a fail-closed structural evaluation to a v3 topic entry.

    The expected option count per question defaults to the character count
    (one option per outcome). Pass an explicit integer to override.
    """
    errors: list[str] = []
    warnings: list[str] = []

    synopsis = topic.get("synopsis") or {}
    title = str(synopsis.get("title") or "").strip()
    summary = str(synopsis.get("summary") or "").strip()
    if not title:
        errors.append("synopsis.title missing or empty")
    if not summary:
        errors.append("synopsis.summary missing or empty")

    characters = list(topic.get("characters") or [])
    if not (min_characters <= len(characters) <= max_characters):
        errors.append(
            f"character count {len(characters)} outside allowed range [{min_characters}, {max_characters}]"
        )

    effective_option_count = (
        int(expected_option_count) if expected_option_count is not None else len(characters)
    )

    names = _collect_character_errors(characters, errors)

    if len(names) != len(set(names)):
        errors.append("duplicate character names detected")

    questions = list(topic.get("baseline_questions") or [])
    if len(questions) != expected_question_count:
        errors.append(
            f"baseline question count {len(questions)} does not equal expected {expected_question_count}"
        )

    question_texts = _collect_question_errors(questions, effective_option_count, errors)

    if question_texts and len(question_texts) != len(set(question_texts)):
        errors.append("duplicate baseline question text detected")

    if not topic.get("aliases"):
        warnings.append("aliases missing; alias-exact lookup coverage may be weak")

    ready = not errors
    score = max(0, 100 - (5 * len(errors)) - (2 * len(warnings)))
    return {
        "ready": ready,
        "score": score,
        "errors": errors,
        "warnings": warnings,
        "character_count": len(characters),
        "question_count": len(questions),
    }


def _rank_key(value: Any) -> int:
    coerced = _coerce_int(value)
    return coerced if coerced is not None else 10**9


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _collect_character_errors(
    characters: list[dict[str, Any]],
    errors: list[str],
) -> list[str]:
    """Append per-character errors and return the collected normalised character names."""
    names: list[str] = []
    for idx, character in enumerate(characters, start=1):
        name = str(character.get("name") or "").strip()
        short_description = str(character.get("short_description") or "").strip()
        profile_text = str(character.get("profile_text") or "").strip()
        if not name:
            errors.append(f"characters[{idx}].name missing or empty")
        if not short_description:
            errors.append(f"characters[{idx}].short_description missing or empty")
        if not profile_text:
            errors.append(f"characters[{idx}].profile_text missing or empty")
        if name:
            names.append(name.casefold())
    return names


def _collect_question_errors(
    questions: list[dict[str, Any]],
    effective_option_count: int,
    errors: list[str],
) -> list[str]:
    """Append per-question errors and return the collected normalised question texts."""
    question_texts: list[str] = []
    for idx, question in enumerate(questions, start=1):
        question_text = str(question.get("question_text") or "").strip()
        if not question_text:
            errors.append(f"baseline_questions[{idx}].question_text missing or empty")
        else:
            question_texts.append(question_text.casefold())

        options = list(question.get("options") or [])
        if len(options) != effective_option_count:
            errors.append(
                f"baseline_questions[{idx}] option count {len(options)} does not equal expected {effective_option_count}"
            )

        option_texts: list[str] = []
        for opt_idx, option in enumerate(options, start=1):
            option_text = str(option.get("text") or "").strip()
            if not option_text:
                errors.append(f"baseline_questions[{idx}].options[{opt_idx}].text missing or empty")
                continue
            option_texts.append(option_text.casefold())

        if option_texts and len(option_texts) != len(set(option_texts)):
            errors.append(f"baseline_questions[{idx}] has duplicate option text")
    return question_texts


def _default_output_paths(limit: int) -> tuple[Path, Path]:
    base = Path("configs/precompute/starter_packs")
    return (
        base / f"starter_ranked_candidates_top{limit}.source.json",
        base / f"starter_ranked_candidates_top{limit}.report.json",
    )


async def _fetch_prod_topics(database_url: str) -> list[dict[str, Any]]:
    import asyncpg

    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn=dsn)
    try:
        rows = await conn.fetch(
            """
            select t.slug, t.display_name, t.popularity_rank, (t.current_pack_id is not null) as has_pack
            from topics t
            where t.policy_status = 'allowed'
            order by t.popularity_rank asc nulls last, t.display_name asc
            """
        )
    finally:
        await conn.close()
    return [dict(row) for row in rows]


def _to_plain_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"Expected Pydantic model or dict, got {type(value).__name__}")


def _normalize_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for option in options:
        text = str(option.get("text") or "").strip()
        if not text:
            continue
        payload: dict[str, Any] = {"text": text}
        image_url = str(option.get("image_url") or option.get("imageUrl") or "").strip()
        if image_url:
            payload["image_url"] = image_url
        out.append(payload)
    return out


def _empty_topic_entry(candidate: RankedTopicCandidate) -> dict[str, Any]:
    return {
        "slug": candidate.slug,
        "display_name": candidate.display_name,
        "aliases": list(candidate.aliases),
        "synopsis": {"title": "", "summary": ""},
        "characters": [],
        "baseline_questions": [],
    }


def _evaluation_sort_key(evaluation: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    """Rank topic evaluations from worst to best.

    Order: ready > has_content > score > character_count > question_count > fewer_errors.
    A topic that has any synopsis/characters/questions always beats an empty
    placeholder, even if its score is lower.
    """
    char_count = int(evaluation.get("character_count") or 0)
    question_count = int(evaluation.get("question_count") or 0)
    has_content = 1 if (char_count > 0 or question_count > 0) else 0
    return (
        1 if bool(evaluation.get("ready")) else 0,
        has_content,
        int(evaluation.get("score") or 0),
        char_count,
        question_count,
        -len(list(evaluation.get("errors") or [])),
    )


async def _generate_topic_entry_with_retries(
    candidate: RankedTopicCandidate,
    *,
    generate_topic_entry: Callable[[RankedTopicCandidate], Awaitable[dict[str, Any]]] | None = None,
    max_attempts: int = TOPIC_BUILD_MAX_ATTEMPTS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempts = max(1, int(max_attempts))
    build_topic = generate_topic_entry or _generate_topic_entry
    best_topic = _empty_topic_entry(candidate)
    best_evaluation = evaluate_topic_entry(best_topic)
    last_error: Exception | None = None

    for _ in range(attempts):
        try:
            topic = await build_topic(candidate)
        except Exception as exc:
            last_error = exc
            continue

        evaluation = evaluate_topic_entry(topic)
        if _evaluation_sort_key(evaluation) > _evaluation_sort_key(best_evaluation):
            best_topic = topic
            best_evaluation = evaluation
        if evaluation["ready"]:
            return topic, evaluation

    if last_error is not None and not best_evaluation["ready"]:
        best_evaluation = {
            **best_evaluation,
            "errors": [
                *list(best_evaluation.get("errors") or []),
                f"generation exception: {type(last_error).__name__}: {last_error}",
            ],
        }
    return best_topic, best_evaluation


async def _generate_topic_entry(candidate: RankedTopicCandidate) -> dict[str, Any]:
    from app.agent.tools.content_creation_tools import (
        draft_character_profiles,
        generate_baseline_questions,
    )
    from app.agent.tools.intent_classification import analyze_topic
    from app.agent.tools.planning_tools import generate_character_list, plan_quiz

    analysis = analyze_topic(candidate.display_name)
    plan = await plan_quiz.ainvoke(
        {
            "category": candidate.display_name,
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "intent": analysis.get("intent", "identify"),
            "names_only": analysis.get("names_only", False),
        }
    )

    title = str(getattr(plan, "title", "") or "").strip() or f"Which {candidate.display_name} are you?"
    summary = str(getattr(plan, "synopsis", "") or "").strip() or f"A quiz about {candidate.display_name}."

    names = await generate_character_list.ainvoke(
        {
            "category": candidate.display_name,
            "synopsis": summary,
            "analysis": analysis,
        }
    )
    names = [str(name).strip() for name in list(names or []) if str(name).strip()]
    if not names:
        names = [str(name).strip() for name in list(getattr(plan, "ideal_archetypes", []) or []) if str(name).strip()]

    max_characters = 6
    names = names[:max_characters]

    profiles_obj = await draft_character_profiles.ainvoke(
        {
            "character_names": names,
            "category": candidate.display_name,
            "analysis": analysis,
        }
    )
    profiles = [_to_plain_model(profile) for profile in list(profiles_obj or [])]

    synopsis_payload = {"title": title, "summary": summary}
    questions_obj = await generate_baseline_questions.ainvoke(
        {
            "category": candidate.display_name,
            "character_profiles": profiles,
            "synopsis": synopsis_payload,
            "analysis": analysis,
            "num_questions": PACK_V3_BASELINE_QUESTION_COUNT,
        }
    )
    questions = []
    for question in list(questions_obj or []):
        q_dict = _to_plain_model(question)
        questions.append(
            {
                "question_text": str(q_dict.get("question_text") or "").strip(),
                "options": _normalize_options(list(q_dict.get("options") or [])),
            }
        )

    return {
        "slug": candidate.slug,
        "display_name": candidate.display_name,
        "aliases": list(candidate.aliases),
        "synopsis": synopsis_payload,
        "characters": [
            {
                "name": str(profile.get("name") or "").strip(),
                "short_description": str(profile.get("short_description") or "").strip(),
                "profile_text": str(profile.get("profile_text") or "").strip(),
                **(
                    {"image_url": str(profile.get("image_url") or profile.get("imageUrl") or "").strip()}
                    if str(profile.get("image_url") or profile.get("imageUrl") or "").strip()
                    else {}
                ),
            }
            for profile in profiles
        ],
        "baseline_questions": questions,
    }


async def generate_candidate_batch(
    *,
    candidates: Sequence[RankedTopicCandidate],
    budget_usd: float,
    estimated_usd_per_topic: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    effective_limit = len(candidates)
    if estimated_usd_per_topic > 0:
        effective_limit = min(effective_limit, max(1, int(budget_usd // estimated_usd_per_topic) or 1))

    selected = list(candidates[:effective_limit])
    topics: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []

    for candidate in selected:
        topic, evaluation = await _generate_topic_entry_with_retries(candidate)
        topics.append(topic)
        report_rows.append(
            {
                "slug": candidate.slug,
                "display_name": candidate.display_name,
                "source": candidate.source,
                "source_rank": candidate.source_rank,
                "selection_reason": candidate.selection_reason,
                "estimated_cost_usd": round(float(estimated_usd_per_topic), 4),
                **evaluation,
            }
        )

    source_doc = {
        "version": 3,
        "built_in_env": "starter",
        "description": "Draft starter topic packs generated from the ranked candidate pipeline. Review evaluation report before building/importing.",
        "topics": topics,
    }
    report_doc = {
        "budget_usd": float(budget_usd),
        "estimated_usd_per_topic": float(estimated_usd_per_topic),
        "estimated_total_usd": round(float(estimated_usd_per_topic) * len(selected), 4),
        "topics": report_rows,
    }
    return source_doc, report_doc


async def _main_async(args: argparse.Namespace) -> int:
    prod_topics: list[dict[str, Any]] = []
    if args.database_url:
        prod_topics = await _fetch_prod_topics(args.database_url)

    queue = select_generation_queue(prod_topics=prod_topics, fallback_topics=FALLBACK_RANKED_TOPICS, limit=args.limit)
    source_doc, report_doc = await generate_candidate_batch(
        candidates=queue,
        budget_usd=args.budget_usd,
        estimated_usd_per_topic=args.estimated_usd_per_topic,
    )

    out_path = Path(args.out)
    report_path = Path(args.report_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(source_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote source: {out_path}")
    print(f"wrote report: {report_path}")
    print(f"topics: {len(source_doc['topics'])}")
    print(f"estimated_total_usd: {report_doc['estimated_total_usd']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate ranked v3 starter-pack draft candidates.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum topics to generate before budget capping.")
    parser.add_argument("--budget-usd", type=float, default=50.0, help="Total budget available for this generation pass.")
    parser.add_argument(
        "--estimated-usd-per-topic",
        type=float,
        default=0.05,
        help="Conservative planning estimate used to cap the generated batch size.",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default="",
        help="Optional production DATABASE_URL; when supplied, unpacked production topics are queued before fallback topics.",
    )
    default_out, default_report = _default_output_paths(limit=5)
    parser.add_argument("--out", type=str, default=str(default_out))
    parser.add_argument("--report-out", type=str, default=str(default_report))
    return parser


def _configure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


def main() -> int:
    _configure_stdio_utf8()
    parser = build_parser()
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be > 0")
    if args.budget_usd <= 0:
        parser.error("--budget-usd must be > 0")
    if args.estimated_usd_per_topic <= 0:
        parser.error("--estimated-usd-per-topic must be > 0")
    if args.out == str(_default_output_paths(limit=5)[0]) and args.limit != 5:
        args.out = str(_default_output_paths(limit=args.limit)[0])
    if args.report_out == str(_default_output_paths(limit=5)[1]) and args.limit != 5:
        args.report_out = str(_default_output_paths(limit=args.limit)[1])
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
