import pytest

# Module under test
from app.agent import canonical_sets as cs

# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_caches():
    """Ensure LRU caches are cleared before/after every test to prevent state leak."""
    cs._compiled_config.cache_clear()
    cs._norm_key.cache_clear()
    cs._norm_key_light.cache_clear()
    yield
    cs._compiled_config.cache_clear()
    cs._norm_key.cache_clear()
    cs._norm_key_light.cache_clear()

@pytest.fixture
def mock_config_data(monkeypatch):
    """Injects a known configuration state for testing lookups."""
    test_data = {
        "sets": {
            "Hogwarts Houses": {
                "names": ["Gryffindor", "Slytherin", "Ravenclaw", "Hufflepuff"],
                "count_hint": 4
            },
            "MBTI Types": {
                "names": ["INTJ", "ENFP", "ISTJ"], # simplified
                "count_hint": 16
            },
            "Fruits": {
                "names": ["Apple", "Banana"],
                # no count_hint
            }
        },
        "aliases": {
            "Hogwarts Houses": ["Harry Potter House", "HP Houses"],
            "MBTI Types": ["Myers Briggs", "Jungian Archetypes"]
        }
    }

    # Bypass file loading and just return this dict
    monkeypatch.setattr(cs, "_from_yaml_blob", lambda: test_data)
    monkeypatch.setattr(cs, "_from_settings_object", lambda: {})
    return test_data

# ---------------------------------------------------------------------
# Linguistic & Normalization Tests
# ---------------------------------------------------------------------

def test_strip_accents():
    assert cs._strip_accents("Pokémon") == "Pokemon"
    assert cs._strip_accents("Caffè") == "Caffe"
    assert cs._strip_accents(None) is None

def test_singular():
    assert cs._singular("cars") == "car"
    assert cs._singular("buses") == "bus" # ends with s but not ss
    assert cs._singular("berries") == "berry" # ies -> y
    assert cs._singular("boxes") == "box" # xes -> x
    assert cs._singular("grass") is None # ss -> None (preserve)
    assert cs._singular("car") is None # no change

def test_plural():
    assert cs._plural("car") == "cars"
    assert cs._plural("berry") == "berries"
    assert cs._plural("box") == "boxes"
    assert cs._plural("cars") is None # already pluralish
    assert cs._plural("grass") is None # ends in s
    assert cs._plural("bus") is None # ends in s

def test_strip_noise():
    # Prefixes
    assert cs._strip_noise("Quiz: Hogwarts Houses") == "Hogwarts Houses"
    assert cs._strip_noise("The Types of MBTI") == "MBTI"
    assert cs._strip_noise("Please list the Fruits") == "Fruits"
    assert cs._strip_noise("Can you give me the Hogwarts Houses") == "Hogwarts Houses"

    # Suffixes
    assert cs._strip_noise("Hogwarts House Characters") == "Hogwarts House"
    assert cs._strip_noise("MBTI Profiles") == "MBTI"

    # Note: "Picker" is in _TRAILING_TOOL_RE, "Tool" is not.
    assert cs._strip_noise("Fruit Picker") == "Fruit"

    # Punctuation/formatting
    assert cs._strip_noise("Hogwarts Houses?") == "Hogwarts Houses"
    assert cs._strip_noise("Hogwarts Houses (Official)") == "Hogwarts Houses"

def test_norm_key():
    # Combines strip accents, strip noise, and tokenization
    # "Characters" suffix is stripped if at the end
    assert cs._norm_key("The Pokémon Characters") == "pokemon"
    assert cs._norm_key("  Types of CHEESE  ") == "cheese"

# ---------------------------------------------------------------------
# Variant Generation Tests
# ---------------------------------------------------------------------

def test_last_token_variants():
    # Use words that the simple singularizer handles correctly
    tokens = ["mbti", "types"]
    variants = list(cs._last_token_variants(tokens))

    assert "mbti types" in variants # base
    assert "mbti type" in variants # singularized last token

    tokens2 = ["apple", "trees"]
    variants2 = list(cs._last_token_variants(tokens2))
    assert "apple trees" in variants2
    assert "apple tree" in variants2

# ---------------------------------------------------------------------
# Config Loading Tests
# ---------------------------------------------------------------------

def test_merge_config():
    a = {"x": 1, "y": 2}
    b = {"y": 3, "z": 4}
    merged = cs._merge_config(a, b)
    assert merged == {"x": 1, "y": 3, "z": 4}

def test_build_sets_map():
    raw = {
        "Set A": {"names": ["a", "b"], "count_hint": "10"},
        "Set B": ["x", "y"], # raw list format
    }
    processed = cs._build_sets_map(raw)

    assert processed["Set A"]["names"] == ["a", "b"]
    assert processed["Set A"]["count_hint"] == 10

    assert processed["Set B"]["names"] == ["x", "y"]
    assert processed["Set B"]["count_hint"] is None


def test_build_sets_map_carries_min_items_and_rigor():
    raw = {
        "Rigorous": {"names": ["a"], "min_items": "22", "rigor": True},
        "Bad": {"names": ["b"], "min_items": "nope"},
        "Zero": {"names": ["c"], "min_items": 0},
    }
    processed = cs._build_sets_map(raw)
    assert processed["Rigorous"]["min_items"] == 22
    assert processed["Rigorous"]["rigor"] is True
    # Non-numeric / non-positive min_items is dropped.
    assert "min_items" not in processed["Bad"]
    assert "min_items" not in processed["Zero"]


def test_extract_min_items_variants():
    assert cs._extract_min_items({"min_items": 18}) == 18
    assert cs._extract_min_items({"min_items": "20"}) == 20
    assert cs._extract_min_items({"min_items": 0}) is None
    assert cs._extract_min_items({"min_items": "x"}) is None
    assert cs._extract_min_items({}) is None
    assert cs._extract_min_items(["a", "b"]) is None  # list-shaped entry

# ---------------------------------------------------------------------
# Lookup / Public API Tests
# ---------------------------------------------------------------------

def test_canonical_for_exact_match(mock_config_data):
    res = cs.canonical_for("Hogwarts Houses")
    assert res is not None
    assert "Gryffindor" in res
    assert len(res) == 4

def test_canonical_for_alias_match(mock_config_data):
    # "HP Houses" is an alias in mock_config_data
    res = cs.canonical_for("HP Houses")
    assert res is not None
    assert "Slytherin" in res

def test_canonical_for_fuzzy_alias_match(mock_config_data):
    # Alias is "Harry Potter House", user asks for "Harry Potter Houses" (plural)
    res = cs.canonical_for("Harry Potter Houses")
    assert res is not None
    assert "Gryffindor" in res

def test_canonical_for_noise_stripped_match(mock_config_data):
    # "Quiz: The MBTI Types?" -> "mbti types"
    res = cs.canonical_for("Quiz: The MBTI Types?")
    assert res is not None
    assert "INTJ" in res

def test_canonical_for_miss(mock_config_data):
    assert cs.canonical_for("Unknown Set") is None
    assert cs.canonical_for("") is None

def test_count_hint_for(mock_config_data):
    # Explicit hint
    assert cs.count_hint_for("Hogwarts Houses") == 4
    assert cs.count_hint_for("MBTI Types") == 16

    # Derived from length (Fruits has 2 items, no explicit hint)
    assert cs.count_hint_for("Fruits") == 2

    # Miss
    assert cs.count_hint_for("Cars") is None


def test_min_items_for_overlay(monkeypatch):
    """min_items_for reads a per-instrument floor from App-Config sets."""
    test_data = {
        "sets": {
            "DISC Profiles": {
                "names": ["D", "I", "S", "C"],
                "min_items": 22,
                "rigor": True,
            },
            "Fruits": {"names": ["Apple", "Banana"]},  # no min_items
        },
        "aliases": {"DISC Profiles": ["disc"]},
    }
    monkeypatch.setattr(cs, "_from_yaml_blob", lambda: test_data)
    monkeypatch.setattr(cs, "_from_settings_object", lambda: {})

    assert cs.min_items_for("disc") == 22
    assert cs.is_rigorous("disc") is True
    # A canonical set without min_items returns None (casual).
    assert cs.min_items_for("Fruits") is None
    assert cs.is_rigorous("Fruits") is False
    # Non-canonical topic -> None / False.
    assert cs.min_items_for("Cats") is None
    assert cs.is_rigorous("Cats") is False
    assert cs.min_items_for(None) is None


def test_min_items_for_real_catalog():
    """Built-in catalog rigorous instruments expose a per-instrument floor."""
    # DISC Styles is catalog-only (no YAML override) -> built-in min_items.
    assert cs.min_items_for("DISC") == 22
    assert cs.is_rigorous("DISC") is True
    # Hogwarts Houses is canonical but not rigorous -> no floor.
    assert cs.min_items_for("Hogwarts Houses") is None
    assert cs.is_rigorous("Hogwarts Houses") is False

def test_config_index_building_robustness(monkeypatch):
    """Test complicated index building edge cases."""
    raw = {
        "sets": {
            "Cats": {"names": ["Persian"]}
        },
        "aliases": {
            "Cats": ["Kittens"],
            "NonExistentSet": ["Ghost"] # Should be ignored safely
        }
    }
    monkeypatch.setattr(cs, "_from_yaml_blob", lambda: raw)
    monkeypatch.setattr(cs, "_from_settings_object", lambda: {})

    cfg = cs._compiled_config()
    index = cfg["index"]

    # Verify direct set indexing
    assert index.get("cats") == "Cats"
    assert index.get("cat") == "Cats" # singular variant

    # Verify alias indexing
    assert index.get("kittens") == "Cats"
    assert index.get("kitten") == "Cats" # singular variant of alias

    # Verify orphan alias ignored
    assert "ghost" not in index

def test_circular_or_broken_input_safety():
    """Ensure weird inputs to normalization don't crash."""
    assert cs._norm_key(None) == ""
    assert cs._norm_key(123) == ""
    assert cs.canonical_for(None) is None


def test_real_catalog_has_high_volume_coverage():
    cfg = cs._compiled_config()
    total_sets = len(cfg["sets"])
    total_names = sum(len(entry["names"]) for entry in cfg["sets"].values())

    assert total_sets >= 200
    assert total_names >= 2000


@pytest.mark.parametrize(
    ("query", "expected_name", "expected_count"),
    [
        ("Pokemon Types", "Fire", 18),
        ("Ilvermorny Houses", "Horned Serpent", 4),
        ("Avatar Nations", "Water Tribes", 4),
        ("US States", "California", 50),
        ("NBA Teams", "Boston Celtics", 30),
    ],
)
def test_real_catalog_supports_new_bounded_taxonomies(query, expected_name, expected_count):
    res = cs.canonical_for(query)
    assert res is not None
    assert expected_name in res
    assert len(res) == expected_count


# ---------------------------------------------------------------------
# Issue 2: OCEAN alias-vs-title collision (Big Five vs geographic Oceans)
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("query", "expect_big_five"),
    [
        ("OCEAN", True),    # uppercase acronym -> Big Five
        ("ocean", False),   # lowercase body-of-water -> geographic
        ("Ocean", False),   # title-case body-of-water -> geographic
        ("oceans", False),  # plural geographic title
        ("Oceans", False),
        ("OCEANS", False),  # uppercase but NOT the bare acronym -> geographic
    ],
)
def test_ocean_casing_routes_correctly(query, expect_big_five):
    """REGRESSION (MED #1): the acronym override is CASE-SENSITIVE.

    Only the uppercase acronym 'OCEAN' resolves to Big Five; every cased form of
    the word 'ocean(s)' keeps resolving to the geographic Oceans set.
    """
    res = cs.canonical_for(query)
    assert res is not None, query
    if expect_big_five:
        assert res == [
            "Openness",
            "Conscientiousness",
            "Extraversion",
            "Agreeableness",
            "Neuroticism",
        ], query
    else:
        assert "Atlantic" in res and "Pacific" in res, query
        assert "Openness" not in res, query


def test_big_five_word_aliases_resolve_regardless_of_case():
    # Non-colliding aliases work in any case (no geographic clash to guard).
    for q in ("ocean traits", "big five", "big 5", "ffm", "FFM", "Big Five"):
        res = cs.canonical_for(q)
        assert res is not None, q
        assert "Openness" in res, q


def test_noncolliding_acronyms_resolve_lowercase():
    """REGRESSION: diverting acronyms must not drop common lowercase acronyms.

    RIASEC / VARK have no clashing set, so both their lowercase and uppercase
    forms must resolve (they are NOT diverted out of the case-blind index).
    """
    for q in ("riasec", "RIASEC", "Riasec"):
        assert cs.canonical_for(q) is not None and "Realistic" in cs.canonical_for(q), q
    for q in ("vark", "VARK"):
        assert cs.canonical_for(q) is not None and "Visual" in cs.canonical_for(q), q


def test_acronym_key_for_detection():
    big_five = ["Openness", "Conscientiousness", "Extraversion", "Agreeableness", "Neuroticism"]
    assert cs._acronym_key_for("ocean", big_five) == "OCEAN"
    # Wrong initials / not an acronym of the set.
    assert cs._acronym_key_for("ffm", big_five) is None
    # Multi-token / contains digits / too long are never acronyms.
    assert cs._acronym_key_for("big 5", big_five) is None
    assert cs._acronym_key_for("openness", big_five) is None
    assert cs._acronym_key_for("", big_five) is None


@pytest.mark.parametrize(
    ("query", "expected_head"),
    [
        # REGRESSION (MED #3): an alias-derived variant must NOT outrank another
        # set's title-derived variant. Each of these is pinned to its pre-PR
        # (main) result. "classical element" is the load-bearing case: the
        # branch previously re-routed it to the 5-element (Aether) set.
        ("classical element", ["Fire", "Water", "Air", "Earth"]),
        ("classical elements", ["Fire", "Water", "Air", "Earth"]),
        ("church mode", ["Ionian", "Dorian", "Phrygian"]),
        ("solar system planet", ["Mercury", "Venus", "Earth"]),
        ("roman numeral symbol", ["I", "V", "X"]),
    ],
)
def test_derived_variant_does_not_reroute_across_sets(query, expected_head):
    res = cs.canonical_for(query)
    assert res is not None, query
    assert res[: len(expected_head)] == expected_head, (query, res)


def test_aether_is_only_the_five_element_set():
    """The 4-element default must not be polluted by the 5-element 'Aether'."""
    four = cs.canonical_for("classical element")
    assert "Aether" not in four
    five = cs.canonical_for("aether elements")
    assert five is not None and "Aether" in five


@pytest.mark.parametrize(
    "query",
    [
        # REGRESSION (LOW #4): a topic that strips to empty / too-short must not
        # match anything (and must not crash).
        "personality",
        "styles",
        "style",
        "results",
        "personality type",
        "my",
        "the",
        "a",
    ],
)
def test_strip_to_empty_returns_none(query):
    assert cs.canonical_for(query) is None
    assert cs.count_hint_for(query) is None


def test_full_original_first_matches_descriptor_like_titles():
    """A real title whose tail looks like a descriptor still resolves."""
    assert cs.canonical_for("Attachment Styles") is not None
    assert "Secure" in cs.canonical_for("attachment styles")
    assert cs.canonical_for("DISC Styles") == [
        "Dominance",
        "Influence",
        "Steadiness",
        "Conscientiousness",
    ]


def test_explicit_alias_overrides_derived_title_variant(monkeypatch):
    """An explicit alias reclaims a key a *derived* plural/singular variant grabbed."""
    raw = {
        "sets": {
            # "Oxen" derives the singular variant "ox".
            "Oxen": {"names": ["An Ox", "Another Ox"]},
            "Operating Systems": {"names": ["Linux", "Windows", "macOS"]},
        },
        "aliases": {
            # Explicit alias "ox" should beat the derived "Oxen" -> "ox" variant.
            "Operating Systems": ["ox"],
        },
    }
    monkeypatch.setattr(cs, "_from_yaml_blob", lambda: raw)
    monkeypatch.setattr(cs, "_from_settings_object", lambda: {})
    # Use only this fixture's sets (not the builtin catalog) for a clean assertion.
    monkeypatch.setattr(cs, "BUILTIN_CANONICAL_SETS", {"sets": {}, "aliases": {}})
    cs._compiled_config.cache_clear()

    assert cs.canonical_for("ox") == ["Linux", "Windows", "macOS"]
    # The exact title "Oxen" is still its own set.
    assert cs.canonical_for("oxen") == ["An Ox", "Another Ox"]


# ---------------------------------------------------------------------
# Issue 1: marquee frameworks live in the CODE catalog (drift-proof)
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("query", "expected_member", "expected_count"),
    [
        ("MBTI", "INTJ", 16),
        ("myers briggs", "ENFP", 16),
        ("16 personalities", "ISTJ", 16),
        ("enneagram", "Type 1 The Reformer", 9),
        ("big five", "Openness", 5),
        ("hogwarts house", "Gryffindor", 4),
        ("which hogwarts house", "Hufflepuff", 4),
        ("DISC", "Dominance", 4),
        ("alignment grid", "Lawful Good", 9),
    ],
)
def test_marquee_frameworks_resolve_from_real_catalog(query, expected_member, expected_count):
    res = cs.canonical_for(query)
    assert res is not None, query
    assert expected_member in res, query
    assert len(res) == expected_count, query


# ---------------------------------------------------------------------
# Issue 3: broadened noise stripping for real phrasings
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DISC personality", "DISC"),
        ("DISC personality type", "DISC"),
        ("What is my DISC type", "DISC"),
        ("Big Five personality", "Big Five"),
        ("my love language", "love language"),
        ("your love languages", "love languages"),
        ("which hogwarts house am I", "hogwarts house"),
        ("MBTI results", "MBTI"),
        ("conflict style", "conflict"),
    ],
)
def test_strip_noise_handles_real_phrasings(raw, expected):
    assert cs._strip_noise(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected_member"),
    [
        ("DISC personality", "Dominance"),
        ("What is my DISC type", "Dominance"),
        ("Big Five personality", "Openness"),
        ("my love language", "Words of Affirmation"),
        ("which hogwarts house am I", "Gryffindor"),
    ],
)
def test_canonical_for_handles_real_phrasings(raw, expected_member):
    res = cs.canonical_for(raw)
    assert res is not None, raw
    assert expected_member in res, raw
