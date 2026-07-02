# app/agent/tools/image_tools.py
"""Pure-function FAL prompt builders (§7.8.3).

Goals:
- Predictable house style: every prompt ends with ``style_suffix`` +
  ``STYLE_ANCHOR``.
- Zero LLM calls in this module: it is hot-path and must stay sub-millisecond.

Branded-character strategy (introduced 2026-05):
- For branded/IP topics the orchestration layer (``image_pipeline``) now
  uses a small fallback ladder powered by ``build_branded_attempt_prompt``
  and ``build_descriptive_attempt_prompt``. The first rung passes the
  verbatim ``"<name> from <source>"`` so FAL can render a recognisable
  likeness — FAL handles licensing on its own side. Only if the literal
  rung returns no image do we fall back to the LLM-described physical
  prompt. The legacy ``build_character_image_prompt`` (descriptive-only)
  remains in place for non-branded topics where the source name would add
  no information (e.g. "Greek God", "Pokémon Type").
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.models.api import CharacterProfile, FinalResult, Synopsis

_MAX_PROMPT_CHARS: int = 600  # FAL handles long prompts but shorter = faster

# AC-IMG-STYLE-1..3 — immutable cross-builder style anchor.
# Appended to EVERY prompt in addition to the configurable ``style_suffix``,
# guaranteeing a recognisable house style even when operators tweak the suffix.
# Keep this string short and free of subject nouns so it never fights with the
# per-image content tokens.
STYLE_ANCHOR: str = (
    "unified illustrated quiz art style, single consistent palette, "
    "matching brushwork across all images in the series"
)

# Blackbox fix #2 — the two LARGE heroes (synopsis banner + result portrait)
# render at 1024px through FLUX dev, where soft/garbled faces and bad anatomy
# are the most common failure. ``_FACE_QUALITY_TOKENS`` are appended to the
# matched-character result prompt to bias toward a clean, well-rendered face;
# ``_FACE_NEGATIVES`` are added to its negative prompt to suppress the classic
# diffusion face artefacts. Kept short so they never crowd out the subject.
_FACE_QUALITY_TOKENS: str = (
    "detailed symmetric face, clear sharp eyes, clean facial features, "
    "well-rendered anatomy, sharp focus"
)
_FACE_NEGATIVES: str = (
    "deformed face, extra fingers, asymmetric eyes, blurry, "
    "distorted features, mangled hands, extra limbs"
)

# Blackbox fix #2 — the wide synopsis hero is an ESTABLISHING SCENE, not a
# portrait. It must NOT inherit the character path's "flat illustrated portrait"
# style suffix (which biases FLUX toward a head-and-shoulders crop of a
# non-person subject). This scene-framed suffix mirrors ``images.qa_style_suffix``
# and is applied inside ``build_synopsis_image_prompt`` regardless of the suffix
# the caller passes, so the wide hero always reads as a world establishing shot.
SCENE_STYLE_SUFFIX: str = (
    "wide establishing shot, cinematic scene, flat illustrated, soft lighting, "
    "muted cohesive palette, simple background, no text"
)

# ---------------------------------------------------------------------------
# Object-vs-person subject detection (owner fix 2026-07-02).
#
# Ground truth complaint: "which sandwich are you" produced a photo of a
# Vietnamese PERSON for the "Banh Mi" outcome. Root cause: every character
# builder frames the outcome as a *person* ("Portrait of <name>", the
# "flat illustrated portrait" style suffix, face-quality tokens), which is
# wrong whenever the quiz outcome is a food / object / place / concept.
#
# ``infer_subject_kind`` is a DETERMINISTIC, sub-millisecond heuristic (no
# LLM, hot-path safe) that classifies an outcome from its topic category
# (dominant signal) and the outcome name (secondary). Person-indicating
# words are checked FIRST so e.g. "Full House character" stays a person
# even though "house" is in the object lexicon. Anything unmatched keeps
# today's behaviour (person) — zero regression for the person topics that
# dominate the catalog.
# ---------------------------------------------------------------------------

SUBJECT_KIND_PERSON = "person"
SUBJECT_KIND_OBJECT = "object"

# Unambiguous OBJECT compounds checked BEFORE the person words, because they
# embed a person-word as marketing jargon: "Skincare HERO Ingredient" is an
# ingredient (object), not a hero (person).
_STRONG_OBJECT_PAT = re.compile(
    r"\b(?:ingredients?|skincare|sunscreens?|serums?)\b",
    re.IGNORECASE,
)

# Person/creature indicators — either the topic clearly asks "which PERSON /
# CHARACTER are you" or the outcome is a personified being that should still
# render as a portrait. Checked before the object lexicon (collision winner).
_PERSON_TOPIC_PAT = re.compile(
    # NB: matches person/persona(s) but NOT "personality" — "Bread type
    # personality" is an OBJECT topic; "personality" is quiz boilerplate.
    r"\b(?:charact\w*|persons?|personas?|people|hero(?:es|ine)?s?|villains?|"
    r"protagonists?|archetypes?|princes?s?e?s?|queens?|kings?|gods?|"
    r"goddess\w*|deit\w*|idols?|icons?|legends?|celebrit\w*|singers?|"
    r"rappers?|actors?|actress\w*|artists?|members?|siblings?|residents?|"
    r"students?|kids?|detectives?|doctors?|nurses?|chefs?|athletes?|"
    r"players?|fighters?|boxers?|wrestlers?|employees?|professions?|"
    r"jobs?|housew\w*|wizards?|witch\w*|sorcer\w*|warriors?|knights?|"
    r"ninjas?|samurai|pirates?|jedi|sith|vampires?|werewol\w*|scouts?|"
    r"hunters?|slayers?|guys?|girls?|boys?|wom[ae]n|m[ae]n\b|dads?|moms?|"
    r"sisters?|brothers?|friends?|besties?|duos?|crews?|dwell\w*|"
    r"citizens?|leaders?|presidents?|captains?|mentors?|teachers?|"
    r"stereotypes?|role\s*model\w*)\b",
    re.IGNORECASE,
)

# Food / drink outcomes — get "appetizing" product-shot framing.
_FOOD_TOPIC_PAT = re.compile(
    r"\b(?:sandwich\w*|coffee|espresso|latte|cappuccino|bread\w*|pizza\w*|"
    r"pasta\w*|curr(?:y|ies)|tacos?|burritos?|sushi|ramen|noodle\w*|soups?|"
    r"salads?|cheeses?|wines?|beers?|cocktails?|teas?|boba|smoothies?|"
    r"juices?|desserts?|cakes?|pies?|donuts?|doughnuts?|bagels?|croissants?|"
    r"pastr\w*|cookies?|cand(?:y|ies)|chocolate\w*|snack\w*|fruits?|"
    r"vegetables?|breakfast\w*|brunch\w*|dish(?:es)?|foods?|drinks?|"
    r"beverages?|burgers?|fries|dumplings?|sauces?|condiments?|spices?|"
    r"herbs?|meals?|cuisines?|ice\s*cream\w*|banh\s*mi|pho\b|bahn\s*mi)\b",
    re.IGNORECASE,
)

# Non-food objects / places / concepts — get styled-object framing. Human
# ACTIVITIES (sports, dances, yoga…) are deliberately absent: images of those
# legitimately contain people.
# NB: franchise-title traps are deliberately absent: "rings" (The Lord of
# the Rings), "stones" (The Rolling Stones), "parks" (Parks and Rec),
# "islands" (Love Island) — those topics' outcomes are people/characters.
_OBJECT_TOPIC_PAT = re.compile(
    r"\b(?:cars?|sneakers?|shoes?|boots?|handbags?|bags?|hats?|gemstones?|"
    r"crystals?|jewel\w*|watches|flowers?|plants?|trees?|succulents?|"
    r"instruments?|guitars?|furniture|chairs?|sofas?|couch(?:es)?|"
    r"d[eé]cor\w*|wallpapers?|colou?rs?|patterns?|fabrics?|aesthetics?|"
    r"scents?|perfumes?|fragrances?|candles?|tattoos?|fonts?|emojis?|"
    r"planets?|elements?|seasons?|weather|storms?|disasters?|holidays?|"
    r"months?|cit(?:y|ies)|towns?|neighbou?rhoods?|countr(?:y|ies)|"
    r"nations?|states?|beach(?:es)?|mountains?|"
    r"destinations?|kingdoms?|houses?|belts?|crayons?|"
    r"metals?|minerals?|vehicles?|bikes?|motorcycles?|boats?|"
    r"buildings?|rooms?|gadgets?|tools?|toys?|board\s*games?)\b",
    re.IGNORECASE,
)

# Style suffix + negatives applied whenever the subject is an object. Like
# ``SCENE_STYLE_SUFFIX``, this OVERRIDES the caller-passed (portrait) suffix
# — "flat illustrated portrait" would re-bias FLUX toward a person.
OBJECT_STYLE_SUFFIX: str = (
    "flat illustrated, centered single subject, styled product-shot framing, "
    "soft lighting, muted cohesive palette, simple background, no people, no text"
)
_OBJECT_NEGATIVES: str = (
    "person, human, man, woman, child, human face, human hands, "
    "portrait of a person, people, crowd"
)


def infer_subject_kind(
    *, name: str, category: str, description: str = ""
) -> str:
    """Classify a quiz outcome as ``person`` or ``object`` (deterministic).

    Signals, in priority order:
      1. Person words in the topic category → ``person`` ("Full House
         character", "D&D character class").
      2. Food/object/place words in the category or the outcome name →
         ``object`` ("Sandwich", "Coffee order", "Seattle neighborhood";
         name-level catches e.g. "Espresso" under a vague topic).
      3. Default ``person`` — exactly today's behaviour, so unknown topics
         regress nothing.

    ``description`` participates only as a weak name-level supplement (its
    first 80 chars), never overriding a category match. Pure function; no IO.
    """
    cat = (category or "").strip()
    if cat and _STRONG_OBJECT_PAT.search(cat):
        return SUBJECT_KIND_OBJECT
    if cat and _PERSON_TOPIC_PAT.search(cat):
        return SUBJECT_KIND_PERSON
    hay = " ".join(
        s for s in ((name or "").strip(), cat, (description or "")[:80]) if s
    )
    if _FOOD_TOPIC_PAT.search(hay) or _OBJECT_TOPIC_PAT.search(hay):
        return SUBJECT_KIND_OBJECT
    return SUBJECT_KIND_PERSON


def _is_food(name: str, category: str) -> bool:
    """True when the object outcome is specifically food/drink (gets the
    'appetizing' framing rather than the generic styled-object one)."""
    return bool(_FOOD_TOPIC_PAT.search(f"{name or ''} {category or ''}"))


def _merge_negatives(negative_prompt: str, extra: str) -> str:
    """Concat caller negatives with path-specific ones (tidy, non-empty)."""
    return ", ".join(p for p in ((negative_prompt or "").strip(), extra) if p) or extra


def derive_seed(session_id: Any, subject: str) -> int:
    """AC-IMG-STYLE-4 — deterministic uint32 seed for FAL RNG.

    Pins the random seed so that re-rendering the same (session, subject)
    pair produces visually identical output, and so that all images for a
    single quiz draw from a related-but-distinct seed neighbourhood (helping
    visual cohesion). Pure function; no IO.
    """
    raw = f"{session_id}|{subject}".encode("utf-8")
    digest = hashlib.blake2b(raw, digest_size=4).digest()
    return int.from_bytes(digest, "big")


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else (s[: n - 1].rstrip() + "…")


def _safe_descriptors(profile_text: str, short_description: str, max_chars: int = 240) -> str:
    """Pull short visual/personality clauses; drop sentences with proper-noun heavy content."""
    text = (short_description or "").strip()
    extra = (profile_text or "").strip()
    if extra:
        # Take the first sentence-ish chunk that doesn't look name-heavy.
        first = re.split(r"(?<=[.!?])\s+", extra)[0]
        if first and not _looks_name_heavy(first):
            text = f"{text}. {first}" if text else first
    return _truncate(text, max_chars)


def _looks_name_heavy(s: str) -> bool:
    """Crude heuristic: if more than 25% of tokens are Capitalized non-stopwords, treat as name-heavy.

    Used only to pick a *cleaner* clause from a noisy profile_text when we
    fall through to descriptive prompts. It does **not** block branded
    content — FAL handles licensing on its own side.
    """
    tokens = re.findall(r"[A-Za-z']+", s)
    if len(tokens) < 4:
        return False
    caps = sum(1 for t in tokens[1:] if t and t[0].isupper())
    return (caps / max(1, len(tokens) - 1)) > 0.25


def _compose_with_anchor(head: str, style_suffix: str) -> str:
    """Compose ``head + style_suffix + STYLE_ANCHOR`` so the anchor is never
    truncated regardless of how long the head/suffix get (AC-IMG-STYLE-2)."""
    tail = f". {style_suffix}. {STYLE_ANCHOR}".rstrip()
    head = (head or "").strip().rstrip(".")
    budget_for_head = max(0, _MAX_PROMPT_CHARS - len(tail) - 1)
    head = _truncate(head, budget_for_head)
    return f"{head}{tail}"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_branded_attempt_prompt(
    *,
    name: str,
    source: str,
    style_suffix: str,
    negative_prompt: str,
    subject_kind: str | None = None,
) -> dict[str, str]:
    """Attempt-1 prompt for a branded character: ``"<name> from <source>"``.

    Used as the first rung of the image-pipeline fallback ladder. FAL
    handles licensing on its side, so we pass the literal character +
    source name through whenever the topic is branded — this is what
    makes branded characters actually look like themselves.

    Object outcomes (2026-07-02 owner fix): when ``subject_kind`` is
    ``object`` — or, if unset, the deterministic heuristic classifies the
    outcome as one (e.g. "Frappuccino from Starbucks Drinks") — the prompt
    depicts the ITEM itself with the object style suffix + people-suppressing
    negatives instead of a "character portrait" (which rendered humans).
    """
    nm = (name or "").strip()
    src = (source or "").strip()
    kind = subject_kind or infer_subject_kind(name=nm, category=src)
    if kind == SUBJECT_KIND_OBJECT:
        noun = "the dish or drink itself, appetizing" if _is_food(nm, src) \
            else "the item itself, beautifully styled"
        if not nm:
            head = f"Illustration of {noun}"
        elif src:
            head = f"{nm} from {src}, illustration of {noun}, not a person"
        else:
            head = f"{nm}, illustration of {noun}, not a person"
        prompt = _compose_with_anchor(head, OBJECT_STYLE_SUFFIX)
        return {
            "prompt": prompt,
            "negative_prompt": _merge_negatives(negative_prompt, _OBJECT_NEGATIVES),
        }
    if not nm:
        head = "Illustrated character portrait"
    elif src:
        head = f"{nm} from {src}, illustrated character portrait"
    else:
        head = f"{nm}, illustrated character portrait"
    prompt = _compose_with_anchor(head, style_suffix)
    return {"prompt": prompt, "negative_prompt": negative_prompt}


def build_descriptive_attempt_prompt(
    *,
    description: str,
    style_suffix: str,
    negative_prompt: str,
    prefix: str | None = None,
    subject_kind: str = SUBJECT_KIND_PERSON,
) -> dict[str, str]:
    """Attempt-2/3 prompt: free-form physical description with no proper nouns.

    The orchestration layer obtains ``description`` via the
    ``character_describer`` LLM helper after a ``build_branded_attempt_prompt``
    rung returned no image (typical sign of a FAL safety/licensing refusal).

    ``subject_kind`` (2026-07-02 owner fix) switches the default framing from
    a person portrait to an object depiction (with the object style suffix +
    people-suppressing negatives). An explicit ``prefix`` still wins.
    """
    desc = _truncate(description or "", 280)
    if subject_kind == SUBJECT_KIND_OBJECT:
        head_prefix = prefix or "Illustrated depiction of the item itself (not a person):"
        head = f"{head_prefix} {desc}".strip()
        prompt = _compose_with_anchor(head, OBJECT_STYLE_SUFFIX)
        return {
            "prompt": prompt,
            "negative_prompt": _merge_negatives(negative_prompt, _OBJECT_NEGATIVES),
        }
    head_prefix = prefix or "Illustrated character portrait of a person:"
    head = f"{head_prefix} {desc}".strip()
    prompt = _compose_with_anchor(head, style_suffix)
    return {"prompt": prompt, "negative_prompt": negative_prompt}


def build_character_image_prompt(
    profile: CharacterProfile,
    *,
    category: str,
    analysis: dict[str, Any] | None,
    style_suffix: str,
    negative_prompt: str,
) -> dict[str, str]:
    """Descriptive character prompt used for non-branded topics.

    The orchestration layer routes branded/IP topics through
    ``build_branded_attempt_prompt`` instead so the character
    name + source reach FAL verbatim. For non-branded archetype
    topics ("Greek God", "Pókemon Type") this builder produces a
    descriptive prompt that includes the character name and topic
    so FAL has enough to work with.

    Object outcomes (2026-07-02 owner fix): a food/object/place outcome
    ("Banh Mi" in "Which Sandwich Are You") must depict the OBJECT itself —
    a styled, appetizing product shot — never a human. The deterministic
    ``infer_subject_kind`` heuristic picks the framing; person topics keep
    the exact prompt shape they had before.
    """
    name = (getattr(profile, "name", "") or "").strip()
    short_desc = getattr(profile, "short_description", "") or ""
    desc = _safe_descriptors(getattr(profile, "profile_text", ""), short_desc)
    cat = (category or "").strip()

    kind = infer_subject_kind(name=name, category=cat, description=short_desc)
    if kind == SUBJECT_KIND_OBJECT:
        # Descriptions are personality copy ("You are the ultimate hydrator…");
        # strip the second-person lead so it doesn't pull FLUX toward a person.
        desc = re.sub(r"^you(?:'re| are)\s+", "", desc, flags=re.IGNORECASE)
        noun = (
            "an appetizing, beautifully presented depiction of the dish or drink itself"
            if _is_food(name, cat)
            else "a beautifully styled depiction of the object or place itself"
        )
        head_bits: list[str] = []
        if name and cat:
            head_bits.append(f"{name} ({cat}), {noun}, not a person")
        elif name:
            head_bits.append(f"{name}, {noun}, not a person")
        elif cat:
            head_bits.append(f"Illustration for the topic '{cat}', {noun}")
        else:
            head_bits.append(f"Illustration, {noun}")
        if desc:
            head_bits.append(desc)
        prompt = _compose_with_anchor(": ".join(head_bits), OBJECT_STYLE_SUFFIX)
        return {
            "prompt": prompt,
            "negative_prompt": _merge_negatives(negative_prompt, _OBJECT_NEGATIVES),
        }

    head_bits = []
    if name and cat:
        head_bits.append(f"Portrait of {name} ({cat})")
    elif name:
        head_bits.append(f"Portrait of {name}")
    elif cat:
        head_bits.append(f"Portrait illustration for the topic '{cat}'")
    else:
        head_bits.append("Character portrait")
    if desc:
        head_bits.append(desc)
    prompt = _compose_with_anchor(": ".join(head_bits), style_suffix)
    return {"prompt": prompt, "negative_prompt": negative_prompt}


def build_synopsis_image_prompt(
    synopsis: Synopsis,
    *,
    category: str,
    analysis: dict[str, Any] | None,
    style_suffix: str,
    negative_prompt: str,
) -> dict[str, str]:
    """Wide hero (establishing scene) for the quiz synopsis card.

    Always includes the topic ``category`` verbatim so FAL can render something
    recognisable for branded topics. Licensing is handled by FAL's safety layer.

    Blackbox fix #2:
      * Reframed from the ABSTRACT "An evocative illustration of <category>" to a
        concrete establishing scene "In the world of <category>: <scene>" — the
        same universe-first framing the Q&A scene builder uses — so FAL grounds
        the banner in that world instead of producing vague symbolic clipart.
      * Uses the SCENE-framed style suffix (``SCENE_STYLE_SUFFIX``), NOT the
        character path's "portrait" suffix the caller passes in. The wide 16:9
        synopsis banner is an establishing shot, never a head-and-shoulders
        portrait; inheriting the portrait suffix was biasing FLUX toward a
        cropped portrait of a non-person subject.
    """
    summary = _truncate(getattr(synopsis, "summary", "") or "", 200)
    cat = (category or "").strip()
    if cat and summary:
        body = f"In the world of {cat}: {summary}"
    elif cat:
        body = f"In the world of {cat}: a wide establishing scene of that world"
    else:
        body = summary or "A wide establishing scene for a personality quiz"

    # Ignore the passed (portrait) suffix on purpose — the wide hero is a scene.
    prompt = _compose_with_anchor(body, SCENE_STYLE_SUFFIX)
    return {"prompt": prompt, "negative_prompt": negative_prompt}


def build_result_image_prompt(  # noqa: C901 — linear prompt-assembly orchestrator (brand/media branches)
    result: FinalResult,
    *,
    category: str,
    character_set: list[dict[str, Any]],
    style_suffix: str,
    negative_prompt: str,
    analysis: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Hero portrait for the final result card.

    When the result title names one of the characters in the set we use
    that character's name + topic for the prompt; otherwise we fall back
    to the result title + description. The topic ``category`` is always
    included — FAL handles licensing.

    AC-UX-2026-05-01 — recognisability rewrite. The previous variant
    leaned heavily on long ``profile_text`` descriptions which dragged
    FAL toward generic portraits even when a well-known character was
    matched. We now mirror the branded-attempt prompt shape used by
    the rest of the image pipeline (``"<name> from <source>, portrait"``)
    so the character's name + source land verbatim at the *start* of the
    prompt — concise but specific, per the design feedback. The longer
    descriptor is dropped on the matched-character path; the unmatched
    fallback continues to use the title + short snippet.
    """
    title = (getattr(result, "title", "") or "").strip()
    description = (getattr(result, "description", "") or "").strip()
    cat = (category or "").strip()

    matched: dict[str, Any] | None = None
    if title and character_set:
        # Title typically reads "You are <Name>" or contains the name.
        for c in character_set:
            name = (c.get("name") if isinstance(c, dict) else None) or ""
            if name and name.lower() in title.lower():
                matched = c
                break

    # 2026-07-02 owner fix — object outcomes ("You are Banh Mi!") must render
    # the OBJECT as a styled hero shot, never a person. Face-quality tokens +
    # face negatives on a sandwich actively pull FLUX toward rendering a human,
    # which is exactly the reported failure. Kind is inferred from the matched
    # character name (or the result title) + topic category.
    subject_name = (matched.get("name") or "").strip() if matched else title
    kind = infer_subject_kind(name=subject_name, category=cat)

    if kind == SUBJECT_KIND_OBJECT:
        noun = (
            "an appetizing, beautifully presented hero shot of the dish or drink itself"
            if _is_food(subject_name, cat)
            else "a beautifully styled hero shot of the object or place itself"
        )
        if matched and subject_name:
            nm = subject_name
            if cat:
                body = f"{nm} ({cat}), {noun}, not a person, centered"
            else:
                body = f"{nm}, {noun}, not a person, centered"
        else:
            # Unmatched: the title reads like "You are Banh Mi!" — keep it as
            # result context, framed around the object depiction.
            label = title or cat or "personality quiz result"
            body = f"Illustration for the result '{label}', {noun}, not a person"
            if description:
                body = f"{body}: {_truncate(description, 160)}"
        neg = _merge_negatives(negative_prompt, _OBJECT_NEGATIVES)
        prompt = _compose_with_anchor(body, OBJECT_STYLE_SUFFIX)
        return {"prompt": prompt, "negative_prompt": neg}

    # Blackbox fix #2 — this LARGE result portrait renders at 1024px through
    # FLUX dev, where soft/garbled faces are the #1 failure. Bias toward a clean
    # face with explicit quality tokens, and suppress the classic diffusion face
    # artefacts via face-specific negatives. The negatives are added for BOTH
    # branches (the unmatched fallback can still depict a person).
    if matched:
        nm = (matched.get("name") or "").strip()
        # Keep the head short and recognisable: name + source up front,
        # then a single "head-and-shoulders portrait" framing token, then the
        # face-quality tokens (FAL responds better to specific tokens at the
        # start than to long descriptive clauses).
        if nm and cat:
            body = (
                f"{nm} from {cat}, head-and-shoulders portrait, single character, "
                f"centered, {_FACE_QUALITY_TOKENS}"
            )
        elif nm:
            body = (
                f"{nm}, head-and-shoulders portrait, single character, centered, "
                f"{_FACE_QUALITY_TOKENS}"
            )
        elif cat:
            body = f"Portrait illustration for '{cat}', {_FACE_QUALITY_TOKENS}"
        else:
            body = f"Character portrait, {_FACE_QUALITY_TOKENS}"
    else:
        snippet = _truncate(description, 220)
        if title and cat:
            body = f"Illustration for the result '{title}' of a '{cat}' quiz: {snippet}"
        elif title:
            body = f"Illustration for the result '{title}': {snippet}"
        elif cat:
            body = f"Illustration for the result of a '{cat}' quiz: {snippet}"
        else:
            body = snippet or "Illustration for a personality quiz result"

    # Compose the face negatives onto whatever negative_prompt the caller passed
    # (dedup-free concat; FAL tolerates repeats but we keep it tidy).
    neg = ", ".join(p for p in (negative_prompt, _FACE_NEGATIVES) if p) or _FACE_NEGATIVES
    prompt = _compose_with_anchor(body, style_suffix)
    return {"prompt": prompt, "negative_prompt": neg}


# ---------------------------------------------------------------------------
# Same-universe Q&A imagery (DRAFT — behind quizzical.images
# .qa_generated_images_enabled). Builds a topic/universe-CONSISTENT prompt for a
# single question stem or answer option, so a "Harry Potter" quiz yields e.g.
# "Dumbledore looking into a pensieve, in the world of Harry Potter" rather than
# generic clipart. The topic is the *universe anchor* placed first; the Q&A
# string is the subject. Pure function, no LLM / IO — same hot-path contract as
# the rest of this module.
# ---------------------------------------------------------------------------

def build_qa_image_prompt(
    *,
    topic: str,
    text: str,
    kind: str = "answer",
    style_suffix: str,
    negative_prompt: str,
) -> dict[str, str]:
    """Same-universe scene prompt for one Q&A string.

    ``topic`` is the quiz topic / universe (e.g. "Harry Potter", "Disney
    Princess"); ``text`` is the question stem or answer option. ``kind`` is
    ``"question"`` or ``"answer"`` — answers describe a concrete subject/scene,
    questions a lighter establishing illustration. The universe is named
    verbatim and FIRST so FAL grounds the image in that world; FAL handles
    licensing on its side, exactly like the branded character path.
    """
    uni = (topic or "").strip()
    subject = _truncate(text or "", 200)
    if not subject and not uni:
        body = "An evocative symbolic illustration for a personality quiz"
    elif uni and subject:
        if kind == "question":
            body = (
                f"In the world of {uni}: an establishing scene illustrating "
                f"“{subject}”"
            )
        else:
            # Owner image-quality rule: the ANSWER string names the subject and
            # the image must depict THAT subject itself as the focus — an object
            # or food answer (e.g. "a banh mi sandwich") must show the thing,
            # never a person holding/eating it. "faithful, centered depiction of
            # X itself" biases FLUX toward the named subject without forbidding
            # people when the subject IS a character.
            body = (
                f"In the world of {uni}: a faithful, centered depiction of "
                f"{subject} itself, the named subject as the sole focus"
            )
    elif uni:
        body = f"An evocative illustration set in the world of {uni}"
    else:
        body = subject

    prompt = _compose_with_anchor(body, style_suffix)
    return {"prompt": prompt, "negative_prompt": negative_prompt}


def qa_image_alt(*, topic: str, text: str) -> str:
    """A concise, decorative-but-descriptive alt string for a bound Q&A image.

    Kept short; the meaningful content remains the Q&A text itself (the image is
    an enrichment, never the sole carrier of meaning)."""
    uni = (topic or "").strip()
    subject = _truncate(text or "", 120)
    if uni and subject:
        return f"{subject} — {uni}"
    if subject:
        return subject
    if uni:
        return f"Illustration for {uni}"
    return "Quiz illustration"
