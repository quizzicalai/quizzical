# tests/unit/agent/tools/test_image_tools_subject_kind.py
"""Object-vs-person image prompt fix (2026-07-02 owner complaint).

Ground truth: "which sandwich are you" rendered the "Banh Mi" outcome as a
photo of a Vietnamese PERSON instead of the sandwich. These tests pin the
deterministic ``infer_subject_kind`` heuristic and the object-mode framing
of every character/result builder: food/object/place outcomes must depict
the ITEM itself (styled/appetizing shot) and suppress humans; person topics
must keep the exact prompt shape they had before (zero regression).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]

STYLE = "flat illustrated portrait, soft lighting, muted palette, no text"
NEG = "text, watermark, logo, signature, blurry, deformed, low quality"


@pytest.fixture
def it():
    from app.agent.tools import image_tools
    return image_tools


def _profile(name: str, short: str = "A beloved classic with real character",
             text: str = "Always dependable and full of flavour."):
    from app.models.api import CharacterProfile
    return CharacterProfile(
        name=name, short_description=short, profile_text=text
    )


# ---------------------------------------------------------------------------
# infer_subject_kind — the deterministic heuristic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("Banh Mi", "Sandwich"),                     # the reported failure
        ("Espresso", "Coffee order"),
        ("Sourdough", "Bread type personality"),
        ("Margherita", "Pizza"),
        ("Old Fashioned", "Cocktail"),
        ("Green Curry", "Curry"),
        ("Ballard", "Seattle neighborhood"),
        ("Peony", "Flower"),
        ("Mid-century Modern", "Interior design aesthetic"),
        ("Croissant", "Breakfast vibe"),
    ],
)
def test_object_outcomes_detected(it, name, category):
    assert it.infer_subject_kind(name=name, category=category) == it.SUBJECT_KIND_OBJECT


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("Hermione Granger", "Harry Potter character"),
        ("The Architect", "MBTI Types"),             # unmatched -> default person
        ("Leslie Knope", "Parks and Rec character"),
        ("Zeus", "Greek God"),
        ("Michael Bluth", "Arrested Development Bluth family member"),
        ("Wednesday Addams", "Nevermore Academy student"),
        ("Rocky Balboa", "Boxing legend"),
        ("Regina George", "High school stereotype"),
    ],
)
def test_person_outcomes_detected(it, name, category):
    assert it.infer_subject_kind(name=name, category=category) == it.SUBJECT_KIND_PERSON


def test_person_words_win_over_object_words(it):
    # "house" is in the object lexicon, but "character" pins it to person.
    assert (
        it.infer_subject_kind(name="Danny Tanner", category="Full House character")
        == it.SUBJECT_KIND_PERSON
    )
    # "Housewives" must never be treated as "house".
    assert (
        it.infer_subject_kind(name="NeNe Leakes",
                              category="Real Housewives of Atlanta")
        == it.SUBJECT_KIND_PERSON
    )


def test_name_level_signal_catches_food_under_vague_topic(it):
    assert (
        it.infer_subject_kind(name="Banh Mi", category="Lunch match")
        == it.SUBJECT_KIND_OBJECT
    )


def test_unknown_defaults_to_person(it):
    assert it.infer_subject_kind(name="Xyzzy", category="Quuxes") == it.SUBJECT_KIND_PERSON


def test_strong_object_compounds_beat_person_jargon(it):
    # "Skincare HERO Ingredient" embeds a person-word as marketing jargon —
    # the outcome ("Retinol") is an ingredient, not a hero.
    assert (
        it.infer_subject_kind(
            name="Retinol", category="What Is Your Skincare Hero Ingredient?"
        )
        == it.SUBJECT_KIND_OBJECT
    )


# ---------------------------------------------------------------------------
# build_character_image_prompt — object mode
# ---------------------------------------------------------------------------

def test_character_prompt_object_mode_depicts_the_item(it):
    out = it.build_character_image_prompt(
        _profile("Banh Mi"), category="Sandwich", analysis={},
        style_suffix=STYLE, negative_prompt=NEG,
    )
    p = out["prompt"].lower()
    assert "banh mi" in p
    assert "portrait of" not in p                    # no person framing
    assert "not a person" in p
    assert "dish or drink itself" in p               # appetizing food framing
    assert "no people" in p                          # object suffix applied
    assert STYLE not in out["prompt"]                # portrait suffix overridden
    assert it.STYLE_ANCHOR in out["prompt"]          # house style preserved
    # People-suppressing negatives merged onto caller negatives.
    assert "human" in out["negative_prompt"]
    assert NEG.split(",")[0] in out["negative_prompt"]


def test_character_prompt_object_mode_non_food(it):
    out = it.build_character_image_prompt(
        _profile("Ballard", short="A quirky, laid-back maritime enclave"),
        category="Seattle neighborhood", analysis={},
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "object or place itself" in out["prompt"]
    assert "human" in out["negative_prompt"]


def test_character_prompt_person_mode_unchanged(it):
    out = it.build_character_image_prompt(
        _profile("Hermione Granger",
                 short="Brilliant young witch with bushy brown hair"),
        category="Harry Potter character", analysis={"is_media": True},
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert out["prompt"].startswith("Portrait of Hermione Granger")
    assert STYLE in out["prompt"]
    assert out["negative_prompt"] == NEG             # untouched for persons


# ---------------------------------------------------------------------------
# build_branded_attempt_prompt / build_descriptive_attempt_prompt
# ---------------------------------------------------------------------------

def test_branded_attempt_object_mode(it):
    out = it.build_branded_attempt_prompt(
        name="Frappuccino", source="Coffee order",
        style_suffix=STYLE, negative_prompt=NEG,
    )
    p = out["prompt"]
    assert "Frappuccino from Coffee order" in p
    assert "character portrait" not in p
    assert "not a person" in p
    assert "human" in out["negative_prompt"]


def test_branded_attempt_person_mode_unchanged(it):
    out = it.build_branded_attempt_prompt(
        name="Hermione Granger", source="Harry Potter character quiz",
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "illustrated character portrait" in out["prompt"]
    assert out["negative_prompt"] == NEG


def test_branded_attempt_explicit_kind_overrides_heuristic(it):
    out = it.build_branded_attempt_prompt(
        name="Mystery Thing", source="Mystery Topic",
        style_suffix=STYLE, negative_prompt=NEG,
        subject_kind=it.SUBJECT_KIND_OBJECT,
    )
    assert "not a person" in out["prompt"]


def test_descriptive_attempt_object_mode(it):
    out = it.build_descriptive_attempt_prompt(
        description="A crusty baguette stuffed with pickled vegetables and herbs",
        style_suffix=STYLE, negative_prompt=NEG,
        subject_kind=it.SUBJECT_KIND_OBJECT,
    )
    assert out["prompt"].startswith("Illustrated depiction of the item itself")
    assert "portrait of a person" not in out["prompt"].lower()
    assert "human" in out["negative_prompt"]


def test_descriptive_attempt_person_default_unchanged(it):
    out = it.build_descriptive_attempt_prompt(
        description="A bushy-haired young woman with a determined expression",
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert out["prompt"].startswith("Illustrated character portrait of a person:")
    assert out["negative_prompt"] == NEG


def test_descriptive_attempt_explicit_prefix_still_wins(it):
    out = it.build_descriptive_attempt_prompt(
        description="whatever",
        style_suffix=STYLE, negative_prompt=NEG,
        prefix="Custom prefix:", subject_kind=it.SUBJECT_KIND_OBJECT,
    )
    assert out["prompt"].startswith("Custom prefix:")


# ---------------------------------------------------------------------------
# build_result_image_prompt — the big visible hero
# ---------------------------------------------------------------------------

def test_result_prompt_object_mode_no_face_tokens(it):
    from app.models.api import FinalResult
    r = FinalResult(
        title="You are Banh Mi!",
        description="Crunchy, layered, and full of surprises.",
        image_url=None,
    )
    charset = [{"name": "Banh Mi"}, {"name": "Club Sandwich"}]
    out = it.build_result_image_prompt(
        r, category="Sandwich", character_set=charset,
        style_suffix=STYLE, negative_prompt=NEG,
    )
    p = out["prompt"]
    assert "Banh Mi" in p
    assert "head-and-shoulders" not in p
    assert "face" not in p.lower()                   # no face-quality tokens
    assert "not a person" in p
    assert "human" in out["negative_prompt"]


def test_result_prompt_person_mode_keeps_face_tokens(it):
    from app.models.api import FinalResult
    r = FinalResult(
        title="You are Hermione Granger!",
        description="Clever and loyal.",
        image_url=None,
    )
    charset = [{"name": "Hermione Granger"}]
    out = it.build_result_image_prompt(
        r, category="Harry Potter character", character_set=charset,
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "head-and-shoulders portrait" in out["prompt"]
    assert "deformed face" in out["negative_prompt"]


def test_result_prompt_object_mode_unmatched_title(it):
    from app.models.api import FinalResult
    r = FinalResult(
        title="You got: The Flat White",
        description="Smooth and understated.",
        image_url=None,
    )
    out = it.build_result_image_prompt(
        r, category="Coffee order", character_set=[{"name": "Espresso"}],
        style_suffix=STYLE, negative_prompt=NEG,
    )
    assert "not a person" in out["prompt"]
    assert "human" in out["negative_prompt"]


# ---------------------------------------------------------------------------
# character_describer prompt text (no LLM call — pure prompt builders)
# ---------------------------------------------------------------------------

def test_describer_object_prompts_describe_the_item():
    from app.services.character_describer import (
        _DESCRIBE_OBJECT_SYSTEM,
        _describe_user_prompt,
    )
    assert "not a person" in _DESCRIBE_OBJECT_SYSTEM.lower() or \
        "never describe" in _DESCRIBE_OBJECT_SYSTEM.lower()
    p0 = _describe_user_prompt(
        name="Banh Mi", source="Sandwich", strict_level=0, subject_kind="object"
    )
    assert "ITEM ITSELF" in p0
    assert "never a person" in p0
    p1 = _describe_user_prompt(
        name="Banh Mi", source="Sandwich", strict_level=1, subject_kind="object"
    )
    assert "proper nouns" in p1
    assert "never a person" in p1


def test_describer_person_prompts_unchanged():
    from app.services.character_describer import _describe_user_prompt
    p0 = _describe_user_prompt(
        name="Hermione Granger", source="Harry Potter", strict_level=0
    )
    assert "physical characteristics" in p0
    assert "ITEM ITSELF" not in p0
