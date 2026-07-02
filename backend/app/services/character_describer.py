# app/services/character_describer.py
"""LLM helpers for branded-character image generation.

Two narrow capabilities exposed by this module:

1. ``classify_topic_brand`` — given a topic ``display_name`` (e.g.
   "Which Hogwarts House Are You?"), return whether the topic is rooted
   in an identifiable IP/franchise and, if so, the canonical franchise
   name (e.g. ``"Harry Potter"``). Used by the offline regeneration
   script to scope work to branded topics only.

2. ``describe_character_physically`` — given a character name and the
   source franchise, return a single-sentence physical description that
   FAL can render when the literal ``"<character> from <source>"``
   prompt is refused by the safety checker. Two ``strict_level`` rungs:

   * ``0`` — original prompt: "highlight major physical characteristics;
     do not mention any branded/licensed items".
   * ``1`` — stricter retry: also forbids proper nouns and minimises
     franchise-specific costume cues.

Both helpers fail soft (``None`` on any error) so callers always have a
clean fallback path. They share a small Pydantic schema each so the
structured-response codepath of ``llm_service`` can do the JSON parsing.
"""

from __future__ import annotations

from typing import Optional

import structlog
from pydantic import BaseModel, Field

from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Topic brand classification
# ---------------------------------------------------------------------------

class _BrandClassification(BaseModel):
    is_branded: bool = Field(
        description="True if the topic is rooted in an identifiable IP, "
        "franchise, TV show, film, book series, or other branded universe."
    )
    source: str = Field(
        default="",
        description="Canonical franchise/source name when is_branded is true "
        "(e.g. 'Harry Potter', 'Marvel Cinematic Universe', 'The Real Housewives "
        "of Beverly Hills'). Empty string when is_branded is false.",
    )


_BRAND_CLASSIFY_SYSTEM = (
    "You classify personality-quiz topics by whether the characters in the "
    "topic come from a specific identifiable IP/franchise (TV show, film, "
    "book series, video game, comic, anime, reality show, etc.) versus a "
    "generic archetype set (mythology, animals, professions, elemental types)."
)


_BRAND_CLASSIFY_USER_TEMPLATE = """\
Classify the following personality-quiz topic.

Topic display name: {display_name}
{summary_block}

Return:
- is_branded: true if the topic asks about characters from a specific IP /
  franchise (e.g. Harry Potter, Star Wars, Friends, Stranger Things,
  Lord of the Rings, The Apothecary Diaries, Real Housewives of Atlanta).
  false for generic archetypes (Greek gods, Pokémon types, Hogwarts houses
  if presented purely as values like courage/wit, zodiac signs, professions,
  D&D classes when generic).
- source: when is_branded is true, the canonical short name of the
  franchise (e.g. "Harry Potter", "Star Wars", "Friends", "Stranger Things",
  "The Lord of the Rings", "The Apothecary Diaries",
  "The Real Housewives of Atlanta"). Empty string otherwise.

Be liberal in marking is_branded=true when the characters are clearly from a
specific media property — FAL handles licensing on its side, so we want to
pass the franchise through whenever it would help recognition.
"""


async def classify_topic_brand(
    *,
    display_name: str,
    summary: str | None = None,
    model: str = "gemini/gemini-flash-latest",
    timeout_s: int = 30,
) -> dict[str, object]:
    """Return ``{"is_branded": bool, "source": str}``.

    Never raises. On any failure returns ``{"is_branded": False, "source": ""}``
    so the caller can safely skip the topic.
    """
    display = (display_name or "").strip()
    if not display:
        return {"is_branded": False, "source": ""}

    summary_block = ""
    if summary and summary.strip():
        summary_block = f"Topic summary: {summary.strip()[:400]}\n"

    user_msg = _BRAND_CLASSIFY_USER_TEMPLATE.format(
        display_name=display, summary_block=summary_block
    )

    try:
        out: _BrandClassification = await llm_service.get_structured_response(
            tool_name="brand_classifier",
            messages=[
                {"role": "system", "content": _BRAND_CLASSIFY_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            response_model=_BrandClassification,
            model=model,
            max_output_tokens=800,
            timeout_s=timeout_s,
        )
    except Exception as e:
        logger.info("brand.classify.fail", display_name=display, error=str(e))
        return {"is_branded": False, "source": ""}

    source = (out.source or "").strip() if out.is_branded else ""
    return {"is_branded": bool(out.is_branded), "source": source}


# ---------------------------------------------------------------------------
# Physical description fallback
# ---------------------------------------------------------------------------

class _CharacterPhysical(BaseModel):
    description: str = Field(
        description="One sentence (max ~35 words) describing the character's "
        "major physical characteristics suitable for an AI image generator."
    )


_DESCRIBE_SYSTEM = (
    "You write tight one-sentence physical descriptions of fictional "
    "characters for an AI image generator. Focus on hair, build, age, "
    "skin/eye colour, posture, and silhouette/costume shape."
)

# 2026-07-02 owner fix — object outcomes ("Banh Mi" in "Which Sandwich Are
# You") were being described like PEOPLE ("hair, build, age…"), which is how
# the descriptive fallback rungs ended up rendering a person instead of the
# sandwich. When the caller's deterministic heuristic
# (``image_tools.infer_subject_kind``) says the subject is an object, the
# describer must describe the ITEM's visual qualities and explicitly exclude
# humans from the description.
_DESCRIBE_OBJECT_SYSTEM = (
    "You write tight one-sentence visual descriptions of foods, drinks, "
    "objects, and places for an AI image generator. Focus on shape, colour, "
    "texture, materials, composition, and presentation (plating, garnish, "
    "backdrop). The subject is an ITEM, not a person — never describe, "
    "mention, or imply any people in the description."
)


def _describe_user_prompt(
    *, name: str, source: str, strict_level: int, subject_kind: str = "person"
) -> str:
    src = source.strip() or "their source material"
    if subject_kind == "object":
        if strict_level <= 0:
            return (
                f"Create a short, 1 sentence visual description of {name} "
                f"from {src}. {name} is a food, drink, object, or place — "
                f"describe the ITEM ITSELF (shape, colours, textures, "
                f"presentation), never a person holding, eating, or using it; "
                f"do not mention any branded or licensed items."
            )
        return (
            f"Create a short, 1 sentence visual description of {name} from "
            f"{src}. {name} is a food, drink, object, or place — describe "
            f"the ITEM ITSELF (shape, colours, textures, presentation), "
            f"never a person. Do NOT use any proper nouns (no product names, "
            f"no franchise names, no place names) and avoid logos, labels, "
            f"or branded packaging — describe only generic shapes, colours, "
            f"and materials."
        )
    if strict_level <= 0:
        return (
            f"Create a short, 1 sentence description of {name} from {src} "
            f"that highlights their major physical characteristics; do not "
            f"mention any branded or licensed items."
        )
    # strict_level >= 1 — stricter retry: no proper nouns, no franchise-specific costume.
    return (
        f"Create a short, 1 sentence description of {name} from {src} that "
        f"highlights their major physical characteristics. Do NOT use any "
        f"proper nouns (no character names, no franchise names, no place "
        f"names). Avoid franchise-specific costume cues, insignia, logos, "
        f"crests, wands, weapons, or named items — describe only generic "
        f"clothing shapes, colours, hair, build, and age."
    )


async def describe_character_physically(
    *,
    name: str,
    source: str,
    strict_level: int = 0,
    subject_kind: str = "person",
    model: str = "gemini/gemini-flash-latest",
    timeout_s: int = 30,
) -> Optional[str]:
    """Return a one-sentence physical description, or ``None`` on failure.

    ``subject_kind`` (``"person"`` | ``"object"``) switches the instruction
    set: object subjects are described as the item itself (colour, texture,
    presentation) with an explicit no-humans constraint. Callers derive the
    kind via the deterministic ``image_tools.infer_subject_kind`` heuristic.
    """
    nm = (name or "").strip()
    if not nm:
        return None
    system_msg = (
        _DESCRIBE_OBJECT_SYSTEM if subject_kind == "object" else _DESCRIBE_SYSTEM
    )
    try:
        out: _CharacterPhysical = await llm_service.get_structured_response(
            tool_name="character_describer",
            messages=[
                {"role": "system", "content": system_msg},
                {
                    "role": "user",
                    "content": _describe_user_prompt(
                        name=nm,
                        source=source,
                        strict_level=int(strict_level),
                        subject_kind=subject_kind,
                    ),
                },
            ],
            response_model=_CharacterPhysical,
            model=model,
            max_output_tokens=800,
            timeout_s=timeout_s,
        )
    except Exception as e:
        logger.info(
            "character.describe.fail",
            name=nm,
            source=source,
            strict_level=strict_level,
            error=str(e),
        )
        return None

    desc = (out.description or "").strip()
    return desc or None
